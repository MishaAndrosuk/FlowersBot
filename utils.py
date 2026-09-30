import re
from html import escape, unescape

import texts
from db import Ad

CAPTION_LIMIT = 1024

_NICK_RE = re.compile(r"^@[A-Za-z0-9_]{5,32}$")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[\s  ]")
_NUMBER_RE = re.compile(r"\d+")
_TME_PREFIX_RE = re.compile(r"^(?:https?://)?(?:www\.)?t(?:elegram)?\.me/", re.IGNORECASE)

MAX_PRICE = 10_000_000


def parse_price(text: str | None) -> int | None:
    """Витягує ціну з тексту: "2750", "2750 грн", "2 750" -> 2750."""
    if not text:
        return None
    compact = _SPACES_RE.sub("", text)
    match = _NUMBER_RE.search(compact)
    if not match:
        return None
    value = int(match.group())
    if value <= 0 or value > MAX_PRICE:
        return None
    return value


def format_price(value: int) -> str:
    """2990 -> "2 990"."""
    return f"{value:,}".replace(",", " ")


def normalize_nick(text: str | None) -> str | None:
    """Повертає нік у форматі @nick або None, якщо він некоректний."""
    if not text:
        return None
    nick = _TME_PREFIX_RE.sub("", text.strip())
    if not nick.startswith("@"):
        nick = "@" + nick
    return nick if _NICK_RE.match(nick) else None


def tg_length(html_text: str) -> int:
    """Довжина тексту так, як її рахує Telegram: без HTML-тегів, в UTF-16 code units."""
    visible = unescape(_TAG_RE.sub("", html_text))
    return len(visible.encode("utf-16-le")) // 2


def _render_caption(title: str, ad: Ad, invite_link: str, bot_link: str, sold: bool) -> str:
    if sold:
        header = texts.POST_SOLD_HEADER
        price = texts.POST_SOLD_PRICE
        contact = ""
    else:
        header = ""
        price = f"{format_price(ad.price)} грн (коштує {format_price(ad.old_price)} грн)"
        contact = f"👤 КОНТАКТ: {escape(ad.contact)}\n"
    return (
        f"{header}"
        f"<b>{escape(title)}</b>\n\n"
        f"💰 ЦІНА: {price}\n"
        f"⏰ ОТРИМАНО: {escape(ad.received_at)}\n"
        f"📍 АДРЕСА: {escape(ad.address)}\n"
        f"{contact}\n"
        f'📢 <a href="{escape(invite_link, quote=True)}">Підписатися</a> | '
        f'📩 <a href="{escape(bot_link, quote=True)}">Розмістити оголошення</a>'
    )


def build_caption(ad: Ad, invite_link: str, bot_link: str, sold: bool = False) -> str:
    """Формує підпис поста (sold=True — варіант «продано»); якщо він довший за ліміт Telegram — обрізає опис."""
    title = ad.title
    caption = _render_caption(title, ad, invite_link, bot_link, sold)
    while tg_length(caption) > CAPTION_LIMIT and title:
        overflow = tg_length(caption) - CAPTION_LIMIT
        title = title[: max(0, len(title) - overflow - 1)].rstrip()
        caption = _render_caption(f"{title}…" if title else "…", ad, invite_link, bot_link, sold)
    return caption


def user_display(username: str | None, full_name: str | None) -> str:
    """HTML-безпечне відображення користувача: @username або ім'я."""
    if username:
        return f"@{escape(username)}"
    return escape(full_name or "без імені")
