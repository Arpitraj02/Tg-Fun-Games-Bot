"""
bot/helpers/formatters.py
─────────────────────────
HTML formatting helpers, human-readable time/number formatters, and
Telegram-entity builders used across the entire bot.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence

from telegram import Chat, User


# ── Basic HTML entity wrappers ────────────────────────────────────────────────

def bold(text: str) -> str:
    """Wrap text in HTML bold tags."""
    return f"<b>{text}</b>"


def italic(text: str) -> str:
    """Wrap text in HTML italic tags."""
    return f"<i>{text}</i>"


def code(text: str) -> str:
    """Wrap text in HTML code tags."""
    return f"<code>{text}</code>"


def pre(text: str, language: str = "") -> str:
    """Wrap text in HTML pre block, optionally specifying a language."""
    if language:
        return f'<pre><code class="language-{language}">{text}</code></pre>'
    return f"<pre>{text}</pre>"


def underline(text: str) -> str:
    """Wrap text in HTML underline tags."""
    return f"<u>{text}</u>"


def strikethrough(text: str) -> str:
    """Wrap text in HTML strikethrough tags."""
    return f"<s>{text}</s>"


def spoiler(text: str) -> str:
    """Wrap text in Telegram spoiler tags."""
    return f"<tg-spoiler>{text}</tg-spoiler>"


def link(text: str, url: str) -> str:
    """Create an HTML hyperlink."""
    return f'<a href="{url}">{text}</a>'


def user_mention(user_id: int, name: str) -> str:
    """Create an inline tg://user mention using HTML."""
    safe_name = name.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def mention_html(user_id: int, name: str) -> str:
    """Alias for user_mention, kept for backward compatibility."""
    return user_mention(user_id, name)


# ── Numeric / Time formatters ─────────────────────────────────────────────────

def format_number(n: int | float) -> str:
    """Format a large number with thousands separators: 1234567 → '1,234,567'."""
    return f"{n:,}"


