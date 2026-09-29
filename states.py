from aiogram.fsm.state import State, StatesGroup


class AdForm(StatesGroup):
    photos = State()
    title = State()
    price = State()
    old_price = State()
    address = State()
    received_at = State()
    contact = State()
    receipt = State()


class AdminStates(StatesGroup):
    reject_reason = State()
