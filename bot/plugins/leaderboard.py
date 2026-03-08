"""
bot/plugins/leaderboard.py
──────────────────────────
Message leaderboard system with anti-spam rules, pagination, voice/media
tracking, and per-user analytics.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, MessageEntityType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.database.connection import get_session
from bot.database.models import Leaderboard, User
from bot.helpers.formatters import (
    bold,
    code,
    escape_html,
    format_number,
    italic,
    progress_bar,
    user_mention,
)

logger = logging.getLogger(__name__)

# ── Anti-spam state (in-memory) ───────────────────────────────────────────────
# {(chat_id, user_id): deque of timestamps}
_flood_tracker: Dict[Tuple[int, int], Deque[float]] = defaultdict(lambda: deque(maxlen=6))
# {(chat_id, user_id): deque of last 5 message texts}
_msg_history: Dict[Tuple[int, int], Deque[str]] = defaultdict(lambda: deque(maxlen=5))

FLOOD_WINDOW = 10.0  # seconds
FLOOD_LIMIT = 5      # messages per window
WORD_POINTS = 1      # base point per message
VOICE_POINTS_PER_30S = 1
MEDIA_POINTS = 2
QUALITY_BONUS = 1    # for messages > 50 chars
MIN_MSG_LEN = 3      # ignore shorter messages


# ── DB Helpers ────────────────────────────────────────────────────────────────

async def _get_or_create_lb(user_id: int, chat_id: int) -> Leaderboard:
    async with get_session() as session:
        stmt = select(Leaderboard).where(
            Leaderboard.user_id == user_id,
            Leaderboard.chat_id == chat_id,
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = Leaderboard(user_id=user_id, chat_id=chat_id)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


async def _add_points(
    user_id: int,
    chat_id: int,
    points: int,
    voice_seconds: int = 0,
    media: bool = False,
) -> None:
    async with get_session() as session:
        stmt = select(Leaderboard).where(
            Leaderboard.user_id == user_id,
            Leaderboard.chat_id == chat_id,
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = Leaderboard(user_id=user_id, chat_id=chat_id)
            session.add(row)

        row.messages_today = (row.messages_today or 0) + points
        row.messages_week = (row.messages_week or 0) + points
        row.messages_month = (row.messages_month or 0) + points
        row.messages_all = (row.messages_all or 0) + points
        if voice_seconds:
            row.voice_seconds = (row.voice_seconds or 0) + voice_seconds
        if media:
            row.media_count = (row.media_count or 0) + 1
        await session.commit()


async def _reply(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


# ── Message handler ───────────────────────────────────────────────────────────

async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Count messages according to leaderboard rules."""
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if user.is_bot:
        return

    now = __import__("time").time()
    key = (chat.id, user.id)

    # ── Voice message handling ────────────────────────────────────────────────
    if msg.voice:
        duration = msg.voice.duration or 0
        voice_pts = max(1, duration // 30) * VOICE_POINTS_PER_30S
        await _add_points(user.id, chat.id, voice_pts, voice_seconds=duration)
        return

    # ── Media handling ────────────────────────────────────────────────────────
    if msg.photo or msg.video:
        await _add_points(user.id, chat.id, MEDIA_POINTS, media=True)
        return

    # ── Text message rules ────────────────────────────────────────────────────
    text = msg.text or msg.caption or ""

    if len(text) < MIN_MSG_LEN:
        return

    # Flood check: >5 messages in 10 seconds
    history = _flood_tracker[key]
    history.append(now)
    recent = [t for t in history if now - t <= FLOOD_WINDOW]
    if len(recent) > FLOOD_LIMIT:
        return

    # Repeat check: same as any of last 5 messages
    msg_hist = _msg_history[key]
    if text.strip() in msg_hist:
        return
    msg_hist.append(text.strip())

    # Points calculation
    points = WORD_POINTS
    if len(text) > 50:
        points += QUALITY_BONUS

    await _add_points(user.id, chat.id, points)

    # Also update global user message count
    async with get_session() as session:
        stmt = select(User).where(User.user_id == user.id)
        db_user = (await session.execute(stmt)).scalar_one_or_none()
        if db_user:
            db_user.message_count = (db_user.message_count or 0) + 1
            db_user.last_active = datetime.now(timezone.utc)
            await session.commit()


# ── /rank ─────────────────────────────────────────────────────────────────────

async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rank [@user] — Show user's rank and stats card."""
    chat = update.effective_chat
    if not chat:
        return

    target_id = update.effective_user.id if update.effective_user else None
    if update.effective_message and update.effective_message.reply_to_message:
        replied = update.effective_message.reply_to_message.from_user
        if replied:
            target_id = replied.id
    elif context.args:
        arg = context.args[0].lstrip("@")
        if arg.isdigit():
            target_id = int(arg)
        else:
            try:
                tg = await context.bot.get_chat(f"@{arg}")
                target_id = tg.id
            except TelegramError:
                await _reply(update, f"❌ Could not find user '@{escape_html(arg)}'.")
                return

    if not target_id:
        await _reply(update, "❌ Could not determine target user.")
        return

    lb = await _get_or_create_lb(target_id, chat.id)

    # Calculate rank position
    async with get_session() as session:
        stmt = (
            select(func.count())
            .select_from(Leaderboard)
            .where(
                Leaderboard.chat_id == chat.id,
                Leaderboard.messages_all > lb.messages_all,
            )
        )
        rank_pos = (await session.execute(stmt)).scalar_one() + 1

    try:
        target = await context.bot.get_chat(target_id)
        name = escape_html(target.first_name or str(target_id))
    except TelegramError:
        name = str(target_id)

    total = lb.messages_all or 0
    bar = progress_bar(min(total, 1000), 1000, length=10)

    text = (
        f"📊 {bold(f'{name}\'s Rank Card')}\n\n"
        f"🏆 {bold('Rank:')} #{code(str(rank_pos))}\n"
        f"💬 {bold('Total messages:')} {code(format_number(total))}\n"
        f"📅 {bold('Today:')} {code(format_number(lb.messages_today or 0))}\n"
        f"📆 {bold('This week:')} {code(format_number(lb.messages_week or 0))}\n"
        f"🗓️ {bold('This month:')} {code(format_number(lb.messages_month or 0))}\n"
        f"🎤 {bold('Voice (sec):')} {code(format_number(lb.voice_seconds or 0))}\n"
        f"🖼️ {bold('Media shared:')} {code(format_number(lb.media_count or 0))}\n"
        f"\n{bar}"
    )
    await _reply(update, text)


# ── /top ──────────────────────────────────────────────────────────────────────

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/top [today|week|month|all] — Leaderboard with pagination."""
    chat = update.effective_chat
    if not chat:
        return

    args = context.args or []
    period = args[0].lower() if args else "all"
    page = 0

    await _send_top(update, context, chat.id, period, page, edit=False)


async def _send_top(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    period: str,
    page: int,
    edit: bool = False,
) -> None:
    col_map = {
        "today": Leaderboard.messages_today,
        "week": Leaderboard.messages_week,
        "month": Leaderboard.messages_month,
        "all": Leaderboard.messages_all,
    }
    col = col_map.get(period, Leaderboard.messages_all)
    per_page = 10
    offset = page * per_page

    async with get_session() as session:
        stmt = (
            select(Leaderboard)
            .where(Leaderboard.chat_id == chat_id)
            .order_by(col.desc())
            .offset(offset)
            .limit(per_page + 1)
        )
        rows = (await session.execute(stmt)).scalars().all()

    has_next = len(rows) > per_page
    rows = rows[:per_page]

    if not rows:
        text = f"📊 {bold('Leaderboard')}\n\n{italic('No data yet.')}"
    else:
        period_label = {"today": "Today", "week": "This Week", "month": "This Month", "all": "All Time"}.get(period, "All Time")
        lines = [f"🏆 {bold(f'Top Members — {period_label}')}\n"]
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7

        for i, lb in enumerate(rows):
            try:
                member = await context.bot.get_chat(lb.user_id)
                name = escape_html(member.first_name or str(lb.user_id))
            except TelegramError:
                name = str(lb.user_id)

            count = getattr(lb, col.key) or 0
            lines.append(f"{medals[i]} {bold(name)}: {code(format_number(count))}")

        text = "\n".join(lines)

    # Build nav buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"lb:top:{period}:{page - 1}"))
    if has_next:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"lb:top:{period}:{page + 1}"))

    period_row = [
        InlineKeyboardButton("Today", callback_data=f"lb:top:today:0"),
        InlineKeyboardButton("Week", callback_data=f"lb:top:week:0"),
        InlineKeyboardButton("Month", callback_data=f"lb:top:month:0"),
        InlineKeyboardButton("All", callback_data=f"lb:top:all:0"),
    ]
    buttons = [period_row]
    if nav_row:
        buttons.append(nav_row)
    markup = InlineKeyboardMarkup(buttons)

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError:
            pass
    else:
        await _reply(update, text, markup)


# ── /topvoice ─────────────────────────────────────────────────────────────────

async def topvoice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/topvoice — Top voice message senders by duration."""
    chat = update.effective_chat
    if not chat:
        return

    async with get_session() as session:
        stmt = (
            select(Leaderboard)
            .where(Leaderboard.chat_id == chat.id, Leaderboard.voice_seconds > 0)
            .order_by(Leaderboard.voice_seconds.desc())
            .limit(10)
        )
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        await _reply(update, f"🎤 {bold('Top Voice')}\n\n{italic('No voice data yet.')}")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"🎤 {bold('Top Voice Senders')}\n"]
    for i, lb in enumerate(rows):
        try:
            m = await context.bot.get_chat(lb.user_id)
            name = escape_html(m.first_name or str(lb.user_id))
        except TelegramError:
            name = str(lb.user_id)
        secs = lb.voice_seconds or 0
        h, r = divmod(secs, 3600)
        mins, s = divmod(r, 60)
        dur = f"{h}h {mins}m {s}s" if h else f"{mins}m {s}s"
        lines.append(f"{medals[i]} {bold(name)}: {code(dur)}")

    await _reply(update, "\n".join(lines))


