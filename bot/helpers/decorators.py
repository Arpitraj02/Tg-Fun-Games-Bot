"""
bot/helpers/decorators.py
─────────────────────────
Async decorator library for the bot's command handlers.

All decorators follow the python-telegram-bot v20+ calling convention
where handlers are coroutines receiving (update, context).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, Tuple

from telegram import ChatMember, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.config import LOG_CHANNEL_ID, MAINTENANCE_MODE, OWNER_ID, SUDO_USERS

logger = logging.getLogger(__name__)

# ── Type alias for PTB handlers ───────────────────────────────────────────────
Handler = Callable[..., Coroutine[Any, Any, None]]

# ── In-memory rate-limit / flood stores (reset on restart) ───────────────────
_rate_store: Dict[Tuple[int, str], float] = {}
_flood_store: Dict[Tuple[int, int], list] = defaultdict(list)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _is_chat_admin(bot: Any, chat_id: int, user_id: int) -> bool:
    """Return True if user_id has admin rights in chat_id."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except TelegramError:
        return False


async def _get_bot_member(bot: Any, chat_id: int) -> ChatMember | None:
    """Return the bot's own ChatMember object, or None on error."""
    try:
        me = await bot.get_me()
        return await bot.get_chat_member(chat_id, me.id)
    except TelegramError:
        return None


def _func_name(func: Callable) -> str:
    return getattr(func, "__name__", repr(func))


# ── Decorators ────────────────────────────────────────────────────────────────

