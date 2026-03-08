"""
bot/helpers/utils.py
────────────────────
General-purpose async utilities used across all bot modules.
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
import string
import time
from typing import Dict, List, Optional, Tuple

from telegram import Bot, Message, Update, User
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.config import OWNER_ID, SUDO_USERS

logger = logging.getLogger(__name__)

# ── In-memory admin cache ─────────────────────────────────────────────────────
# {chat_id: (timestamp, [user_id, ...])}
_admin_cache: Dict[int, Tuple[float, List[int]]] = {}
_ADMIN_CACHE_TTL = 300  # 5 minutes


# ── User / Target extraction ──────────────────────────────────────────────────

async def extract_user_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Determine the target user from:
      1. A reply to another message.
      2. A numeric ID or @username passed as the first argument.
      3. Fallback to the message author.

    Returns:
        (user_id, error_message)  — error_message is None on success.
    """
    message: Optional[Message] = update.effective_message
    if message is None:
        return None, "No message found."

    # 1. Reply target
    if message.reply_to_message:
        replied_user = message.reply_to_message.from_user
        if replied_user:
            return replied_user.id, None

    # 2. First argument
    if context.args:
        arg = context.args[0].lstrip("@")

        # Numeric ID
        if arg.lstrip("-").isdigit():
            return int(arg), None

        # Username
        try:
            chat = await context.bot.get_chat(f"@{arg}")
            return chat.id, None
        except TelegramError as exc:
            return None, f"Could not find user '@{arg}': {exc}"

    # 3. The sender themselves
    user = update.effective_user
    if user:
        return user.id, None

    return None, "Could not determine the target user."


async def extract_user_and_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    Like extract_user_id but also returns the remainder of args as a reason.

    Returns:
        (user_id, reason, error_message)
    """
    user_id, err = await extract_user_id(update, context)
    if err:
        return None, None, err

    # If we resolved via reply, all args are the reason
    message = update.effective_message
    reply = message.reply_to_message if message else None

    if reply and reply.from_user and reply.from_user.id == user_id:
        reason = " ".join(context.args) if context.args else None
    else:
        # First arg was the user; rest is reason
        reason = " ".join(context.args[1:]) if context.args and len(context.args) > 1 else None

    return user_id, reason or None, None


# ── Admin helpers ─────────────────────────────────────────────────────────────

async def get_admin_list(bot: Bot, chat_id: int, force_refresh: bool = False) -> List[int]:
    """
    Return a cached list of admin user_ids for a chat.
    Re-fetches from the API if the cache has expired or force_refresh=True.
    """
    now = time.monotonic()
    cached = _admin_cache.get(chat_id)
    if cached and not force_refresh:
        ts, admin_ids = cached
        if now - ts < _ADMIN_CACHE_TTL:
            return admin_ids

    try:
        admins = await bot.get_chat_administrators(chat_id)
        admin_ids = [m.user.id for m in admins]
        _admin_cache[chat_id] = (now, admin_ids)
        return admin_ids
    except TelegramError as exc:
        logger.warning("Could not fetch admin list for %s: %s", chat_id, exc)
        return cached[1] if cached else []


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Return True if user_id is an admin (or owner) of chat_id."""
    if user_id in SUDO_USERS:
        return True
    admin_ids = await get_admin_list(bot, chat_id)
    return user_id in admin_ids


def is_owner(user_id: int) -> bool:
    """Return True if user_id is the bot owner."""
    return user_id == OWNER_ID


def invalidate_admin_cache(chat_id: int) -> None:
    """Force the next admin lookup for this chat to re-fetch from the API."""
    _admin_cache.pop(chat_id, None)


# ── Time parsing ──────────────────────────────────────────────────────────────

_TIME_PATTERN = re.compile(
    r"(\d+)\s*(s|sec|second|seconds|"
    r"m|min|minute|minutes|"
    r"h|hr|hour|hours|"
    r"d|day|days|"
    r"w|week|weeks)",
    re.IGNORECASE,
)

_TIME_UNITS: Dict[str, int] = {
    "s": 1,       "sec": 1,     "second": 1,  "seconds": 1,
    "m": 60,      "min": 60,    "minute": 60, "minutes": 60,
    "h": 3_600,   "hr": 3_600,  "hour": 3_600,"hours": 3_600,
    "d": 86_400,  "day": 86_400,"days": 86_400,
    "w": 604_800, "week": 604_800, "weeks": 604_800,
}


