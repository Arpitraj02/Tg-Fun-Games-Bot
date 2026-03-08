"""
bot/config.py
─────────────
Central configuration loader.  All settings are read from environment
variables (populated from .env by python-dotenv).  Every attribute is
type-annotated so the rest of the codebase can rely on the types.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final, List

from dotenv import load_dotenv

# ── Load .env file ────────────────────────────────────────────────────────────
BASE_DIR: Final[Path] = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _require(key: str) -> str:
    """Return the value of a required env-var or abort with a clear message."""
    value = os.getenv(key)
    if not value:
        sys.exit(f"[config] FATAL – required environment variable '{key}' is not set.")
    return value


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _list(key: str, default: str = "") -> List[int]:
    raw = os.getenv(key, default).strip()
    if not raw:
        return []
    parts = raw.replace(",", " ").split()
    result: List[int] = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            continue
    return result


# ── Core ──────────────────────────────────────────────────────────────────────
BOT_TOKEN: Final[str] = _require("BOT_TOKEN")
OWNER_ID: Final[int] = int(_require("OWNER_ID"))
SUDO_USERS: Final[List[int]] = list(set(_list("SUDO_USERS") + [OWNER_ID]))
BOT_USERNAME: Final[str] = os.getenv("BOT_USERNAME", "")
SUPPORT_CHAT: Final[str] = os.getenv("SUPPORT_CHAT", "")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL: Final[str] = os.getenv(
    "DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'bot.db'}"
)

# ── Redis ─────────────────────────────────────────────────────────────────────
REDIS_URL: Final[str] = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY: Final[str] = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: Final[str] = os.getenv("OPENAI_MODEL", "gpt-4-turbo-preview")
OPENAI_MAX_TOKENS: Final[int] = _int("OPENAI_MAX_TOKENS", 1024)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_CHANNEL_ID: Final[int] = _int("LOG_CHANNEL_ID", 0)
LOG_LEVEL: Final[str] = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE: Final[str] = os.getenv("LOG_FILE", "")

# ── Bot Behaviour ─────────────────────────────────────────────────────────────
MAINTENANCE_MODE: bool = _bool("MAINTENANCE_MODE", False)
MAX_WARN_LIMIT: Final[int] = _int("MAX_WARN_LIMIT", 3)
WARN_MODE: Final[str] = os.getenv("WARN_MODE", "kick").lower()  # ban|kick|mute|none
DEFAULT_LANGUAGE: Final[str] = os.getenv("DEFAULT_LANGUAGE", "en")

# Anti-flood
ANTIFLOOD_LIMIT: Final[int] = _int("ANTIFLOOD_LIMIT", 5)
ANTIFLOOD_TIME: Final[int] = _int("ANTIFLOOD_TIME", 5)

# Anti-raid
ANTIRAID_LIMIT: Final[int] = _int("ANTIRAID_LIMIT", 10)
ANTIRAID_TIME: Final[int] = _int("ANTIRAID_TIME", 60)

# ── Economy ───────────────────────────────────────────────────────────────────
DAILY_COINS: Final[int] = _int("DAILY_COINS", 500)
WEEKLY_COINS: Final[int] = _int("WEEKLY_COINS", 2500)
WORK_COINS_MIN: Final[int] = _int("WORK_COINS_MIN", 100)
WORK_COINS_MAX: Final[int] = _int("WORK_COINS_MAX", 500)
CRIME_COINS_MIN: Final[int] = _int("CRIME_COINS_MIN", 200)
CRIME_COINS_MAX: Final[int] = _int("CRIME_COINS_MAX", 1000)
CRIME_FAIL_CHANCE: Final[int] = _int("CRIME_FAIL_CHANCE", 40)

# ── Cooldowns (seconds) ───────────────────────────────────────────────────────
DAILY_COOLDOWN: Final[int] = _int("DAILY_COOLDOWN", 86_400)
WEEKLY_COOLDOWN: Final[int] = _int("WEEKLY_COOLDOWN", 604_800)
WORK_COOLDOWN: Final[int] = _int("WORK_COOLDOWN", 3_600)
CRIME_COOLDOWN: Final[int] = _int("CRIME_COOLDOWN", 7_200)
REP_COOLDOWN: Final[int] = _int("REP_COOLDOWN", 86_400)

# ── Games ─────────────────────────────────────────────────────────────────────
GAME_TIMEOUT: Final[int] = _int("GAME_TIMEOUT", 30)
TRIVIA_POINTS: Final[int] = _int("TRIVIA_POINTS", 10)
WORD_GAME_POINTS: Final[int] = _int("WORD_GAME_POINTS", 5)

# ── Rate Limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT: Final[float] = _float("RATE_LIMIT", 1.0)

# ── Webhook ───────────────────────────────────────────────────────────────────
WEBHOOK_URL: Final[str] = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PORT: Final[int] = _int("WEBHOOK_PORT", 8443)
WEBHOOK_SECRET_TOKEN: Final[str] = os.getenv("WEBHOOK_SECRET_TOKEN", "")

# ── Sentry ────────────────────────────────────────────────────────────────────
SENTRY_DSN: Final[str] = os.getenv("SENTRY_DSN", "")

# ── NSFW Detection ────────────────────────────────────────────────────────────
NSFW_API_URL: Final[str] = os.getenv("NSFW_API_URL", "")
NSFW_API_KEY: Final[str] = os.getenv("NSFW_API_KEY", "")

# ── Sticker Packs ─────────────────────────────────────────────────────────────
STICKER_BOT: Final[str] = os.getenv("STICKER_BOT", "@Stickers")
MAX_STICKERS_PER_PACK: Final[int] = _int("MAX_STICKERS_PER_PACK", 120)

# ── Media ─────────────────────────────────────────────────────────────────────
MAX_FILE_SIZE_MB: Final[int] = _int("MAX_FILE_SIZE_MB", 50)
MAX_FILE_SIZE_BYTES: Final[int] = MAX_FILE_SIZE_MB * 1024 * 1024

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR: Final[Path] = BASE_DIR / "data"
LOGS_DIR: Final[Path] = BASE_DIR / "logs"
DOWNLOADS_DIR: Final[Path] = BASE_DIR / "downloads"
GENERATED_DIR: Final[Path] = BASE_DIR / "generated"

# Ensure runtime directories exist
for _dir in (DATA_DIR, LOGS_DIR, DOWNLOADS_DIR, GENERATED_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


def is_sudo(user_id: int) -> bool:
    """Return True if the user is the owner or in SUDO_USERS."""
    return user_id in SUDO_USERS


def is_owner(user_id: int) -> bool:
    """Return True if the user is the bot owner."""
    return user_id == OWNER_ID
