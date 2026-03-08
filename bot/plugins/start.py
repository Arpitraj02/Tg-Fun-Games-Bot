"""
bot/plugins/start.py
────────────────────
/start, /alive, /ping, /games, /settings, /profile commands and
start-menu callback handling. All menus are owner-locked — only the
user who triggered the command can interact with the inline buttons.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
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
from bot.database.connection import get_session
from bot.database.models import Economy, User
from bot.helpers.formatters import (
    bold,
    code,
    escape_html,
    format_number,
    format_time,
    italic,
    user_mention,
    xp_bar,
)

logger = logging.getLogger(__name__)

BOT_VERSION = "2.0.0"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _uid_tag(uid: int) -> str:
    """Short tag appended to callback_data to identify the menu owner."""
    return str(uid)


def _owner_check(query_uid: int, callback_data: str) -> bool:
    """Return True if the last segment of callback_data matches query_uid."""
    try:
        return int(callback_data.rsplit(":", 1)[-1]) == query_uid
    except (ValueError, IndexError):
        return True  # legacy data without uid — allow


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _start_keyboard_private(uid: int) -> InlineKeyboardMarkup:
    u = _uid_tag(uid)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("◆ Help", callback_data=f"start:help:{u}"),
                InlineKeyboardButton("◆ Settings", callback_data=f"start:settings:{u}"),
            ],
            [
                InlineKeyboardButton("◆ Games", callback_data=f"start:games:{u}"),
                InlineKeyboardButton("◆ Economy", callback_data=f"start:economy:{u}"),
            ],
            [
                InlineKeyboardButton("◆ My Profile", callback_data=f"start:profile:{u}"),
                InlineKeyboardButton("◆ Reminders", callback_data=f"start:reminders:{u}"),
            ],
            [
                InlineKeyboardButton(
                    "Support Chat",
                    url=f"https://t.me/{config.SUPPORT_CHAT.lstrip('@')}"
                    if config.SUPPORT_CHAT
                    else "https://t.me/",
                ),
            ],
        ]
    )


def _start_keyboard_group(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Start in PM",
                    url=f"https://t.me/{bot_username}?start=group",
                ),
                InlineKeyboardButton("Help", callback_data="start:help:0"),
            ]
        ]
    )


def _alive_keyboard(uid: int) -> InlineKeyboardMarkup:
    u = _uid_tag(uid)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("◆ Help", callback_data=f"start:help:{u}"),
                InlineKeyboardButton("◄ Main Menu", callback_data=f"start:menu:{u}"),
            ]
        ]
    )


def _back_keyboard(uid: int) -> InlineKeyboardMarkup:
    u = _uid_tag(uid)
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("◄ Back", callback_data=f"start:menu:{u}")]]
    )


# ── Text builders ─────────────────────────────────────────────────────────────

def _private_welcome_text(user_name: str) -> str:
    return (
        f"{bold(f'Hello, {escape_html(user_name)}!')}  (^_^)\n\n"
        f"I am an {bold('advanced group management bot')} packed with features:\n\n"
        f"◆ {bold('Administration')} — promote, demote, pin, set descriptions\n"
        f"◆ {bold('Moderation')} — ban, kick, mute, warn, temp-actions\n"
        f"◆ {bold('Welcome System')} — custom messages, captcha, rules\n"
        f"◆ {bold('Notes & Filters')} — save notes, auto-respond to triggers\n"
        f"◆ {bold('Economy')} — coins, bank, daily rewards\n"
        f"◆ {bold('Games')} — trivia, hangman, wordle, TicTacToe\n"
        f"◆ {bold('AI')} — ask questions, translate, summarise\n"
        f"◆ {bold('Media')} — convert stickers, OCR, QR codes\n"
        f"◆ {bold('Federation')} — share bans across groups\n\n"
        f"{italic('Use the buttons below to explore.')}"
    )


def _group_welcome_text(chat_title: str) -> str:
    return (
        f"{bold(f'Thanks for adding me to {escape_html(chat_title)}!')}  (^_^)\n\n"
        f"I am ready to help manage this group.  To get started:\n\n"
        f"◆ Make me an {bold('admin')} with the required permissions\n"
        f"◆ Use /help to see all available commands\n"
        f"◆ Use /settings to configure features\n\n"
        f"{italic('Need help?')} Use /help or message support."
    )


# ── Games list ────────────────────────────────────────────────────────────────

_GAMES_TEXT = (
    f"{bold('Available Games')}  (^_^)\n\n"
    f"◆ {code('/tictactoe')} {italic('or')} {code('/ttt')} — TicTacToe (1v1)\n"
    f"◆ {code('/trivia')} — Multiple-choice trivia question\n"
    f"◆ {code('/hangman')} — Guess the word letter by letter\n"
    f"◆ {code('/wordle')} — 5-letter word guessing game\n"
    f"◆ {code('/blackjack')} {italic('or')} {code('/bj')} — Blackjack card game\n"
    f"◆ {code('/mathchallenge')} — Solve a maths problem for coins\n"
    f"◆ {code('/riddle')} — Riddle challenge\n"
    f"◆ {code('/rps_challenge')} — Rock Paper Scissors\n"
    f"◆ {code('/quiz')} — Custom quiz question\n"
    f"◆ {code('/game_stats')} — Your personal game statistics\n"
    f"◆ {code('/triviaboard')} — Trivia leaderboard\n\n"
    f"{italic('Challenge a friend by replying to their message!')}"
)


# ── /settings text ────────────────────────────────────────────────────────────

_SETTINGS_TEXT = (
    f"{bold('Settings')}  (~_~)\n\n"
    f"Use the following commands to configure the bot:\n\n"
    f"◆ {code('/setwelcome')} — Set a custom welcome message\n"
    f"◆ {code('/setgoodbye')} — Set a custom goodbye message\n"
    f"◆ {code('/setwarns')} — Set the max warnings before action\n"
    f"◆ {code('/setrules')} — Set group rules\n"
    f"◆ {code('/setlang')} — Set bot language for this group\n"
    f"◆ {code('/antiflood')} — Configure flood protection\n"
    f"◆ {code('/antiraid')} — Configure raid protection\n"
    f"◆ {code('/filtermode')} — Set filter action mode\n\n"
    f"{italic('Settings are group-specific and require admin rights.')}"
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
            reply_markup=_start_keyboard_private(user.id),
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
    sent = await update.effective_message.reply_text("(o_o)  Measuring ping...")
    ping_ms = round((time.monotonic() - t_start) * 1000, 2)

    user = update.effective_user
    mention = user_mention(user.id, user.first_name) if user else "User"

    text = (
        f"{bold('Bot is alive!')}  (^_^)\n\n"
        f"◆ {bold('Requested by:')} {mention}\n"
        f"◆ {bold('Version:')} {code(BOT_VERSION)}\n"
        f"◆ {bold('Uptime:')} {code(uptime_str)}\n"
        f"◆ {bold('Ping:')} {code(f'{ping_ms} ms')}\n"
        f"◆ {bold('Server time:')} {code(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))}\n"
        f"◆ {bold('Python:')} {code('3.11+')}\n"
        f"◆ {bold('PTB:')} {code('v20+')}"
    )

    try:
        await sent.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_alive_keyboard(user.id if user else 0),
        )
    except TelegramError:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_alive_keyboard(user.id if user else 0),
        )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ping — measure and display the bot's response time."""
    t_start = time.monotonic()
    sent = await update.effective_message.reply_text("(o)  Pong!")
    ping_ms = round((time.monotonic() - t_start) * 1000, 2)

    try:
        await sent.edit_text(
            f"{bold('Pong!')}  (^)\n\n"
            f"◆ {bold('Response time:')} {code(f'{ping_ms} ms')}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


async def games_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/games — list all available games."""
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(
        _GAMES_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◄ Main Menu", callback_data=f"start:menu:{user.id}")]]
        ),
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/settings — show bot configuration commands."""
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(
        _SETTINGS_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◄ Main Menu", callback_data=f"start:menu:{user.id}")]]
        ),
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/profile — show the user's profile and stats."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    async with get_session() as session:
        db_user = (
            await session.execute(select(User).where(User.user_id == user.id))
        ).scalar_one_or_none()
        eco = (
            await session.execute(
                select(Economy).where(
                    Economy.user_id == user.id, Economy.chat_id == chat.id
                )
            )
        ).scalar_one_or_none()

    xp = db_user.xp if db_user else 0
    level = max(1, (xp or 0) // 100)
    rep = db_user.reputation if db_user else 0
    balance = eco.balance if eco else 0
    bank = eco.bank if eco else 0
    bar = xp_bar(xp or 0, level)

    name_html = escape_html(user.full_name)
    username_line = f"\n◆ {bold('Username:')} @{user.username}" if user.username else ""

    text = (
        f"{bold(f'{name_html}' + chr(39) + 's Profile')}  (^_^)\n\n"
        f"◆ {bold('ID:')} {code(str(user.id))}"
        f"{username_line}\n"
        f"◆ {bold('Level:')} {code(str(level))}\n"
        f"◆ {bold('XP:')} {code(str(xp or 0))}  {bar}\n"
        f"◆ {bold('Reputation:')} {code(str(rep or 0))}\n"
        f"◆ {bold('Wallet:')} {code(format_number(balance))} coins\n"
        f"◆ {bold('Bank:')} {code(format_number(bank))} coins"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◄ Main Menu", callback_data=f"start:menu:{user.id}")]]
        ),
    )


# ── Callback handler ──────────────────────────────────────────────────────────

async def start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle start:* callback queries."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    # ── Owner check ──────────────────────────────────────────────────────────
    if not _owner_check(user.id, query.data or ""):
        await query.answer("(x_x)  This menu is not for you!", show_alert=True)
        return

    await query.answer()

    # Strip trailing uid to get clean action
    raw = query.data or ""
    parts = raw.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "menu":
        text = _private_welcome_text(user.first_name)
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=_start_keyboard_private(user.id),
            )
        except TelegramError:
            pass
        return

    if action == "help":
        uid = _uid_tag(user.id)
        try:
            await query.edit_message_text(
                f"{bold('Help Menu')}  (~_~)\n\n"
                f"Browse commands by category below.\n"
                f"You can also use {code('/help &lt;command&gt;')} for detailed info.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("◆ Admin", callback_data=f"help:cat:Admin:0:{uid}"),
                            InlineKeyboardButton("◆ Moderation", callback_data=f"help:cat:Moderation:0:{uid}"),
                        ],
                        [
                            InlineKeyboardButton("◆ Welcome", callback_data=f"help:cat:Welcome:0:{uid}"),
                            InlineKeyboardButton("◆ Notes", callback_data=f"help:cat:Notes:0:{uid}"),
                        ],
                        [
                            InlineKeyboardButton("◆ Economy", callback_data=f"help:cat:Economy:0:{uid}"),
                            InlineKeyboardButton("◆ Games", callback_data=f"help:cat:Games:0:{uid}"),
                        ],
                        [
                            InlineKeyboardButton("◆ AI", callback_data=f"help:cat:AI:0:{uid}"),
                            InlineKeyboardButton("◆ Utilities", callback_data=f"help:cat:Utilities:0:{uid}"),
                        ],
                        [InlineKeyboardButton("◄ Back", callback_data=f"start:menu:{uid}")],
                    ]
                ),
            )
        except TelegramError:
            pass
        return

    if action == "games":
        try:
            await query.edit_message_text(
                _GAMES_TEXT,
                parse_mode=ParseMode.HTML,
                reply_markup=_back_keyboard(user.id),
            )
        except TelegramError:
            pass
        return

    if action == "settings":
        try:
            await query.edit_message_text(
                _SETTINGS_TEXT,
                parse_mode=ParseMode.HTML,
                reply_markup=_back_keyboard(user.id),
            )
        except TelegramError:
            pass
        return

    if action == "economy":
        try:
            await query.edit_message_text(
                f"{bold('Economy')}  ($.$)\n\n"
                f"◆ {code('/balance')} or {code('/bal')} — Check your coins\n"
                f"◆ {code('/daily')} — Claim daily reward\n"
                f"◆ {code('/weekly')} — Claim weekly reward\n"
                f"◆ {code('/work')} — Earn coins by working\n"
                f"◆ {code('/shop')} — Browse the item shop\n"
                f"◆ {code('/gamble')} — Gamble your coins\n"
                f"◆ {code('/richest')} — Top 10 richest members\n\n"
                f"{italic('Use /balance to see your current coins.')}",
                parse_mode=ParseMode.HTML,
                reply_markup=_back_keyboard(user.id),
            )
        except TelegramError:
            pass
        return

    if action == "profile":
        # Fetch live data and show inline
        chat = update.effective_chat
        async with get_session() as session:
            db_user = (
                await session.execute(select(User).where(User.user_id == user.id))
            ).scalar_one_or_none()
            eco = (
                await session.execute(
                    select(Economy).where(
                        Economy.user_id == user.id,
                        Economy.chat_id == (chat.id if chat else user.id),
                    )
                )
            ).scalar_one_or_none()

        xp = db_user.xp if db_user else 0
        level = max(1, (xp or 0) // 100)
        rep = db_user.reputation if db_user else 0
        balance = eco.balance if eco else 0
        bank = eco.bank if eco else 0
        bar = xp_bar(xp or 0, level)

        name_html = escape_html(user.full_name)
        try:
            await query.edit_message_text(
                f"{bold(name_html + chr(39) + 's Profile')}  (^_^)\n\n"
                f"◆ {bold('Level:')} {code(str(level))}\n"
                f"◆ {bold('XP:')} {code(str(xp or 0))}  {bar}\n"
                f"◆ {bold('Reputation:')} {code(str(rep or 0))}\n"
                f"◆ {bold('Wallet:')} {code(format_number(balance))} coins\n"
                f"◆ {bold('Bank:')} {code(format_number(bank))} coins",
                parse_mode=ParseMode.HTML,
                reply_markup=_back_keyboard(user.id),
            )
        except TelegramError:
            pass
        return

    if action == "reminders":
        try:
            await query.edit_message_text(
                f"{bold('Reminders')}  (o_o)\n\n"
                f"◆ {code('/reminder &lt;time&gt; &lt;message&gt;')} — Set a reminder\n"
                f"◆ {code('/reminders')} — List your reminders\n"
                f"◆ {code('/cancelreminder &lt;id&gt;')} — Cancel a reminder\n\n"
                f"{italic('Time format: 30m, 2h, 1d')}",
                parse_mode=ParseMode.HTML,
                reply_markup=_back_keyboard(user.id),
            )
        except TelegramError:
            pass
        return

    await query.answer("(x_x)  Unknown action.", show_alert=True)


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    # Record startup time so /alive can compute uptime
    app.bot_data.setdefault("start_time", time.time())

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("alive", alive_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("games", games_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CallbackQueryHandler(start_callback, pattern=r"^start:"))