def admin_only(func: Handler) -> Handler:
    """Allow only group admins (or sudo users) to run the command."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return

        if user.id in SUDO_USERS:
            return await func(update, context, *args, **kwargs)

        if not await _is_chat_admin(context.bot, chat.id, user.id):
            await update.effective_message.reply_text(
                "🚫 This command is reserved for group administrators."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def owner_only(func: Handler) -> Handler:
    """Allow only the bot owner (OWNER_ID) to run the command."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or user.id != OWNER_ID:
            await update.effective_message.reply_text(
                "🚫 This command is restricted to the bot owner."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def sudo_only(func: Handler) -> Handler:
    """Allow only SUDO_USERS (which includes the owner) to run the command."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or user.id not in SUDO_USERS:
            await update.effective_message.reply_text(
                "🚫 This command is restricted to sudo users."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def group_only(func: Handler) -> Handler:
    """Ensure the command is used inside a group or supergroup."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat = update.effective_chat
        if chat is None or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await update.effective_message.reply_text(
                "⚠️ This command can only be used inside a group."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def private_only(func: Handler) -> Handler:
    """Ensure the command is used in a private (DM) chat."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat = update.effective_chat
        if chat is None or chat.type != ChatType.PRIVATE:
            await update.effective_message.reply_text(
                "⚠️ This command can only be used in a private chat with me."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def bot_admin_required(func: Handler) -> Handler:
    """Ensure the bot itself has admin rights before executing."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat = update.effective_chat
        if chat is None:
            return

        bot_member = await _get_bot_member(context.bot, chat.id)
        if bot_member is None or bot_member.status not in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            await update.effective_message.reply_text(
                "❌ I need to be an administrator to perform this action."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def can_restrict_members(func: Handler) -> Handler:
    """Ensure the bot has the 'can_restrict_members' permission."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat = update.effective_chat
        if chat is None:
            return

        bot_member = await _get_bot_member(context.bot, chat.id)
        if (
            bot_member is None
            or not getattr(bot_member, "can_restrict_members", False)
        ):
            await update.effective_message.reply_text(
                "❌ I need the **Restrict Members** permission to do that.",
                parse_mode="Markdown",
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def can_delete_messages(func: Handler) -> Handler:
    """Ensure the bot has the 'can_delete_messages' permission."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat = update.effective_chat
        if chat is None:
            return

        bot_member = await _get_bot_member(context.bot, chat.id)
        if (
            bot_member is None
            or not getattr(bot_member, "can_delete_messages", False)
        ):
            await update.effective_message.reply_text(
                "❌ I need the **Delete Messages** permission to do that.",
                parse_mode="Markdown",
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def maintenance_check(func: Handler) -> Handler:
    """Block non-sudo users when global MAINTENANCE_MODE is active."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # Re-read at call time so runtime toggles are respected
        from bot.config import MAINTENANCE_MODE as _MM  # noqa: PLC0415

        user = update.effective_user
        if _MM and (user is None or user.id not in SUDO_USERS):
            await update.effective_message.reply_text(
                "🔧 The bot is currently under maintenance. Please try again later."
            )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def rate_limit(seconds: float = 1.0):
    """
    Rate-limit a command per user.

    Usage::

        @rate_limit(seconds=5)
        async def my_handler(update, context): ...
    """

    def decorator(func: Handler) -> Handler:
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user = update.effective_user
            if user is None:
                return await func(update, context, *args, **kwargs)

            # Sudo users bypass rate limits
            if user.id in SUDO_USERS:
                return await func(update, context, *args, **kwargs)

            key = (user.id, _func_name(func))
            last_call = _rate_store.get(key, 0.0)
            now = time.monotonic()
            elapsed = now - last_call

            if elapsed < seconds:
                remaining = seconds - elapsed
                await update.effective_message.reply_text(
                    f"⏳ Slow down! Try again in {remaining:.1f}s."
                )
                return

            _rate_store[key] = now
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator


def log_action(action: str = ""):
    """
    Log a moderation action to LOG_CHANNEL_ID after the handler succeeds.

    Usage::

        @log_action("ban")
        async def ban_handler(update, context): ...
    """

    def decorator(func: Handler) -> Handler:
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            result = await func(update, context, *args, **kwargs)

            if not LOG_CHANNEL_ID:
                return result

            user = update.effective_user
            chat = update.effective_chat
            label = action or _func_name(func)

            if user and chat:
                try:
                    await context.bot.send_message(
                        chat_id=LOG_CHANNEL_ID,
                        text=(
                            f"📋 <b>Action:</b> {label}\n"
                            f"👤 <b>By:</b> <a href='tg://user?id={user.id}'>"
                            f"{user.full_name}</a> (<code>{user.id}</code>)\n"
                            f"💬 <b>Chat:</b> {chat.title or 'Private'} "
                            f"(<code>{chat.id}</code>)\n"
                            f"🕐 <b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                        ),
                        parse_mode="HTML",
                    )
                except TelegramError as exc:
                    logger.warning("Could not log action '%s' to channel: %s", label, exc)

            return result

        return wrapper

    return decorator


def antiflood_check(func: Handler) -> Handler:
    """
    Per-group flood detection.  Uses the group's configured antiflood_limit
    and antiflood_time from bot.config (global defaults).  Per-group
    settings can be passed via context.chat_data if available.
    """

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        from bot.config import ANTIFLOOD_LIMIT, ANTIFLOOD_TIME  # noqa: PLC0415

        user = update.effective_user
        chat = update.effective_chat

        if user is None or chat is None:
            return await func(update, context, *args, **kwargs)

        if user.id in SUDO_USERS:
            return await func(update, context, *args, **kwargs)

        # Retrieve per-group overrides when available
        limit: int = ANTIFLOOD_LIMIT
        window: int = ANTIFLOOD_TIME

        if context.chat_data:
            limit = int(context.chat_data.get("antiflood_limit", limit))
            window = int(context.chat_data.get("antiflood_time", window))

        key = (chat.id, user.id)
        now = time.monotonic()

        # Prune timestamps outside the window
        timestamps = _flood_store[key]
        _flood_store[key] = [t for t in timestamps if now - t < window]
        _flood_store[key].append(now)

        if len(_flood_store[key]) > limit:
            logger.info(
                "Flood detected: user=%s chat=%s (%d msgs/%ds)",
                user.id, chat.id, len(_flood_store[key]), window,
            )
            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=__import__(
                        "telegram", fromlist=["ChatPermissions"]
                    ).ChatPermissions(can_send_messages=False),
                    until_date=int(time.time()) + 60,
                )
                await update.effective_message.reply_text(
                    f"🚨 {user.mention_html()} has been muted for 1 minute due to flooding.",
                    parse_mode="HTML",
                )
            except TelegramError as exc:
                logger.warning("Could not mute flooder %s: %s", user.id, exc)
            return  # Do not process the flooded message

        return await func(update, context, *args, **kwargs)

    return wrapper
