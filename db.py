import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime

import aiosqlite

logger = logging.getLogger(__name__)

STATUS_AWAITING_PAYMENT = "awaiting_payment"
STATUS_PENDING_REVIEW = "pending_review"
STATUS_PUBLISHED = "published"
STATUS_REJECTED = "rejected"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL,
    username           TEXT,
    full_name          TEXT,
    photos             TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    price              INTEGER NOT NULL,
    old_price          INTEGER NOT NULL,
    address            TEXT    NOT NULL,
    received_at        TEXT    NOT NULL,
    contact            TEXT    NOT NULL,
    receipt_file_id    TEXT,
    receipt_type       TEXT,
    status             TEXT    NOT NULL DEFAULT 'awaiting_payment',
    reject_reason      TEXT,
    channel_message_id INTEGER,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ads_status ON ads (status);

CREATE TABLE IF NOT EXISTS moderation_messages (
    ad_id      INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_moderation_ad ON moderation_messages (ad_id);
"""


@dataclass
class Ad:
    id: int
    user_id: int
    username: str | None
    full_name: str | None
    photos: list[str]
    title: str
    price: int
    old_price: int
    address: str
    received_at: str
    contact: str
    receipt_file_id: str | None
    receipt_type: str | None
    status: str
    reject_reason: str | None
    channel_message_id: int | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Ad":
        data = dict(row)
        data["photos"] = json.loads(data["photos"])
        return cls(**data)


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База даних не підключена")
        return self._conn

    async def connect(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info("База даних підключена: %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def create_ad(
        self,
        *,
        user_id: int,
        username: str | None,
        full_name: str | None,
        photos: list[str],
        title: str,
        price: int,
        old_price: int,
        address: str,
        received_at: str,
        contact: str,
    ) -> int:
        now = _now()
        cursor = await self.conn.execute(
            """
            INSERT INTO ads (user_id, username, full_name, photos, title, price, old_price,
                             address, received_at, contact, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                full_name,
                json.dumps(photos),
                title,
                price,
                old_price,
                address,
                received_at,
                contact,
                STATUS_AWAITING_PAYMENT,
                now,
                now,
            ),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def get_ad(self, ad_id: int) -> Ad | None:
        async with self.conn.execute("SELECT * FROM ads WHERE id = ?", (ad_id,)) as cursor:
            row = await cursor.fetchone()
        return Ad.from_row(row) if row else None

    async def list_pending(self) -> list[Ad]:
        async with self.conn.execute(
            "SELECT * FROM ads WHERE status = ? ORDER BY id", (STATUS_PENDING_REVIEW,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [Ad.from_row(row) for row in rows]

    async def set_receipt(self, ad_id: int, file_id: str, receipt_type: str) -> bool:
        cursor = await self.conn.execute(
            """
            UPDATE ads
               SET receipt_file_id = ?, receipt_type = ?, status = ?, updated_at = ?
             WHERE id = ? AND status = ?
            """,
            (file_id, receipt_type, STATUS_PENDING_REVIEW, _now(), ad_id, STATUS_AWAITING_PAYMENT),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def mark_published(self, ad_id: int, channel_message_id: int) -> bool:
        cursor = await self.conn.execute(
            """
            UPDATE ads
               SET status = ?, channel_message_id = ?, updated_at = ?
             WHERE id = ? AND status = ?
            """,
            (STATUS_PUBLISHED, channel_message_id, _now(), ad_id, STATUS_PENDING_REVIEW),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def mark_rejected(self, ad_id: int, reason: str | None) -> bool:
        cursor = await self.conn.execute(
            """
            UPDATE ads
               SET status = ?, reject_reason = ?, updated_at = ?
             WHERE id = ? AND status = ?
            """,
            (STATUS_REJECTED, reason, _now(), ad_id, STATUS_PENDING_REVIEW),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def add_moderation_message(self, ad_id: int, chat_id: int, message_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO moderation_messages (ad_id, chat_id, message_id) VALUES (?, ?, ?)",
            (ad_id, chat_id, message_id),
        )
        await self.conn.commit()

    async def get_moderation_messages(self, ad_id: int) -> list[tuple[int, int]]:
        async with self.conn.execute(
            "SELECT chat_id, message_id FROM moderation_messages WHERE ad_id = ?", (ad_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [(row["chat_id"], row["message_id"]) for row in rows]