# ── /topmedia ─────────────────────────────────────────────────────────────────

async def topmedia_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/topmedia — Top media sharers."""
    chat = update.effective_chat
    if not chat:
        return

    async with get_session() as session:
        stmt = (
            select(Leaderboard)
            .where(Leaderboard.chat_id == chat.id, Leaderboard.media_count > 0)
            .order_by(Leaderboard.media_count.desc())
            .limit(10)
        )
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        await _reply(update, f"🖼️ {bold('Top Media')}\n\n{italic('No media data yet.')}")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"🖼️ {bold('Top Media Sharers')}\n"]
    for i, lb in enumerate(rows):
        try:
            m = await context.bot.get_chat(lb.user_id)
            name = escape_html(m.first_name or str(lb.user_id))
        except TelegramError:
            name = str(lb.user_id)
        lines.append(f"{medals[i]} {bold(name)}: {code(format_number(lb.media_count or 0))} files")

    await _reply(update, "\n".join(lines))


# ── /topwords ─────────────────────────────────────────────────────────────────

async def topwords_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/topwords — Most messages typed (all-time)."""
    chat = update.effective_chat
    if not chat:
        return

    async with get_session() as session:
        stmt = (
            select(Leaderboard)
            .where(Leaderboard.chat_id == chat.id)
            .order_by(Leaderboard.messages_all.desc())
            .limit(10)
        )
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        await _reply(update, f"📝 {bold('Top Words')}\n\n{italic('No data yet.')}")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"📝 {bold('Top Message Senders')}\n"]
    for i, lb in enumerate(rows):
        try:
            m = await context.bot.get_chat(lb.user_id)
            name = escape_html(m.first_name or str(lb.user_id))
        except TelegramError:
            name = str(lb.user_id)
        lines.append(f"{medals[i]} {bold(name)}: {code(format_number(lb.messages_all or 0))} messages")

    await _reply(update, "\n".join(lines))


