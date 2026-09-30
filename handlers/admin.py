import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

import texts
from config import Config
from db import STATUS_PENDING_REVIEW, STATUS_PUBLISHED, STATUS_SOLD, Database
from filters import IsAdmin
from keyboards import ModerationCb, cancel_kb, main_kb, pending_list_kb, sold_confirm_kb, sold_kb
from services import (
    ad_lock,
    admin_mention,
    build_post_link,
    publish_to_channel,
    send_ad_to_admin,
    update_moderation_messages,
)
from states import AdminStates
from utils import format_price, user_display

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(F.chat.type == ChatType.PRIVATE, IsAdmin())
router.callback_query.filter(IsAdmin())

PENDING_LIST_LIMIT = 30
REJECT_REASON_MAX_LEN = 500


async def _drop_markup(callback: CallbackQuery, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=reply_markup)
    except (TelegramBadRequest, AttributeError):
        pass


# ---------------------------------------------------------------- /pending

@router.message(Command("pending"))
async def cmd_pending(message: Message, db: Database) -> None:
    ads = await db.list_pending()
    if not ads:
        await message.answer(texts.ADMIN_NO_PENDING)
        return

    shown = ads[:PENDING_LIST_LIMIT]
    text = texts.ADMIN_PENDING_HEADER.format(count=len(ads))
    for ad in shown:
        title = ad.title if len(ad.title) <= 60 else ad.title[:59] + "…"
        text += texts.ADMIN_PENDING_ITEM.format(
            ad_id=ad.id,
            title=escape(title),
            price=format_price(ad.price),
            author=user_display(ad.username, ad.full_name),
            created_at=escape(ad.created_at),
        )
    if len(ads) > len(shown):
        text += texts.ADMIN_PENDING_MORE.format(count=len(ads) - len(shown))

    await message.answer(text, reply_markup=pending_list_kb([ad.id for ad in shown]))


@router.callback_query(ModerationCb.filter(F.action == "show"))
async def cb_show(callback: CallbackQuery, callback_data: ModerationCb, bot: Bot, db: Database, config: Config) -> None:
    ad = await db.get_ad(callback_data.ad_id)
    if ad is None:
        await callback.answer(texts.ADMIN_AD_NOT_FOUND, show_alert=True)
        return
    await callback.answer()
    try:
        await send_ad_to_admin(bot, db, config, callback.from_user.id, ad)
    except TelegramAPIError as error:
        logger.warning("Не вдалося показати оголошення №%s: %s", ad.id, error)
        await bot.send_message(callback.from_user.id, f"⚠️ <code>{escape(str(error))}</code>")


# ---------------------------------------------------------------- публікація

@router.callback_query(ModerationCb.filter(F.action == "publish"))
async def cb_publish(callback: CallbackQuery, callback_data: ModerationCb, bot: Bot, db: Database, config: Config) -> None:
    ad_id = callback_data.ad_id

    async with ad_lock(ad_id):
        ad = await db.get_ad(ad_id)
        if ad is None:
            await callback.answer(texts.ADMIN_AD_NOT_FOUND, show_alert=True)
            return
        if ad.status != STATUS_PENDING_REVIEW:
            status = texts.STATUS_NAMES.get(ad.status, ad.status)
            await callback.answer(texts.ADMIN_ALREADY_PROCESSED.format(status=status), show_alert=True)
            await _drop_markup(callback)
            return

        await callback.answer(texts.ADMIN_PUBLISHING)
        try:
            channel_message_id = await publish_to_channel(bot, config, ad)
        except TelegramAPIError as error:
            logger.error("Помилка публікації оголошення №%s: %s", ad_id, error)
            await bot.send_message(
                callback.from_user.id,
                texts.ADMIN_PUBLISH_ERROR.format(ad_id=ad_id, error=escape(str(error))),
            )
            return

        await db.mark_published(ad_id, channel_message_id)
        ad = await db.get_ad(ad_id)

    logger.info("Оголошення №%s опубліковано адміном %s", ad_id, callback.from_user.id)

    link = await build_post_link(bot, config.channel_id, channel_message_id)
    author_text = texts.AD_PUBLISHED
    if link:
        author_text += texts.AD_PUBLISHED_LINK.format(link=escape(link, quote=True))
    author_text += texts.AD_PUBLISHED_SOLD_HINT
    try:
        await bot.send_message(ad.user_id, author_text, reply_markup=sold_kb(ad_id, texts.BTN_BOUQUET_SOLD))
    except TelegramAPIError as error:
        logger.warning("Не вдалося сповістити автора оголошення №%s: %s", ad_id, error)
        await bot.send_message(callback.from_user.id, texts.ADMIN_NOTIFY_USER_FAILED)

    await update_moderation_messages(
        bot,
        db,
        ad,
        texts.ADMIN_STATUS_PUBLISHED.format(admin=admin_mention(callback.from_user)),
        reply_markup=sold_kb(ad_id),
    )
    await _drop_markup(callback, sold_kb(ad_id))


