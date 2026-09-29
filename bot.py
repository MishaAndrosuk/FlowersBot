import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from config import ConfigError, load_config
from db import Database
from handlers import get_routers

logger = logging.getLogger(__name__)

USER_COMMANDS = [
    BotCommand(command="start", description="Головне меню"),
    BotCommand(command="new", description="Виставити букет"),
    BotCommand(command="cancel", description="Скасувати заповнення"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="pending", description="Оголошення на перевірці"),
]


async def set_commands(bot: Bot, admin_ids: frozenset[int]) -> None:
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramAPIError as error:
            logger.warning("Не вдалося встановити команди для адміна %s (він має спершу написати боту /start): %s",
                           admin_id, error)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
        stream=sys.stdout,
    )

    try:
        config = load_config()
    except ConfigError as error:
        logger.critical("Помилка конфігурації: %s", error)
        sys.exit(1)

    db = Database(config.db_path)
    await db.connect()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp["config"] = config
    dp.include_routers(*get_routers())

    try:
        me = await bot.get_me()
        logger.info("Бот запущено: @%s", me.username)
        await set_commands(bot, config.admin_ids)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()
        logger.info("Бот зупинено")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
