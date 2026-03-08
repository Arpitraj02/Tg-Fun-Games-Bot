"""bot/plugins/automation.py – Reminders, scheduling, and auto-delete."""
from __future__ import annotations
import datetime
import re
from telegram import Update, Message
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from sqlalchemy import select
from bot.database.connection import get_session
from bot.database.models import Reminder, Schedule

_AUTODELETE_CHATS: set[int] = set()


def _parse_duration(s: str) -> datetime.timedelta | None:
    m = re.fullmatch(r"(\d+)(s|m|h|d)", s.strip().lower())
    if not m:
        return None
    val, unit = int(m.group(1)), m.group(2)
    return {"s": datetime.timedelta(seconds=val), "m": datetime.timedelta(minutes=val),
            "h": datetime.timedelta(hours=val), "d": datetime.timedelta(days=val)}[unit]


async def reminder_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if len(args) < 3:
        await update.message.reply_text("Usage: /reminder me|@user <time> <text>\nTime format: 10s, 5m, 2h, 1d")
        return
    _target, time_str, *msg_parts = args
    delta = _parse_duration(time_str)
    if not delta:
        await update.message.reply_text("❌ Invalid time format. Use: 10s, 5m, 2h, 1d")
        return
    remind_at = datetime.datetime.utcnow() + delta
    message = " ".join(msg_parts)
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    async with get_session() as db:
        r = Reminder(user_id=user_id, chat_id=chat_id, message=message, remind_at=remind_at)
        db.add(r)
        await db.commit()
        await db.refresh(r)
    await update.message.reply_text(f"⏰ Reminder set for {remind_at.strftime('%Y-%m-%d %H:%M UTC')} (ID: {r.id})")


async def reminders_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    async with get_session() as db:
        result = await db.execute(
            select(Reminder).where(Reminder.user_id == user_id, Reminder.active == True)
        )
        reminders = result.scalars().all()
    if not reminders:
        await update.message.reply_text("You have no active reminders.")
        return
    lines = [f"#{r.id} – {r.remind_at.strftime('%Y-%m-%d %H:%M UTC')}: {r.message[:50]}" for r in reminders]
    await update.message.reply_text("⏰ Your reminders:\n" + "\n".join(lines))


async def cancelreminder_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /cancelreminder <id>")
        return
    try:
        rid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    user_id = update.effective_user.id
    async with get_session() as db:
        r = await db.get(Reminder, rid)
        if not r or r.user_id != user_id:
            await update.message.reply_text("❌ Reminder not found.")
            return
        r.active = False
        await db.commit()
    await update.message.reply_text(f"✅ Reminder #{rid} cancelled.")


async def schedule_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /schedule <time> <message>\nTime format: 10s, 5m, 2h, 1d")
        return
    time_str, *msg_parts = args
    delta = _parse_duration(time_str)
    if not delta:
        await update.message.reply_text("❌ Invalid time format.")
        return
    next_run = datetime.datetime.utcnow() + delta
    message = " ".join(msg_parts)
    chat_id = update.effective_chat.id
    async with get_session() as db:
        s = Schedule(chat_id=chat_id, message=message, next_run=next_run, interval_seconds=0, repeat=False)
        db.add(s)
        await db.commit()
        await db.refresh(s)
    await update.message.reply_text(f"📅 Message scheduled for {next_run.strftime('%Y-%m-%d %H:%M UTC')} (ID: {s.id})")


async def autodelete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    arg = (ctx.args[0] if ctx.args else "").lower()
    if arg == "on":
        _AUTODELETE_CHATS.add(chat_id)
        await update.message.reply_text("✅ Auto-delete service messages enabled.")
    elif arg == "off":
        _AUTODELETE_CHATS.discard(chat_id)
        await update.message.reply_text("✅ Auto-delete service messages disabled.")
    else:
        await update.message.reply_text("Usage: /autodelete <on|off>")


async def _autodelete_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id in _AUTODELETE_CHATS and update.message:
        try:
            await update.message.delete()
        except Exception:
            pass


def setup(app: Application) -> None:
    app.add_handler(CommandHandler("reminder", reminder_cmd))
    app.add_handler(CommandHandler("reminders", reminders_cmd))
    app.add_handler(CommandHandler("cancelreminder", cancelreminder_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("autodelete", autodelete_cmd))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.ALL,
        _autodelete_handler,
    ))