# ---------------------------------------------------------------- /sold <id>
# Підтвердження («Так»/«Ні») обробляє спільний хендлер у handlers/user.py.

@router.message(Command("sold"))
async def cmd_sold(message: Message, command: CommandObject, db: Database) -> None:
    raw = (command.args or "").strip().lstrip("№#")
    if not raw.isdigit():
        await message.answer(texts.ADMIN_SOLD_USAGE)
        return
    ad = await db.get_ad(int(raw))
    if ad is None:
        await message.answer(texts.ADMIN_AD_NOT_FOUND)
        return
    if ad.status == STATUS_SOLD:
        await message.answer(texts.SOLD_ALREADY)
        return
    if ad.status != STATUS_PUBLISHED:
        status = texts.STATUS_NAMES.get(ad.status, ad.status)
        await message.answer(texts.SOLD_NOT_PUBLISHED.format(status=status))
        return
    await message.answer(
        f"<b>№{ad.id}</b> " + texts.SOLD_CONFIRM.format(title=escape(ad.title)),
        reply_markup=sold_confirm_kb(ad.id),
    )


# ---------------------------------------------------------------- відхилення

@router.callback_query(ModerationCb.filter(F.action == "reject"))
async def cb_reject(
    callback: CallbackQuery, callback_data: ModerationCb, state: FSMContext, bot: Bot, db: Database
) -> None:
    ad = await db.get_ad(callback_data.ad_id)
    if ad is None:
        await callback.answer(texts.ADMIN_AD_NOT_FOUND, show_alert=True)
        return
    if ad.status != STATUS_PENDING_REVIEW:
        status = texts.STATUS_NAMES.get(ad.status, ad.status)
        await callback.answer(texts.ADMIN_ALREADY_PROCESSED.format(status=status), show_alert=True)
        await _drop_markup(callback)
        return

    await state.clear()
    await state.set_state(AdminStates.reject_reason)
    await state.update_data(reject_ad_id=ad.id)
    await callback.answer()
    await bot.send_message(
        callback.from_user.id,
        texts.ADMIN_REJECT_ASK.format(ad_id=ad.id),
        reply_markup=cancel_kb(),
    )


@router.message(
    AdminStates.reject_reason,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_({texts.BTN_CANCEL, texts.BTN_NEW_AD, texts.BTN_MY_ADS}),
)
async def reject_reason(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    ad_id = (await state.get_data()).get("reject_ad_id")
    await state.clear()

    text = message.text.strip()
    reason = None if text in {"-", "—", "–"} else text[:REJECT_REASON_MAX_LEN]

    async with ad_lock(ad_id):
        ad = await db.get_ad(ad_id) if ad_id else None
        if ad is None:
            await message.answer(texts.ADMIN_AD_NOT_FOUND, reply_markup=main_kb())
            return
        if ad.status != STATUS_PENDING_REVIEW or not await db.mark_rejected(ad.id, reason):
            status = texts.STATUS_NAMES.get(ad.status, ad.status)
            await message.answer(texts.ADMIN_ALREADY_PROCESSED.format(status=status), reply_markup=main_kb())
            return
        ad = await db.get_ad(ad.id)

    logger.info("Оголошення №%s відхилено адміном %s", ad.id, message.from_user.id)

    author_text = texts.AD_REJECTED
    if reason:
        author_text += texts.AD_REJECTED_REASON.format(reason=escape(reason))
    try:
        await bot.send_message(ad.user_id, author_text, reply_markup=main_kb())
    except TelegramAPIError as error:
        logger.warning("Не вдалося сповістити автора оголошення №%s: %s", ad.id, error)
        await message.answer(texts.ADMIN_NOTIFY_USER_FAILED)

    status_text = texts.ADMIN_STATUS_REJECTED.format(admin=admin_mention(message.from_user))
    if reason:
        status_text += texts.ADMIN_STATUS_REASON.format(reason=escape(reason))
    await update_moderation_messages(bot, db, ad, status_text)

    await message.answer(texts.ADMIN_REJECT_OK.format(ad_id=ad.id), reply_markup=main_kb())


@router.message(AdminStates.reject_reason, ~F.text)
async def reject_reason_invalid(message: Message) -> None:
    await message.answer(texts.ADMIN_REJECT_ASK_TEXT)
