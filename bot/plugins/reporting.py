"""bot/plugins/reporting.py – Reporting, log channel, and logging toggle."""
from __future__ import annotations
from telegram import Update, Chat
from telegram.ext import Application, CommandHandler, ContextTypes
from sqlalchemy import select
from bot.database.connection import get_session
from bot.database.models import Report, Group

_REPORTS_OFF: set[int] = set()
_LOG_CHANNELS: dict[int, int] = {}
_LOGGING_OFF: set[int] = set()


async def report_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id in _REPORTS_OFF:
        await update.message.reply_text("Reports are disabled in this chat.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a message to report it.")
        return
    reported = update.message.reply_to_message.from_user
    reason = " ".join(ctx.args or []) or "No reason provided"
    reporter_id = update.effective_user.id
    async with get_session() as db:
        r = Report(
            chat_id=chat_id,
            reporter_id=reporter_id,
            reported_user_id=reported.id,
            message_id=update.message.reply_to_message.message_id,
            reason=reason,
        )
        db.add(r)
        await db.commit()
    await update.message.reply_text(
        f"✅ Reported [{reported.full_name}](tg://user?id={reported.id}).\nReason: {reason}",
        parse_mode="Markdown",
    )
    log_channel = _LOG_CHANNELS.get(chat_id)
    if log_channel and chat_id not in _LOGGING_OFF:
        try:
            await ctx.bot.send_message(
                log_channel,
                f"🚨 Report in {update.effective_chat.title}\n"
                f"Reporter: `{reporter_id}`\nReported: `{reported.id}`\nReason: {reason}",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def reports_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    arg = (ctx.args[0] if ctx.args else "").lower()
    if arg == "off":
        _REPORTS_OFF.add(chat_id)
        await update.message.reply_text("🔕 Reports disabled.")
    elif arg == "on":
        _REPORTS_OFF.discard(chat_id)
        await update.message.reply_text("🔔 Reports enabled.")
    else:
        status = "off" if chat_id in _REPORTS_OFF else "on"
        await update.message.reply_text(f"Reports are currently: {status}\nUsage: /reports <on|off>")


async def logchannel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /logchannel @channel")
        return
    channel = ctx.args[0]
    try:
        chat = await ctx.bot.get_chat(channel)
        _LOG_CHANNELS[update.effective_chat.id] = chat.id
        await update.message.reply_text(f"✅ Log channel set to {chat.title}.")
    except Exception:
        await update.message.reply_text("❌ Could not find that channel. Make sure the bot is an admin there.")


async def logging_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    arg = (ctx.args[0] if ctx.args else "").lower()
    if arg == "off":
        _LOGGING_OFF.add(chat_id)
        await update.message.reply_text("🔕 Logging disabled.")
    elif arg == "on":
        _LOGGING_OFF.discard(chat_id)
        await update.message.reply_text("🔔 Logging enabled.")
    else:
        status = "off" if chat_id in _LOGGING_OFF else "on"
        await update.message.reply_text(f"Logging is currently: {status}\nUsage: /logging <on|off>")


def setup(app: Application) -> None:
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("reports", reports_cmd))
    app.add_handler(CommandHandler("logchannel", logchannel_cmd))
    app.add_handler(CommandHandler("logging", logging_cmd))
