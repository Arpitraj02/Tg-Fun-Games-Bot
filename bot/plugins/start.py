"""
bot/plugins/start.py
────────────────────
/start, /alive, /ping commands and start-menu callback handling.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot import config
from bot.helpers.formatters import bold, code, format_time, italic, user_mention

logger = logging.getLogger(__name__)

BOT_VERSION = "2.0.0"

# ── Keyboards ─────────────────────────────────────────────────────────────────

def _start_keyboard_private() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📚 Help", callback_data="start:help"),
                InlineKeyboardButton("⚙️ Settings", callback_data="start:settings"),
            ],
            [
                InlineKeyboardButton("🎮 Games", callback_data="start:games"),
                InlineKeyboardButton("💰 Economy", callback_data="start:economy"),
            ],
            [
                InlineKeyboardButton("📊 My Profile", callback_data="start:profile"),
                InlineKeyboardButton("🔔 Reminders", callback_data="start:reminders"),
            ],
            [
                InlineKeyboardButton("🌐 Support Chat", url=f"https://t.me/{config.SUPPORT_CHAT.lstrip('@')}" if config.SUPPORT_CHAT else "https://t.me/"),
                InlineKeyboardButton("📢 Updates", url="https://t.me/"),
            ],
        ]
    )


def _start_keyboard_group(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📬 Start in PM",
                    url=f"https://t.me/{bot_username}?start=group",
                ),
                InlineKeyboardButton("📚 Help", callback_data="start:help"),
            ]
        ]
    )


def _alive_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📚 Help", callback_data="start:help"),
                InlineKeyboardButton("🔙 Main Menu", callback_data="start:menu"),
            ]
        ]
    )


# ── Text builders ─────────────────────────────────────────────────────────────

def _private_welcome_text(user_name: str) -> str:
    return (
        f"👋 {bold(f'Hello, {user_name}!')} Welcome to the bot!\n\n"
        f"I'm an {bold('ultra-advanced group management bot')} packed with features:\n\n"
        f"🛡️ {bold('Administration')} — promote, demote, pin, set descriptions\n"
        f"🔨 {bold('Moderation')} — ban, kick, mute, warn, temp-actions\n"
        f"👋 {bold('Welcome System')} — custom messages, captcha, rules\n"
        f"📝 {bold('Notes & Filters')} — save notes, auto-respond to triggers\n"
        f"💰 {bold('Economy')} — coins, bank, daily rewards, games\n"
        f"🎮 {bold('Games')} — trivia, hangman, word chain, TicTacToe\n"
        f"🤖 {bold('AI')} — ask questions, generate images, translate\n"
        f"📸 {bold('Media')} — convert stickers, OCR, QR codes\n"
        f"🌐 {bold('Federation')} — share bans across multiple groups\n\n"
        f"Use the buttons below to explore my features. 🚀"
    )


def _group_welcome_text(chat_title: str) -> str:
    return (
        f"👋 {bold(f'Thanks for adding me to {chat_title}!')} \n\n"
        f"I'm ready to help manage this group. To get started:\n\n"
        f"• Make me an {bold('admin')} with necessary permissions\n"
        f"• Use /help to see all available commands\n"
        f"• Use /settings to configure my features\n\n"
        f"Need help? Use /help or contact support."
    )


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — welcome message (private) or group greeting."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    if chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text(
            _private_welcome_text(user.first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=_start_keyboard_private(),
        )
    else:
        me = await context.bot.get_me()
        await update.effective_message.reply_text(
            _group_welcome_text(chat.title or "this group"),
            parse_mode=ParseMode.HTML,
            reply_markup=_start_keyboard_group(me.username or ""),
        )


async def alive_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/alive — bot uptime, ping and version info."""
    start_ts: float = context.application.bot_data.get("start_time", time.time())
    uptime_secs = int(time.time() - start_ts)
    uptime_str = format_time(uptime_secs)

    t_start = time.monotonic()
    sent = await update.effective_message.reply_text("⏳ Measuring ping…")
    ping_ms = round((time.monotonic() - t_start) * 1000, 2)

    user = update.effective_user
    mention = user_mention(user.id, user.first_name) if user else "User"

    text = (
        f"✅ {bold('Bot is alive!')}\n\n"
        f"👤 {bold('Requested by:')} {mention}\n"
        f"🤖 {bold('Version:')} {code(BOT_VERSION)}\n"
        f"⏱️ {bold('Uptime:')} {code(uptime_str)}\n"
        f"🏓 {bold('Ping:')} {code(f'{ping_ms} ms')}\n"
        f"🕐 {bold('Server time:')} {code(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))}\n"
        f"🐍 {bold('Python:')} {code('3.11+')}\n"
        f"📦 {bold('PTB:')} {code('v20+')}"
    )

    try:
        await sent.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_alive_keyboard(),
        )
    except TelegramError:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_alive_keyboard(),
        )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ping — measure and display the bot's response time."""
    t_start = time.monotonic()
    sent = await update.effective_message.reply_text("🏓 Pong!")
    ping_ms = round((time.monotonic() - t_start) * 1000, 2)

    try:
        await sent.edit_text(
            f"🏓 {bold('Pong!')}\n\n"
            f"📡 {bold('Response time:')} {code(f'{ping_ms} ms')}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


# ── Callback handler ──────────────────────────────────────────────────────────

async def start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle start:* callback queries."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    action = query.data.split(":", 1)[1] if ":" in query.data else ""

    if action == "menu":
        text = _private_welcome_text(user.first_name)
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=_start_keyboard_private(),
            )
        except TelegramError:
            pass
        return

    if action == "help":
        try:
            await query.edit_message_text(
                f"🆘 {bold('Help Menu')}\n\nBrowse commands by category below.\n"
                f"You can also use {code('/help &lt;command&gt;')} for detailed info.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("🛡️ Admin", callback_data="help:cat:Admin:0"),
                            InlineKeyboardButton("🔨 Moderation", callback_data="help:cat:Moderation:0"),
                        ],
                        [
                            InlineKeyboardButton("👋 Welcome", callback_data="help:cat:Welcome:0"),
                            InlineKeyboardButton("📝 Notes", callback_data="help:cat:Notes:0"),
                        ],
                        [
                            InlineKeyboardButton("💰 Economy", callback_data="help:cat:Economy:0"),
                            InlineKeyboardButton("🎮 Games", callback_data="help:cat:Games:0"),
                        ],
                        [
                            InlineKeyboardButton("🤖 AI", callback_data="help:cat:AI:0"),
                            InlineKeyboardButton("🔧 Utilities", callback_data="help:cat:Utilities:0"),
                        ],
                        [InlineKeyboardButton("🔙 Back", callback_data="start:menu")],
                    ]
                ),
            )
        except TelegramError:
            pass
        return

    if action in ("settings", "games", "economy", "profile", "reminders"):
        label_map = {
            "settings": "⚙️ Settings",
            "games": "🎮 Games",
            "economy": "💰 Economy",
            "profile": "📊 Profile",
            "reminders": "🔔 Reminders",
        }
        try:
            await query.edit_message_text(
                f"{label_map[action]}\n\n"
                f"{italic('Use the corresponding commands or type')} "
                f"{code('/' + action)} {italic('to interact with this feature.')}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back", callback_data="start:menu")]]
                ),
            )
        except TelegramError:
            pass
        return

    await query.answer("Unknown action.", show_alert=True)


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    # Record startup time so /alive can compute uptime
    app.bot_data.setdefault("start_time", time.time())

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("alive", alive_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CallbackQueryHandler(start_callback, pattern=r"^start:"))
