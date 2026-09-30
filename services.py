"""Спільна логіка: надсилання оголошень, модерація, публікація в канал."""
import asyncio
import logging
from collections import defaultdict
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto, Message, User

import texts
from config import Config
from db import STATUS_PENDING_REVIEW, STATUS_PUBLISHED, STATUS_SOLD, Ad, Database
from keyboards import moderation_kb, sold_kb
from utils import build_caption, user_display

logger = logging.getLogger(__name__)

# Блокування на рівні оголошення — захист від одночасної обробки кількома адмінами.
_ad_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_channel_username: dict[int | str, str | None] = {}


def ad_lock(ad_id: int) -> asyncio.Lock:
    return _ad_locks[ad_id]


async def send_ad_media(bot: Bot, chat_id: int | str, ad: Ad, config: Config) -> list[Message]:
    """Надсилає оголошення: одне фото — send_photo, кілька — альбом з підписом на першому фото."""
    caption = build_caption(ad, config.channel_invite_link, config.bot_link, sold=ad.status == STATUS_SOLD)
    if len(ad.photos) == 1:
        message = await bot.send_photo(chat_id, ad.photos[0], caption=caption)
        return [message]
    media = [
        InputMediaPhoto(media=file_id, caption=caption if index == 0 else None)
        for index, file_id in enumerate(ad.photos)
    ]
    return await bot.send_media_group(chat_id, media)


async def send_receipt(bot: Bot, chat_id: int, ad: Ad) -> None:
    if not ad.receipt_file_id:
        return
    caption = texts.ADMIN_RECEIPT_CAPTION.format(ad_id=ad.id)
    if ad.receipt_type == "photo":
        await bot.send_photo(chat_id, ad.receipt_file_id, caption=caption)
    else:
        await bot.send_document(chat_id, ad.receipt_file_id, caption=caption)


def admin_ad_info(ad: Ad) -> str:
    return texts.ADMIN_AD_INFO.format(
        ad_id=ad.id,
        user_id=ad.user_id,
        full_name=escape(ad.full_name or "без імені"),
        username=f"@{escape(ad.username)}" if ad.username else "—",
        contact=escape(ad.contact),
        created_at=escape(ad.created_at),
    )


def admin_status_line(ad: Ad) -> str:
    return texts.ADMIN_STATUS_OTHER.format(status=texts.STATUS_NAMES.get(ad.status, ad.status))


async def send_ad_to_admin(bot: Bot, db: Database, config: Config, admin_id: int, ad: Ad) -> None:
    """Надсилає адміну фото, квитанцію та картку оголошення з кнопками модерації."""
    await send_ad_media(bot, admin_id, ad, config)
    await send_receipt(bot, admin_id, ad)
    if ad.status == STATUS_PENDING_REVIEW:
        info = await bot.send_message(
            admin_id,
            texts.ADMIN_NEW_AD + admin_ad_info(ad),
            reply_markup=moderation_kb(ad.id),
        )
        await db.add_moderation_message(ad.id, admin_id, info.message_id)
    else:
        markup = sold_kb(ad.id) if ad.status == STATUS_PUBLISHED else None
        await bot.send_message(admin_id, admin_ad_info(ad) + admin_status_line(ad), reply_markup=markup)


async def notify_admins(bot: Bot, db: Database, config: Config, ad: Ad) -> None:
    for admin_id in config.admin_ids:
        try:
            await send_ad_to_admin(bot, db, config, admin_id, ad)
        except TelegramAPIError as error:
            logger.warning("Не вдалося надіслати оголошення №%s адміну %s: %s", ad.id, admin_id, error)


