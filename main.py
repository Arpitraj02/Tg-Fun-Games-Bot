"""main.py – Bot entry point."""
from __future__ import annotations
import asyncio
import logging
import datetime
from telegram.ext import Application
from bot.config import BOT_TOKEN, LOG_LEVEL
from bot.database.connection import init_db

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger(__name__)

# ── Plugin imports ────────────────────────────────────────────────────────────
from bot.plugins import (
    start, help, admin, moderation, welcome, filters,
    notes, economy, leaderboard, fun, games, social,
    stickers, utilities, owner,
)
from bot.plugins import ai, information, federation, automation, reporting

# Plugins using register_handlers
_REG_PLUGINS = [
    start, help, admin, moderation, welcome, filters,
    notes, economy, leaderboard, fun, games, social,
    stickers, utilities, owner,
]
# Plugins using setup
_SETUP_PLUGINS = [ai, information, federation, automation, reporting]


async def _post_init(app: Application) -> None:
    await init_db()
    logger.info("Database initialised.")

    # Scheduler for reminders/schedules
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from bot.database.connection import get_session
        from bot.database.models import Reminder, Schedule
        from sqlalchemy import select

        scheduler = AsyncIOScheduler()

        async def _fire_reminders() -> None:
            now = datetime.datetime.utcnow()
            async with get_session() as db:
                result = await db.execute(
                    select(Reminder).where(Reminder.active == True, Reminder.remind_at <= now)
                )
                for r in result.scalars().all():
                    try:
                        await app.bot.send_message(r.chat_id, f"⏰ Reminder: {r.message}")
                    except Exception:
                        pass
                    r.active = False
                await db.commit()

        async def _fire_schedules() -> None:
            now = datetime.datetime.utcnow()
            async with get_session() as db:
                result = await db.execute(
                    select(Schedule).where(Schedule.active == True, Schedule.next_run <= now)
                )
                for s in result.scalars().all():
                    try:
                        await app.bot.send_message(s.chat_id, s.message)
                    except Exception:
                        pass
                    if s.repeat and s.interval_seconds:
                        s.next_run = now + datetime.timedelta(seconds=s.interval_seconds)
                    else:
                        s.active = False
                await db.commit()

        scheduler.add_job(_fire_reminders, "interval", seconds=30)
        scheduler.add_job(_fire_schedules, "interval", seconds=30)
        scheduler.start()
        app.bot_data["scheduler"] = scheduler
        logger.info("Scheduler started.")
    except ImportError:
        logger.warning("apscheduler not installed; reminders/schedules disabled.")


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    for plugin in _REG_PLUGINS:
        if hasattr(plugin, "register_handlers"):
            plugin.register_handlers(app)
        elif hasattr(plugin, "setup"):
            plugin.setup(app)

    for plugin in _SETUP_PLUGINS:
        plugin.setup(app)

    logger.info("All plugins registered. Starting polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