# ── /analytics ────────────────────────────────────────────────────────────────

async def analytics_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/analytics [@user] — Personal chat analytics."""
    chat = update.effective_chat
    if not chat:
        return

    target_id = update.effective_user.id if update.effective_user else None
    if update.effective_message and update.effective_message.reply_to_message:
        replied = update.effective_message.reply_to_message.from_user
        if replied:
            target_id = replied.id
    elif context.args:
        arg = context.args[0].lstrip("@")
        if arg.isdigit():
            target_id = int(arg)
        else:
            try:
                tg = await context.bot.get_chat(f"@{arg}")
                target_id = tg.id
            except TelegramError:
                await _reply(update, f"❌ Could not find user '@{escape_html(arg)}'.")
                return

    if not target_id:
        await _reply(update, "❌ Could not determine user.")
        return

    lb = await _get_or_create_lb(target_id, chat.id)

    try:
        target = await context.bot.get_chat(target_id)
        name = escape_html(target.first_name or str(target_id))
    except TelegramError:
        name = str(target_id)

    async with get_session() as session:
        stmt = select(User).where(User.user_id == target_id)
        db_user = (await session.execute(stmt)).scalar_one_or_none()

    join_info = ""
    if db_user and db_user.join_date:
        jd = db_user.join_date
        join_info = f"\n📅 {bold('Member since:')} {code(jd.strftime('%Y-%m-%d'))}"

    total = lb.messages_all or 0
    rank_stmt_result = 0
    async with get_session() as session:
        rank_stmt = (
            select(func.count())
            .select_from(Leaderboard)
            .where(Leaderboard.chat_id == chat.id, Leaderboard.messages_all > total)
        )
        rank_pos = (await session.execute(rank_stmt)).scalar_one() + 1

    text = (
        f"📈 {bold(f'{name}\'s Analytics')}\n\n"
        f"🏆 {bold('Rank:')} #{code(str(rank_pos))}\n"
        f"💬 {bold('Total messages:')} {code(format_number(total))}\n"
        f"📅 {bold('Today:')} {code(format_number(lb.messages_today or 0))}\n"
        f"📆 {bold('This week:')} {code(format_number(lb.messages_week or 0))}\n"
        f"🗓️ {bold('This month:')} {code(format_number(lb.messages_month or 0))}\n"
        f"🎤 {bold('Voice (sec):')} {code(format_number(lb.voice_seconds or 0))}\n"
        f"🖼️ {bold('Media shared:')} {code(format_number(lb.media_count or 0))}"
        f"{join_info}"
    )
    await _reply(update, text)


# ── /chatstats ────────────────────────────────────────────────────────────────

async def chatstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chatstats — Overall chat statistics."""
    chat = update.effective_chat
    if not chat:
        return

    async with get_session() as session:
        total_msgs_stmt = select(func.sum(Leaderboard.messages_all)).where(Leaderboard.chat_id == chat.id)
        total_msgs = (await session.execute(total_msgs_stmt)).scalar_one() or 0

        today_msgs_stmt = select(func.sum(Leaderboard.messages_today)).where(Leaderboard.chat_id == chat.id)
        today_msgs = (await session.execute(today_msgs_stmt)).scalar_one() or 0

        total_voice_stmt = select(func.sum(Leaderboard.voice_seconds)).where(Leaderboard.chat_id == chat.id)
        total_voice = (await session.execute(total_voice_stmt)).scalar_one() or 0

        total_media_stmt = select(func.sum(Leaderboard.media_count)).where(Leaderboard.chat_id == chat.id)
        total_media = (await session.execute(total_media_stmt)).scalar_one() or 0

        active_members_stmt = select(func.count()).select_from(Leaderboard).where(
            Leaderboard.chat_id == chat.id, Leaderboard.messages_all > 0
        )
        active_members = (await session.execute(active_members_stmt)).scalar_one() or 0

    h, rem = divmod(int(total_voice), 3600)
    mins, s = divmod(rem, 60)
    voice_fmt = f"{h}h {mins}m {s}s" if h else f"{mins}m {s}s"

    chat_title = escape_html(chat.title or "This chat")
    text = (
        f"📊 {bold(f'{chat_title} — Statistics')}\n\n"
        f"💬 {bold('Total messages:')} {code(format_number(int(total_msgs)))}\n"
        f"📅 {bold('Messages today:')} {code(format_number(int(today_msgs)))}\n"
        f"🎤 {bold('Total voice:')} {code(voice_fmt)}\n"
        f"🖼️ {bold('Total media:')} {code(format_number(int(total_media)))}\n"
        f"👥 {bold('Active members:')} {code(format_number(active_members))}"
    )
    await _reply(update, text)


# ── Callback handler ──────────────────────────────────────────────────────────

async def leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle lb:* callback queries."""
    query = update.callback_query
    chat = update.effective_chat
    if not query or not chat:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "top" and len(parts) >= 4:
        period = parts[2]
        try:
            page = int(parts[3])
        except ValueError:
            page = 0
        await _send_top(update, context, chat.id, period, page, edit=True)


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("rank", rank_command))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("topvoice", topvoice_command))
    app.add_handler(CommandHandler("topmedia", topmedia_command))
    app.add_handler(CommandHandler("topwords", topwords_command))
    app.add_handler(CommandHandler("analytics", analytics_command))
    app.add_handler(CommandHandler("chatstats", chatstats_command))
    app.add_handler(CallbackQueryHandler(leaderboard_callback, pattern=r"^lb:"))
    # Track all messages in groups
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.COMMAND,
            track_message,
        ),
        group=10,  # low priority group so other handlers run first
    )
