"""Спільна логіка: надсилання оголошень, модерація, публікація в канал."""
import asyncio
import logging
from collections import defaultdict
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InputMediaPhoto, Message

import texts
from config import Config
from db import STATUS_PENDING_REVIEW, Ad, Database
from keyboards import moderation_kb
from utils import build_caption, user_display

logger = logging.getLogger(__name__)

# Блокування на рівні оголошення — захист від одночасної обробки кількома адмінами.
_ad_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_channel_username: dict[int | str, str | None] = {}


def ad_lock(ad_id: int) -> asyncio.Lock:
    return _ad_locks[ad_id]


async def send_ad_media(bot: Bot, chat_id: int | str, ad: Ad, config: Config) -> list[Message]:
    """Надсилає оголошення: одне фото — send_photo, кілька — альбом з підписом на першому фото."""
    caption = build_caption(ad, config.channel_invite_link, config.bot_link)
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
        await bot.send_message(admin_id, admin_ad_info(ad) + admin_status_line(ad))


async def notify_admins(bot: Bot, db: Database, config: Config, ad: Ad) -> None:
    for admin_id in config.admin_ids:
        try:
            await send_ad_to_admin(bot, db, config, admin_id, ad)
        except TelegramAPIError as error:
            logger.warning("Не вдалося надіслати оголошення №%s адміну %s: %s", ad.id, admin_id, error)


async def update_moderation_messages(bot: Bot, db: Database, ad: Ad, status_text: str) -> None:
    """Оновлює картки оголошення в усіх адмінів: додає статус і прибирає кнопки."""
    text = texts.ADMIN_NEW_AD + admin_ad_info(ad) + status_text
    for chat_id, message_id in await db.get_moderation_messages(ad.id):
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=None)
        except TelegramBadRequest as error:
            logger.debug("Не вдалося оновити повідомлення модерації %s/%s: %s", chat_id, message_id, error)
        except TelegramAPIError as error:
            logger.warning("Помилка оновлення повідомлення модерації %s/%s: %s", chat_id, message_id, error)


async def publish_to_channel(bot: Bot, config: Config, ad: Ad) -> int:
    """Публікує пост у канал і повертає message_id першого повідомлення."""
    messages = await send_ad_media(bot, config.channel_id, ad, config)
    return messages[0].message_id


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
