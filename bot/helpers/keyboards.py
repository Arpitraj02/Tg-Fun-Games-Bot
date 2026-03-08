"""
bot/helpers/keyboards.py
────────────────────────
Reusable InlineKeyboard builder functions.
Every function returns a telegram.InlineKeyboardMarkup.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(*buttons: InlineKeyboardButton) -> List[InlineKeyboardButton]:
    return list(buttons)


def _btn(text: str, callback_data: str = "", url: str = "") -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=callback_data)


# ── Public keyboard builders ──────────────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Main menu shown via /start in private chat."""
    return InlineKeyboardMarkup(
        [
            _row(
                _btn("📚 Help", callback_data="help:0"),
                _btn("⚙️ Settings", callback_data="settings:main"),
            ),
            _row(
                _btn("🎮 Games", callback_data="games:menu"),
                _btn("💰 Economy", callback_data="economy:menu"),
            ),
            _row(
                _btn("🛡️ Moderation", callback_data="mod:menu"),
                _btn("📊 Stats", callback_data="stats:menu"),
            ),
            _row(
                _btn("🌐 Support", url="https://t.me/support"),
                _btn("📢 Channel", url="https://t.me/channel"),
            ),
        ]
    )


def help_keyboard(page: int = 0, total_pages: int = 10) -> InlineKeyboardMarkup:
    """Paginated help menu."""
    nav_row: List[InlineKeyboardButton] = []

    if page > 0:
        nav_row.append(_btn("◀️ Prev", callback_data=f"help:{page - 1}"))

    nav_row.append(_btn(f"📄 {page + 1}/{total_pages}", callback_data="help:noop"))

    if page < total_pages - 1:
        nav_row.append(_btn("Next ▶️", callback_data=f"help:{page + 1}"))

    return InlineKeyboardMarkup(
        [
            _row(
                _btn("🛡️ Admin", callback_data="help:admin"),
                _btn("🔧 Filters", callback_data="help:filters"),
            ),
            _row(
                _btn("📝 Notes", callback_data="help:notes"),
                _btn("🚫 Blacklist", callback_data="help:blacklist"),
            ),
            _row(
                _btn("💰 Economy", callback_data="help:economy"),
                _btn("🎮 Games", callback_data="help:games"),
            ),
            _row(
                _btn("🤖 AI", callback_data="help:ai"),
                _btn("🔔 Reminders", callback_data="help:reminders"),
            ),
            nav_row,
            _row(_btn("🏠 Main Menu", callback_data="start:menu")),
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    """Administration actions overview."""
    return InlineKeyboardMarkup(
        [
            _row(
                _btn("🔨 Ban", callback_data="admin:ban"),
                _btn("👢 Kick", callback_data="admin:kick"),
                _btn("🔇 Mute", callback_data="admin:mute"),
            ),
            _row(
                _btn("⚠️ Warn", callback_data="admin:warn"),
                _btn("📌 Pin", callback_data="admin:pin"),
                _btn("🗑️ Purge", callback_data="admin:purge"),
            ),
            _row(
                _btn("📋 Logs", callback_data="admin:logs"),
                _btn("🛡️ Fed", callback_data="admin:fed"),
            ),
            _row(_btn("🔙 Back", callback_data="start:menu")),
        ]
    )


def moderation_keyboard() -> InlineKeyboardMarkup:
    """Quick-access moderation panel."""
    return InlineKeyboardMarkup(
        [
            _row(
                _btn("🔗 Anti-Link", callback_data="mod:antilink"),
                _btn("⏩ Anti-Forward", callback_data="mod:antiforward"),
            ),
            _row(
                _btn("🔞 Anti-NSFW", callback_data="mod:antinsfw"),
                _btn("🌊 Anti-Flood", callback_data="mod:antiflood"),
            ),
            _row(
                _btn("🛡️ Anti-Raid", callback_data="mod:antiraid"),
                _btn("🤖 Captcha", callback_data="mod:captcha"),
            ),
            _row(
                _btn("📝 Rules", callback_data="mod:rules"),
                _btn("🚫 Blacklist", callback_data="mod:blacklist"),
            ),
            _row(_btn("🔙 Back", callback_data="start:menu")),
        ]
    )


def economy_keyboard() -> InlineKeyboardMarkup:
    """Economy system actions."""
    return InlineKeyboardMarkup(
        [
            _row(
                _btn("👜 Wallet", callback_data="eco:wallet"),
                _btn("🏦 Bank", callback_data="eco:bank"),
            ),
            _row(
                _btn("📅 Daily", callback_data="eco:daily"),
                _btn("📆 Weekly", callback_data="eco:weekly"),
            ),
            _row(
                _btn("💼 Work", callback_data="eco:work"),
                _btn("🦹 Crime", callback_data="eco:crime"),
            ),
            _row(
                _btn("🏪 Shop", callback_data="eco:shop"),
                _btn("🎰 Gamble", callback_data="eco:gamble"),
            ),
            _row(
                _btn("🏆 Leaderboard", callback_data="eco:leaderboard"),
                _btn("💸 Transfer", callback_data="eco:transfer"),
            ),
            _row(_btn("🔙 Back", callback_data="start:menu")),
        ]
    )


def games_keyboard() -> InlineKeyboardMarkup:
    """Available in-group games."""
    return InlineKeyboardMarkup(
        [
            _row(
                _btn("❓ Trivia", callback_data="game:trivia"),
                _btn("🔤 Word Chain", callback_data="game:wordchain"),
            ),
            _row(
                _btn("🎲 Dice", callback_data="game:dice"),
                _btn("✂️ RPS", callback_data="game:rps"),
            ),
            _row(
                _btn("🔢 Math Quiz", callback_data="game:math"),
                _btn("🃏 Blackjack", callback_data="game:blackjack"),
            ),
            _row(
                _btn("🐍 Snake", callback_data="game:snake"),
                _btn("💣 Minesweeper", callback_data="game:minesweeper"),
            ),
            _row(_btn("🔙 Back", callback_data="start:menu")),
        ]
    )


def confirm_keyboard(action: str, data: str) -> InlineKeyboardMarkup:
    """Generic yes/no confirmation dialog."""
    return InlineKeyboardMarkup(
        [
            _row(
                _btn("✅ Confirm", callback_data=f"confirm:{action}:{data}"),
                _btn("❌ Cancel", callback_data=f"cancel:{action}:{data}"),
            )
        ]
    )


def pagination_keyboard(
    page: int,
    total: int,
    callback_prefix: str,
) -> InlineKeyboardMarkup:
    """
    Generic pagination row.

    Args:
        page: Current page (0-indexed).
        total: Total number of pages.
        callback_prefix: e.g. "notes" → callbacks become "notes:page:0"
    """
    row: List[InlineKeyboardButton] = []

    if page > 0:
        row.append(_btn("◀️", callback_data=f"{callback_prefix}:page:{page - 1}"))

    row.append(_btn(f"{page + 1}/{total}", callback_data=f"{callback_prefix}:noop"))

    if page < total - 1:
        row.append(_btn("▶️", callback_data=f"{callback_prefix}:page:{page + 1}"))

    return InlineKeyboardMarkup([row])


def back_keyboard(callback: str) -> InlineKeyboardMarkup:
    """Single back button."""
    return InlineKeyboardMarkup([[_btn("🔙 Back", callback_data=callback)]])


def yes_no_keyboard(
    callback_yes: str,
    callback_no: str,
) -> InlineKeyboardMarkup:
    """Simple yes / no keyboard."""
    return InlineKeyboardMarkup(
        [_row(_btn("✅ Yes", callback_data=callback_yes), _btn("❌ No", callback_data=callback_no))]
    )


def warn_keyboard(user_id: int, chat_id: int) -> InlineKeyboardMarkup:
    """Inline actions attached to a warning message."""
    return InlineKeyboardMarkup(
        [
            _row(
                _btn("🔨 Ban", callback_data=f"warn_action:ban:{user_id}:{chat_id}"),
                _btn("👢 Kick", callback_data=f"warn_action:kick:{user_id}:{chat_id}"),
                _btn("🔇 Mute", callback_data=f"warn_action:mute:{user_id}:{chat_id}"),
            ),
            _row(
                _btn("🗑️ Remove Warn", callback_data=f"warn_action:rm:{user_id}:{chat_id}"),
                _btn("✅ Dismiss", callback_data=f"warn_action:dismiss:{user_id}:{chat_id}"),
            ),
        ]
    )


def settings_keyboard(chat_id: int, settings: dict) -> InlineKeyboardMarkup:
    """
    Dynamic group settings keyboard.
    Each feature shows its current state as ✅/❌.
    """

    def toggle(key: str, label: str, callback: str) -> InlineKeyboardButton:
        icon = "✅" if settings.get(key) else "❌"
        return _btn(f"{icon} {label}", callback_data=f"settings:{callback}:{chat_id}")

    return InlineKeyboardMarkup(
        [
            _row(
                toggle("antilink", "Anti-Link", "antilink"),
                toggle("antiforward", "Anti-Forward", "antiforward"),
            ),
            _row(
                toggle("antinsfw", "Anti-NSFW", "antinsfw"),
                toggle("antiflood", "Anti-Flood", "antiflood"),
            ),
            _row(
                toggle("antiraid", "Anti-Raid", "antiraid"),
                toggle("captcha_enabled", "Captcha", "captcha"),
            ),
            _row(
                toggle("welcome_enabled", "Welcome Msg", "welcome"),
                toggle("goodbye_enabled", "Goodbye Msg", "goodbye"),
            ),
            _row(_btn("🔙 Back", callback_data="start:menu")),
        ]
    )


def report_keyboard(
    reporter_id: int,
    reported_id: int,
    chat_id: int,
    report_id: int,
) -> InlineKeyboardMarkup:
    """Admin action buttons for a submitted report."""
    return InlineKeyboardMarkup(
        [
            _row(
                _btn(
                    "🔨 Ban",
                    callback_data=f"report_action:ban:{reported_id}:{chat_id}:{report_id}",
                ),
                _btn(
                    "👢 Kick",
                    callback_data=f"report_action:kick:{reported_id}:{chat_id}:{report_id}",
                ),
                _btn(
                    "🔇 Mute",
                    callback_data=f"report_action:mute:{reported_id}:{chat_id}:{report_id}",
                ),
            ),
            _row(
                _btn(
                    "⚠️ Warn",
                    callback_data=f"report_action:warn:{reported_id}:{chat_id}:{report_id}",
                ),
                _btn(
                    "✅ Handled",
                    callback_data=f"report_action:handled:{reported_id}:{chat_id}:{report_id}",
                ),
                _btn(
                    "❌ Dismiss",
                    callback_data=f"report_action:dismiss:{reported_id}:{chat_id}:{report_id}",
                ),
            ),
        ]
    )


def profile_keyboard(user_id: int, is_self: bool = False) -> InlineKeyboardMarkup:
    """Inline buttons on a user profile card."""
    rows = [
        _row(
            _btn("🏆 Achievements", callback_data=f"profile:achievements:{user_id}"),
            _btn("📊 Stats", callback_data=f"profile:stats:{user_id}"),
        ),
    ]
    if is_self:
        rows.append(
            _row(
                _btn("✏️ Edit Bio", callback_data=f"profile:editbio:{user_id}"),
                _btn("🖼️ Change Avatar", callback_data=f"profile:avatar:{user_id}"),
            )
        )
    rows.append(_row(_btn("🔙 Back", callback_data="start:menu")))
    return InlineKeyboardMarkup(rows)