def parse_time(time_str: str) -> Optional[int]:
    """
    Parse a human-readable time string into seconds.

    Examples:
        parse_time("5m")   → 300
        parse_time("2h")   → 7200
        parse_time("1d")   → 86400
        parse_time("30s")  → 30
        parse_time("bad")  → None
    """
    if not time_str:
        return None

    total = 0
    matches = _TIME_PATTERN.findall(time_str.strip())
    if not matches:
        return None

    for amount_str, unit in matches:
        multiplier = _TIME_UNITS.get(unit.lower())
        if multiplier is None:
            continue
        total += int(amount_str) * multiplier

    return total if total > 0 else None


def get_readable_time(seconds: int | float) -> str:
    """Convert seconds to a compact human-readable string (delegates to formatters)."""
    from bot.helpers.formatters import format_time  # local import to avoid circular deps
    return format_time(int(seconds))


# ── Anti-spam ─────────────────────────────────────────────────────────────────

# Simple in-memory store: {(user_id, chat_id): [(timestamp, text_hash), ...]}
_spam_store: Dict[Tuple[int, int], List[Tuple[float, str]]] = {}
_SPAM_WINDOW = 10   # seconds
_SPAM_THRESHOLD = 5 # same message repeated this many times in the window


def anti_spam_check(user_id: int, chat_id: int, message_text: str) -> bool:
    """
    Basic anti-spam detector based on repeated messages.

    Returns:
        True  if the message looks like spam.
        False otherwise.
    """
    if user_id in SUDO_USERS:
        return False

    now = time.monotonic()
    key = (user_id, chat_id)
    msg_hash = hashlib.sha256(message_text.lower().strip().encode()).hexdigest()

    history = _spam_store.get(key, [])
    # Prune old entries
    history = [(ts, h) for ts, h in history if now - ts < _SPAM_WINDOW]
    history.append((now, msg_hash))
    _spam_store[key] = history

    same_count = sum(1 for _, h in history if h == msg_hash)
    return same_count >= _SPAM_THRESHOLD


# ── String helpers ────────────────────────────────────────────────────────────

def generate_random_string(length: int = 8) -> str:
    """Generate a random alphanumeric string of given length."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def generate_token(length: int = 32) -> str:
    """Generate a cryptographically suitable random hex token."""
    import secrets
    return secrets.token_hex(length // 2)


def sanitize_filename(name: str, max_len: int = 64) -> str:
    """Remove path-unsafe characters and truncate a filename."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name[:max_len]


def split_text(text: str, max_length: int = 4096) -> List[str]:
    """
    Split a long text into chunks of at most `max_length` characters,
    trying to break on newlines where possible.
    """
    if len(text) <= max_length:
        return [text]

    chunks: List[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ── File / Media helpers ──────────────────────────────────────────────────────

def get_file_type(message: Message) -> Optional[str]:
    """
    Detect the primary media type of a Telegram Message.

    Returns:
        One of: 'photo', 'video', 'audio', 'voice', 'document',
                'sticker', 'animation', 'video_note', 'text', None.
    """
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if message.voice:
        return "voice"
    if message.document:
        return "document"
    if message.sticker:
        return "sticker"
    if message.animation:
        return "animation"
    if message.video_note:
        return "video_note"
    if message.text:
        return "text"
    return None


def get_file_id(message: Message) -> Optional[str]:
    """
    Extract the file_id from a media message.
    Returns None for pure text messages.
    """
    mapping = {
        "photo": lambda m: m.photo[-1].file_id if m.photo else None,
        "video": lambda m: m.video.file_id if m.video else None,
        "audio": lambda m: m.audio.file_id if m.audio else None,
        "voice": lambda m: m.voice.file_id if m.voice else None,
        "document": lambda m: m.document.file_id if m.document else None,
        "sticker": lambda m: m.sticker.file_id if m.sticker else None,
        "animation": lambda m: m.animation.file_id if m.animation else None,
        "video_note": lambda m: m.video_note.file_id if m.video_note else None,
    }
    for ftype, getter in mapping.items():
        fid = getter(message)
        if fid:
            return fid
    return None


# ── Mention helper ────────────────────────────────────────────────────────────

def mention_html(user_id: int, name: str) -> str:
    """Return an HTML inline mention for a user."""
    safe = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'


# ── Misc ──────────────────────────────────────────────────────────────────────

def chunks(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value to [min_val, max_val]."""
    return max(min_val, min(max_val, value))
