import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    channel_id: int | str
    admin_ids: frozenset[int]
    channel_invite_link: str
    bot_link: str
    price: int
    # Реквізити ФОП — тимчасово вимкнено, оплата лише на картку
    # pay_recipient: str
    # pay_edrpou: str
    # pay_iban: str
    pay_card: str
    pay_purpose: str
    db_path: str


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Змінна {name} не задана у .env")
    return value


def _parse_channel_id(raw: str) -> int | str:
    if raw.lstrip("-").isdigit():
        return int(raw)
    if not raw.startswith("@"):
        raw = "@" + raw
    return raw


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ConfigError(f"ADMIN_IDS містить некоректне значення: {part!r}")
        ids.add(int(part))
    if not ids:
        raise ConfigError("ADMIN_IDS порожній — вкажіть хоча б один id адміна")
    return frozenset(ids)


def load_config() -> Config:
    load_dotenv()
    price_raw = os.getenv("PRICE", "250").strip()
    if not price_raw.isdigit():
        raise ConfigError("PRICE має бути цілим числом")
    return Config(
        bot_token=_require("BOT_TOKEN"),
        channel_id=_parse_channel_id(_require("CHANNEL_ID")),
        admin_ids=_parse_admin_ids(_require("ADMIN_IDS")),
        channel_invite_link=_require("CHANNEL_INVITE_LINK"),
        bot_link=_require("BOT_LINK"),
        price=int(price_raw),
        # pay_recipient=_require("PAY_RECIPIENT"),
        # pay_edrpou=_require("PAY_EDRPOU"),
        # pay_iban=_require("PAY_IBAN"),
        pay_card=_require("PAY_CARD"),
        pay_purpose=_require("PAY_PURPOSE"),
        db_path=os.getenv("DB_PATH", "ads.db").strip() or "ads.db",
    )
