from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

import texts

PHOTOS_DONE_CB = "photos_done"
USE_USERNAME_CB = "use_username"


class ModerationCb(CallbackData, prefix="mod"):
    action: str  # publish / reject / show
    ad_id: int


def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_NEW_AD)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BTN_CANCEL)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def photos_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_PHOTOS_DONE, callback_data=PHOTOS_DONE_CB)]]
    )


def use_username_kb(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_USE_USERNAME.format(username=username),
                    callback_data=USE_USERNAME_CB,
                )
            ]
        ]
    )


def moderation_kb(ad_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_PUBLISH,
                    callback_data=ModerationCb(action="publish", ad_id=ad_id).pack(),
                ),
                InlineKeyboardButton(
                    text=texts.BTN_REJECT,
                    callback_data=ModerationCb(action="reject", ad_id=ad_id).pack(),
                ),
            ]
        ]
    )


def pending_list_kb(ad_ids: list[int]) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for ad_id in ad_ids:
        row.append(
            InlineKeyboardButton(
                text=texts.BTN_SHOW_AD.format(ad_id=ad_id),
                callback_data=ModerationCb(action="show", ad_id=ad_id).pack(),
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)