def format_time(seconds: int | float) -> str:
    """
    Convert a raw seconds count into a human-readable duration string.

    Examples:
        format_time(90)    → "1m 30s"
        format_time(3661)  → "1h 1m 1s"
        format_time(86400) → "1d"
    """
    seconds = int(seconds)
    if seconds < 0:
        return "0s"

    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, secs = divmod(remainder, 60)

    parts: List[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


def get_readable_time(seconds: int | float) -> str:
    """Alias for format_time used by utils.py."""
    return format_time(seconds)


def format_datetime(dt: datetime, tz_name: str = "UTC") -> str:
    """Format a datetime into a human-readable string with timezone label."""
    return dt.strftime(f"%Y-%m-%d %H:%M:%S {tz_name}")


def time_ago(dt: datetime) -> str:
    """Return a natural-language 'X ago' string relative to now (UTC)."""
    now = datetime.now(tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())

    if secs < 60:
        return f"{secs}s ago"
    if secs < 3_600:
        return f"{secs // 60}m ago"
    if secs < 86_400:
        return f"{secs // 3_600}h ago"
    if secs < 2_592_000:
        return f"{secs // 86_400}d ago"
    if secs < 31_536_000:
        return f"{secs // 2_592_000}mo ago"
    return f"{secs // 31_536_000}y ago"


def format_size(size_bytes: int) -> str:
    """Convert a byte count to a human-readable file size (e.g. '4.2 MB')."""
    if size_bytes == 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    i = min(i, len(units) - 1)
    value = size_bytes / (1024 ** i)
    return f"{value:.2f} {units[i]}"


# ── Visual components ─────────────────────────────────────────────────────────

def progress_bar(value: int | float, max_val: int | float, length: int = 10) -> str:
    """
    Render a Unicode block progress bar.

    Example:
        progress_bar(3, 10, 10) → "███░░░░░░░"
    """
    if max_val <= 0:
        return "░" * length
    ratio = max(0.0, min(1.0, value / max_val))
    filled = round(ratio * length)
    return "█" * filled + "░" * (length - filled)


def xp_bar(xp: int, level: int, length: int = 10) -> str:
    """XP progress bar for the current level (each level needs level*100 XP)."""
    required = level * 100
    current_xp = xp % required if required else 0
    return progress_bar(current_xp, required, length)


def level_up_threshold(level: int) -> int:
    """Return the XP needed to reach the next level."""
    return level * 100


# ── Compound formatters ───────────────────────────────────────────────────────

def format_user_info(
    user: User,
    chat_member: Any = None,
    extra: dict | None = None,
) -> str:
    """
    Build an HTML-formatted user information card.

    Args:
        user: telegram.User object.
        chat_member: Optional telegram.ChatMember for status info.
        extra: Optional dict with keys like 'coins', 'level', 'xp', 'warnings'.
    """
    extra = extra or {}
    lines: List[str] = [
        bold("👤 User Information"),
        "",
        f"• {bold('Name:')} {user_mention(user.id, user.full_name)}",
        f"• {bold('ID:')} {code(str(user.id))}",
    ]

    if user.username:
        lines.append(f"• {bold('Username:')} @{user.username}")

    if user.language_code:
        lines.append(f"• {bold('Language:')} {user.language_code}")

    if chat_member:
        status_map = {
            "creator": "👑 Owner",
            "administrator": "⭐ Admin",
            "member": "👤 Member",
            "restricted": "🔇 Restricted",
            "left": "👻 Left",
            "kicked": "🚫 Banned",
        }
        status = status_map.get(chat_member.status, chat_member.status)
        lines.append(f"• {bold('Status:')} {status}")

    if "warnings" in extra:
        lines.append(f"• {bold('Warnings:')} {extra['warnings']}")

    if "level" in extra:
        xp = extra.get("xp", 0)
        lvl = extra.get("level", 1)
        bar = xp_bar(xp, lvl)
        lines.append(f"• {bold('Level:')} {lvl}  {bar}")

    if "coins" in extra:
        lines.append(f"• {bold('Coins:')} 💰 {format_number(extra['coins'])}")

    if "reputation" in extra:
        lines.append(f"• {bold('Reputation:')} ⭐ {format_number(extra['reputation'])}")

    return "\n".join(lines)


def format_group_info(chat: Chat, extra: dict | None = None) -> str:
    """
    Build an HTML-formatted group information card.

    Args:
        chat: telegram.Chat object.
        extra: Optional dict with keys like 'members', 'admins', 'messages'.
    """
    extra = extra or {}
    chat_type = chat.type.capitalize() if chat.type else "Unknown"

    lines: List[str] = [
        bold("💬 Group Information"),
        "",
        f"• {bold('Title:')} {chat.title or 'N/A'}",
        f"• {bold('ID:')} {code(str(chat.id))}",
        f"• {bold('Type:')} {chat_type}",
    ]

    if chat.username:
        lines.append(f"• {bold('Username:')} @{chat.username}")

    if chat.description:
        desc = chat.description[:200] + ("…" if len(chat.description) > 200 else "")
        lines.append(f"• {bold('Description:')} {desc}")

    if "members" in extra:
        lines.append(f"• {bold('Members:')} {format_number(extra['members'])}")

    if "admins" in extra:
        lines.append(f"• {bold('Admins:')} {extra['admins']}")

    if "messages" in extra:
        lines.append(f"• {bold('Total Messages:')} {format_number(extra['messages'])}")

    if "language" in extra:
        lines.append(f"• {bold('Language:')} {extra['language']}")

    return "\n".join(lines)


# ── Pagination helpers ─────────────────────────────────────────────────────────

def paginate_items(items: Sequence[Any], page: int = 0, items_per_page: int = 10):
    """
    Slice a sequence into pages.

    Returns:
        (page_items, total_pages)
    """
    total = len(items)
    total_pages = max(1, math.ceil(total / items_per_page))
    page = max(0, min(page, total_pages - 1))
    start = page * items_per_page
    end = start + items_per_page
    return list(items[start:end]), total_pages


def paginate_text(
    text: str,
    items_per_page: int = 10,
    separator: str = "\n",
) -> List[str]:
    """
    Split a newline-separated text blob into pages of at most
    `items_per_page` lines each.

    Returns a list of page strings.
    """
    lines = text.split(separator)
    pages: List[str] = []
    for i in range(0, max(1, len(lines)), items_per_page):
        pages.append(separator.join(lines[i : i + items_per_page]))
    return pages


def format_list(
    items: Sequence[str],
    numbered: bool = False,
    bullet: str = "•",
) -> str:
    """Format a sequence as a bulleted or numbered HTML list."""
    if not items:
        return italic("(empty)")
    if numbered:
        return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(items))
    return "\n".join(f"{bullet} {item}" for item in items)


def truncate(text: str, max_len: int = 200, suffix: str = "…") -> str:
    """Truncate text to max_len characters, appending suffix if truncated."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def escape_html(text: str) -> str:
    """Escape characters that have special meaning in Telegram HTML mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
