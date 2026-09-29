import asyncio
import logging
from collections import defaultdict
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

import texts
from config import Config
from db import Database
from keyboards import (
    PHOTOS_DONE_CB,
    USE_USERNAME_CB,
    cancel_kb,
    main_kb,
    photos_done_kb,
    use_username_kb,
)
from services import notify_admins, send_ad_media
from states import AdForm
from utils import format_price, normalize_nick, parse_price

logger = logging.getLogger(__name__)

router = Router(name="user")
router.message.filter(F.chat.type == ChatType.PRIVATE)

MAX_PHOTOS = 3
ALBUM_COLLECT_DELAY = 1.2  # секунд очікування решти фото з альбому
TITLE_MAX_LEN = 200
ADDRESS_MAX_LEN = 200
RECEIVED_MAX_LEN = 100

# Буфер фото з альбомів: (chat_id, media_group_id) -> список file_id
_album_buffer: dict[tuple[int, str], list[str]] = {}
# Блокування на рівні користувача — щоб паралельні апдейти не затирали дані FSM
_user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _remove_markup(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramBadRequest:
        pass


# ---------------------------------------------------------------- меню

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        texts.WELCOME.format(name=escape(message.from_user.first_name or "")),
        reply_markup=main_kb(),
    )


@router.message(StateFilter("*"), Command("cancel"))
@router.message(StateFilter("*"), F.text == texts.BTN_CANCEL)
async def cancel_form(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await _remove_markup(bot, message.chat.id, data.get("photos_msg_id"))
    await _remove_markup(bot, message.chat.id, data.get("contact_msg_id"))
    await state.clear()
    await message.answer(texts.CANCELLED, reply_markup=main_kb())


@router.message(StateFilter("*"), F.text == texts.BTN_NEW_AD)
@router.message(StateFilter("*"), Command("new"))
async def start_form(message: Message, state: FSMContext, bot: Bot) -> None:
    async with _user_locks[message.from_user.id]:
        data = await state.get_data()
        await _remove_markup(bot, message.chat.id, data.get("photos_msg_id"))
        await _remove_markup(bot, message.chat.id, data.get("contact_msg_id"))
        await state.clear()
        await state.set_state(AdForm.photos)
        await state.update_data(photos=[])
    await message.answer(texts.STEP_1_PHOTOS, reply_markup=cancel_kb())


# ---------------------------------------------------------------- крок 1: фото

async def _go_to_title(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(AdForm.title)
    await bot.send_message(chat_id, texts.STEP_2_TITLE, reply_markup=cancel_kb())


async def _process_photos(bot: Bot, chat_id: int, user_id: int, state: FSMContext, file_ids: list[str]) -> None:
    async with _user_locks[user_id]:
        if await state.get_state() != AdForm.photos.state:
            return
        data = await state.get_data()
        photos: list[str] = list(data.get("photos", []))

        free = MAX_PHOTOS - len(photos)
        accepted = file_ids[:free]
        ignored = len(file_ids) - len(accepted)
        photos.extend(accepted)
        await state.update_data(photos=photos)

        # Кнопка "Досить" має бути лише на останньому повідомленні
        await _remove_markup(bot, chat_id, data.get("photos_msg_id"))

        ignored_note = texts.PHOTOS_IGNORED.format(max=MAX_PHOTOS, ignored=ignored) if ignored else ""

        if len(photos) >= MAX_PHOTOS:
            await state.update_data(photos_msg_id=None)
            await bot.send_message(
                chat_id,
                texts.PHOTOS_LIMIT_REACHED.format(count=len(photos), max=MAX_PHOTOS) + ignored_note,
            )
            await _go_to_title(bot, chat_id, state)
            return

        sent = await bot.send_message(
            chat_id,
            texts.PHOTO_UPLOADED.format(count=len(photos), max=MAX_PHOTOS) + ignored_note,
            reply_markup=photos_done_kb(),
        )
        await state.update_data(photos_msg_id=sent.message_id)


@router.message(AdForm.photos, F.photo)
async def form_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    file_id = message.photo[-1].file_id  # найбільший розмір

    if message.media_group_id:
        key = (message.chat.id, message.media_group_id)
        if key in _album_buffer:
            # Перше фото альбому вже чекає на решту — просто додаємо у буфер
            _album_buffer[key].append(file_id)
            return
        _album_buffer[key] = [file_id]
        await asyncio.sleep(ALBUM_COLLECT_DELAY)
        file_ids = _album_buffer.pop(key, [file_id])
    else:
        file_ids = [file_id]

    await _process_photos(bot, message.chat.id, message.from_user.id, state, file_ids)


@router.message(AdForm.photos)
async def form_photo_invalid(message: Message) -> None:
    await message.answer(texts.ASK_PHOTO)


@router.callback_query(F.data == PHOTOS_DONE_CB)
async def photos_done(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    async with _user_locks[callback.from_user.id]:
        if await state.get_state() != AdForm.photos.state:
            await callback.answer(texts.BUTTON_OUTDATED)
            if callback.message:
                await _remove_markup(bot, callback.from_user.id, callback.message.message_id)
            return

        data = await state.get_data()
        if not data.get("photos"):
            await callback.answer(texts.NEED_PHOTO_ALERT, show_alert=True)
            return

        await callback.answer()
        if callback.message:
            await _remove_markup(bot, callback.from_user.id, callback.message.message_id)
        await state.update_data(photos_msg_id=None)
        await _go_to_title(bot, callback.from_user.id, state)


# ---------------------------------------------------------------- крок 2: назва

@router.message(AdForm.title, F.text)
async def form_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not title:
        await message.answer(texts.ASK_TEXT)
        return
    if len(title) > TITLE_MAX_LEN:
        await message.answer(texts.TITLE_TOO_LONG.format(length=len(title), max=TITLE_MAX_LEN))
        return
    await state.update_data(title=title)
    await state.set_state(AdForm.price)
    await message.answer(texts.STEP_3_PRICE)


# ---------------------------------------------------------------- крок 3: ціна

@router.message(AdForm.price, F.text)
async def form_price(message: Message, state: FSMContext) -> None:
    price = parse_price(message.text)
    if price is None:
        await message.answer(texts.PRICE_INVALID)
        return
    await state.update_data(price=price)
    await state.set_state(AdForm.old_price)
    await message.answer(texts.STEP_4_OLD_PRICE)


# ---------------------------------------------------------------- крок 4: стара ціна

@router.message(AdForm.old_price, F.text)
async def form_old_price(message: Message, state: FSMContext) -> None:
    old_price = parse_price(message.text)
    if old_price is None:
        await message.answer(texts.PRICE_INVALID)
        return
    price = (await state.get_data())["price"]
    if old_price <= price:
        await message.answer(texts.OLD_PRICE_NOT_GREATER.format(price=format_price(price)))
        return
    await state.update_data(old_price=old_price)
    await state.set_state(AdForm.address)
    await message.answer(texts.STEP_5_ADDRESS)


# ---------------------------------------------------------------- крок 5: адреса

@router.message(AdForm.address, F.text)
async def form_address(message: Message, state: FSMContext) -> None:
    address = message.text.strip()
    if not address:
        await message.answer(texts.ASK_TEXT)
        return
    if len(address) > ADDRESS_MAX_LEN:
        await message.answer(texts.TEXT_TOO_LONG.format(length=len(address), max=ADDRESS_MAX_LEN))
        return
    await state.update_data(address=address)
    await state.set_state(AdForm.received_at)
    await message.answer(texts.STEP_6_RECEIVED)


# ---------------------------------------------------------------- крок 6: коли отримано

@router.message(AdForm.received_at, F.text)
async def form_received_at(message: Message, state: FSMContext) -> None:
    received_at = message.text.strip()
    if not received_at:
        await message.answer(texts.ASK_TEXT)
        return
    if len(received_at) > RECEIVED_MAX_LEN:
        await message.answer(texts.TEXT_TOO_LONG.format(length=len(received_at), max=RECEIVED_MAX_LEN))
        return
    await state.update_data(received_at=received_at)
    await state.set_state(AdForm.contact)

    username = message.from_user.username
    if username:
        sent = await message.answer(
            texts.STEP_7_CONTACT.format(example=f"@{escape(username)}"),
            reply_markup=use_username_kb(username),
        )
        await state.update_data(contact_msg_id=sent.message_id)
    else:
        await message.answer(texts.STEP_7_CONTACT.format(example="@your_nick") + texts.NO_USERNAME_HINT)


# ---------------------------------------------------------------- крок 7: контакт

async def _finish_form(
    bot: Bot, user: User, state: FSMContext, db: Database, config: Config, contact: str
) -> None:
    """Зберігає оголошення (awaiting_payment), показує превʼю та реквізити. Викликати під локом користувача."""
    data = await state.get_data()
    await _remove_markup(bot, user.id, data.get("contact_msg_id"))
    try:
        ad_id = await db.create_ad(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            photos=data["photos"],
            title=data["title"],
            price=data["price"],
            old_price=data["old_price"],
            address=data["address"],
            received_at=data["received_at"],
            contact=contact,
        )
    except KeyError:
        logger.exception("Неповні дані анкети користувача %s", user.id)
        await state.clear()
        await bot.send_message(user.id, texts.SOMETHING_WRONG, reply_markup=main_kb())
        return

    await state.clear()
    await state.set_state(AdForm.receipt)
    await state.update_data(ad_id=ad_id)
    logger.info("Створено оголошення №%s від користувача %s", ad_id, user.id)

    ad = await db.get_ad(ad_id)
    await bot.send_message(user.id, texts.PREVIEW_HEADER)
    try:
        await send_ad_media(bot, user.id, ad, config)
    except TelegramAPIError as error:
        logger.warning("Не вдалося показати превʼю оголошення №%s: %s", ad_id, error)
    await bot.send_message(user.id, texts.payment_text(config), reply_markup=cancel_kb())


@router.message(AdForm.contact, F.text)
async def form_contact(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    contact = normalize_nick(message.text)
    if contact is None:
        await message.answer(texts.CONTACT_INVALID)
        return
    async with _user_locks[message.from_user.id]:
        if await state.get_state() != AdForm.contact.state:
            return
        await _finish_form(bot, message.from_user, state, db, config, contact)


@router.callback_query(F.data == USE_USERNAME_CB)
async def use_username(callback: CallbackQuery, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    async with _user_locks[callback.from_user.id]:
        if await state.get_state() != AdForm.contact.state:
            await callback.answer(texts.BUTTON_OUTDATED)
            if callback.message:
                await _remove_markup(bot, callback.from_user.id, callback.message.message_id)
            return

        username = callback.from_user.username
        contact = normalize_nick(username) if username else None
        if contact is None:
            await callback.answer(texts.NO_USERNAME_ALERT, show_alert=True)
            return

        await callback.answer()
        await _finish_form(bot, callback.from_user, state, db, config, contact)


# ---------------------------------------------------------------- квитанція

@router.message(AdForm.receipt, F.photo | F.document)
async def form_receipt(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    if message.photo:
        file_id, receipt_type = message.photo[-1].file_id, "photo"
    else:
        mime = (message.document.mime_type or "").lower()
        if mime != "application/pdf" and not mime.startswith("image/"):
            await message.answer(texts.RECEIPT_BAD_DOCUMENT)
            return
        file_id, receipt_type = message.document.file_id, "document"

    async with _user_locks[message.from_user.id]:
        if await state.get_state() != AdForm.receipt.state:
            return
        ad_id = (await state.get_data()).get("ad_id")
        await state.clear()

    if not ad_id or not await db.set_receipt(ad_id, file_id, receipt_type):
        await message.answer(texts.SOMETHING_WRONG, reply_markup=main_kb())
        return

    logger.info("Отримано квитанцію до оголошення №%s", ad_id)
    await message.answer(texts.RECEIPT_ACCEPTED, reply_markup=main_kb())

    ad = await db.get_ad(ad_id)
    await notify_admins(bot, db, config, ad)


@router.message(AdForm.receipt)
async def form_receipt_invalid(message: Message) -> None:
    await message.answer(texts.ASK_RECEIPT)


# ---------------------------------------------------------------- не текст у текстових кроках

@router.message(
    StateFilter(AdForm.title, AdForm.price, AdForm.old_price, AdForm.address, AdForm.received_at, AdForm.contact)
)
async def form_expect_text(message: Message) -> None:
    await message.answer(texts.ASK_TEXT)


# ---------------------------------------------------------------- усе інше

@router.message(StateFilter(None))
async def fallback(message: Message) -> None:
    await message.answer(texts.USE_MENU, reply_markup=main_kb())
