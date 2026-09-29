from aiogram import Router

from handlers import admin, user


def get_routers() -> list[Router]:
    # Порядок важливий: адмінський роутер перевіряється першим.
    return [admin.router, user.router]