async def update_moderation_messages(
    bot: Bot, db: Database, ad: Ad, status_text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Оновлює картки оголошення в усіх адмінів: додає статус і замінює кнопки (за замовчуванням — прибирає)."""
    text = texts.ADMIN_NEW_AD + admin_ad_info(ad) + status_text
    for chat_id, message_id in await db.get_moderation_messages(ad.id):
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        except TelegramBadRequest as error:
            logger.debug("Не вдалося оновити повідомлення модерації %s/%s: %s", chat_id, message_id, error)
        except TelegramAPIError as error:
            logger.warning("Помилка оновлення повідомлення модерації %s/%s: %s", chat_id, message_id, error)


async def publish_to_channel(bot: Bot, config: Config, ad: Ad) -> int:
    """Публікує пост у канал і повертає message_id першого повідомлення."""
    messages = await send_ad_media(bot, config.channel_id, ad, config)
    return messages[0].message_id


# Результати mark_ad_sold
SOLD_OK = "ok"
SOLD_POST_MISSING = "post_missing"  # статус змінено, але пост у каналі не знайдено
SOLD_ALREADY = "already"
SOLD_NOT_FOUND = "not_found"
SOLD_NOT_PUBLISHED = "not_published"
SOLD_EDIT_FAILED = "edit_failed"  # статус НЕ змінено

_POST_MISSING_ERRORS = ("message to edit not found", "message_id_invalid")


def can_mark_sold(user_id: int, ad: Ad, config: Config) -> bool:
    return user_id == ad.user_id or user_id in config.admin_ids


async def mark_ad_sold(bot: Bot, db: Database, config: Config, ad_id: int, actor: User) -> str:
    """Редагує підпис поста в каналі на «продано» і лише після цього змінює статус у БД."""
    async with ad_lock(ad_id):
        ad = await db.get_ad(ad_id)
        if ad is None:
            return SOLD_NOT_FOUND
        if ad.status == STATUS_SOLD:
            return SOLD_ALREADY
        if ad.status != STATUS_PUBLISHED:
            return SOLD_NOT_PUBLISHED

        result = SOLD_OK
        if ad.channel_message_id is None:
            result = SOLD_POST_MISSING
        else:
            caption = build_caption(ad, config.channel_invite_link, config.bot_link, sold=True)
            try:
                await bot.edit_message_caption(
                    chat_id=config.channel_id, message_id=ad.channel_message_id, caption=caption
                )
            except TelegramBadRequest as error:
                message = error.message.lower()
                if "message is not modified" in message:
                    pass
                elif any(marker in message for marker in _POST_MISSING_ERRORS):
                    logger.warning("Пост оголошення №%s не знайдено в каналі: %s", ad_id, error)
                    result = SOLD_POST_MISSING
                else:
                    logger.error("Не вдалося позначити пост оголошення №%s як проданий: %s", ad_id, error)
                    return SOLD_EDIT_FAILED
            except TelegramAPIError as error:
                logger.error("Не вдалося позначити пост оголошення №%s як проданий: %s", ad_id, error)
                return SOLD_EDIT_FAILED

        if not await db.mark_sold(ad_id):
            return SOLD_ALREADY
        ad = await db.get_ad(ad_id)

    logger.info("Оголошення №%s позначено як продане користувачем %s", ad_id, actor.id)
    await update_moderation_messages(bot, db, ad, texts.ADMIN_STATUS_SOLD.format(who=admin_mention(actor)))
    return result


async def build_post_link(bot: Bot, channel_id: int | str, message_id: int) -> str | None:
    if isinstance(channel_id, str) and channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}/{message_id}"

    if channel_id not in _channel_username:
        try:
            chat = await bot.get_chat(channel_id)
            _channel_username[channel_id] = chat.username
        except TelegramAPIError as error:
            logger.warning("Не вдалося отримати інформацію про канал: %s", error)
            _channel_username[channel_id] = None

    username = _channel_username[channel_id]
    if username:
        return f"https://t.me/{username}/{message_id}"

    raw = str(channel_id)
    if raw.startswith("-100"):
        return f"https://t.me/c/{raw[4:]}/{message_id}"
    return None


def admin_mention(user) -> str:
    return user_display(user.username, user.full_name)
