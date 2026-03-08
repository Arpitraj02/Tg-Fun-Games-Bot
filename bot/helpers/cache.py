"""
bot/helpers/cache.py
────────────────────
Redis-backed caching layer built on aioredis (v2+).

The module exposes:
  • Low-level primitives : get_cache / set_cache / delete_cache
  • Domain helpers       : group settings, user data, admin lists
  • Graceful degradation : every function silently returns None / falls through
    when Redis is unavailable, so the bot keeps working without a cache.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import aioredis

from bot.config import REDIS_URL

logger = logging.getLogger(__name__)

# ── Connection pool ───────────────────────────────────────────────────────────

_redis: Optional[aioredis.Redis] = None


async def get_redis() -> Optional[aioredis.Redis]:
    """
    Return a shared Redis connection, creating it on first call.
    Returns None if the connection cannot be established.
    """
    global _redis
    if _redis is not None:
        return _redis
    try:
        _redis = await aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        await _redis.ping()
        logger.info("Redis connection established: %s", REDIS_URL.split("@")[-1])
    except Exception as exc:
        logger.warning("Redis unavailable (%s) — caching disabled.", exc)
        _redis = None
    return _redis


async def close_redis() -> None:
    """Close the Redis connection pool gracefully on shutdown."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis connection closed.")


async def check_redis_connection() -> bool:
    """Return True if Redis is reachable."""
    r = await get_redis()
    if r is None:
        return False
    try:
        await r.ping()
        return True
    except Exception:
        return False


# ── Low-level primitives ──────────────────────────────────────────────────────

async def get_cache(key: str) -> Optional[Any]:
    """
    Retrieve a JSON-decoded value from Redis.
    Returns None if the key does not exist or Redis is unavailable.
    """
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.debug("get_cache(%s) error: %s", key, exc)
        return None


async def set_cache(key: str, value: Any, ttl: int = 300) -> bool:
    """
    JSON-encode and store a value in Redis with an optional TTL (seconds).
    Returns True on success, False otherwise.
    """
    r = await get_redis()
    if r is None:
        return False
    try:
        serialised = json.dumps(value, default=str)
        if ttl and ttl > 0:
            await r.setex(key, ttl, serialised)
        else:
            await r.set(key, serialised)
        return True
    except Exception as exc:
        logger.debug("set_cache(%s) error: %s", key, exc)
        return False


async def delete_cache(key: str) -> bool:
    """Delete a key from Redis. Returns True if the key existed."""
    r = await get_redis()
    if r is None:
        return False
    try:
        result = await r.delete(key)
        return bool(result)
    except Exception as exc:
        logger.debug("delete_cache(%s) error: %s", key, exc)
        return False


async def delete_pattern(pattern: str) -> int:
    """Delete all keys matching a glob pattern. Returns the number of deleted keys."""
    r = await get_redis()
    if r is None:
        return 0
    try:
        keys = await r.keys(pattern)
        if not keys:
            return 0
        return await r.delete(*keys)
    except Exception as exc:
        logger.debug("delete_pattern(%s) error: %s", pattern, exc)
        return 0


async def increment_cache(key: str, amount: int = 1, ttl: int = 0) -> Optional[int]:
    """
    Atomically increment an integer counter in Redis.
    Optionally set / refresh a TTL.
    Returns the new value, or None on failure.
    """
    r = await get_redis()
    if r is None:
        return None
    try:
        new_val = await r.incrby(key, amount)
        if ttl > 0:
            await r.expire(key, ttl)
        return new_val
    except Exception as exc:
        logger.debug("increment_cache(%s) error: %s", key, exc)
        return None


async def cache_exists(key: str) -> bool:
    """Return True if the key exists in Redis."""
    r = await get_redis()
    if r is None:
        return False
    try:
        return bool(await r.exists(key))
    except Exception:
        return False


# ── Cache key builders ────────────────────────────────────────────────────────

def _group_key(chat_id: int) -> str:
    return f"group:settings:{chat_id}"


def _user_key(user_id: int) -> str:
    return f"user:data:{user_id}"


def _admin_key(chat_id: int) -> str:
    return f"chat:admins:{chat_id}"


def _economy_key(user_id: int, chat_id: int) -> str:
    return f"economy:{user_id}:{chat_id}"


def _rate_limit_key(user_id: int, action: str) -> str:
    return f"ratelimit:{user_id}:{action}"


def _flood_key(user_id: int, chat_id: int) -> str:
    return f"flood:{chat_id}:{user_id}"


def _gban_key(user_id: int) -> str:
    return f"gban:{user_id}"


def _fed_key(fed_id: str) -> str:
    return f"fed:{fed_id}"


# ── Domain-specific helpers ───────────────────────────────────────────────────

# -- Group settings --

async def get_group_settings(chat_id: int) -> Optional[Dict[str, Any]]:
    """
    Return cached group settings dict, or None if not cached.
    The caller should fall back to the database on None.
    """
    return await get_cache(_group_key(chat_id))


async def set_group_settings(
    chat_id: int, settings: Dict[str, Any], ttl: int = 600
) -> bool:
    """Cache group settings for `ttl` seconds (default 10 min)."""
    return await set_cache(_group_key(chat_id), settings, ttl)


async def invalidate_group_cache(chat_id: int) -> bool:
    """Bust the cached settings for a group."""
    return await delete_cache(_group_key(chat_id))


# -- User data --

async def get_user_data(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Return cached user data dict, or None if not cached.
    The caller should fall back to the database on None.
    """
    return await get_cache(_user_key(user_id))


async def set_user_data(
    user_id: int, data: Dict[str, Any], ttl: int = 300
) -> bool:
    """Cache user data for `ttl` seconds (default 5 min)."""
    return await set_cache(_user_key(user_id), data, ttl)


async def invalidate_user_cache(user_id: int) -> bool:
    """Bust the cached data for a user."""
    return await delete_cache(_user_key(user_id))


# -- Admin list --

async def get_cached_admins(chat_id: int) -> Optional[List[int]]:
    """Return the cached admin ID list for a chat."""
    return await get_cache(_admin_key(chat_id))


async def set_cached_admins(
    chat_id: int, admin_ids: List[int], ttl: int = 300
) -> bool:
    """Cache the admin list for a chat."""
    return await set_cache(_admin_key(chat_id), admin_ids, ttl)


async def invalidate_admin_cache(chat_id: int) -> bool:
    """Invalidate the admin cache for a chat (e.g. after a promotion/demotion)."""
    return await delete_cache(_admin_key(chat_id))


# -- Economy --

async def get_economy_data(user_id: int, chat_id: int) -> Optional[Dict[str, Any]]:
    """Return cached economy wallet for a user in a chat."""
    return await get_cache(_economy_key(user_id, chat_id))


async def set_economy_data(
    user_id: int, chat_id: int, data: Dict[str, Any], ttl: int = 120
) -> bool:
    """Cache economy data (default 2 min)."""
    return await set_cache(_economy_key(user_id, chat_id), data, ttl)


async def invalidate_economy_cache(user_id: int, chat_id: int) -> bool:
    """Invalidate the economy cache for a user in a chat."""
    return await delete_cache(_economy_key(user_id, chat_id))


# -- Rate limiting --

async def check_rate_limit(user_id: int, action: str, window: int) -> bool:
    """
    Token-bucket rate limiter backed by Redis.

    Returns True if the user is within the rate limit (action allowed),
    False if they are over the limit (action should be blocked).
    """
    key = _rate_limit_key(user_id, action)
    r = await get_redis()
    if r is None:
        return True  # Allow if Redis is down

    try:
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, window)
        return count <= 1
    except Exception as exc:
        logger.debug("check_rate_limit error: %s", exc)
        return True


# -- GBan cache --

async def is_gbanned_cached(user_id: int) -> Optional[bool]:
    """
    Return True/False if the gban status is cached, None if unknown.
    """
    val = await get_cache(_gban_key(user_id))
    if val is None:
        return None
    return bool(val)


async def set_gban_cache(user_id: int, is_banned: bool, ttl: int = 3600) -> bool:
    """Cache the gban status (default 1 hour)."""
    return await set_cache(_gban_key(user_id), int(is_banned), ttl)


async def invalidate_gban_cache(user_id: int) -> bool:
    """Invalidate the gban cache for a user."""
    return await delete_cache(_gban_key(user_id))


# -- Federation --

async def get_fed_data(fed_id: str) -> Optional[Dict[str, Any]]:
    """Return cached federation data."""
    return await get_cache(_fed_key(fed_id))


async def set_fed_data(
    fed_id: str, data: Dict[str, Any], ttl: int = 600
) -> bool:
    """Cache federation data."""
    return await set_cache(_fed_key(fed_id), data, ttl)


async def invalidate_fed_cache(fed_id: str) -> bool:
    """Invalidate federation cache."""
    return await delete_cache(_fed_key(fed_id))


# -- Flood tracking (Redis-backed) --

async def get_cache_stats() -> dict:
    """
    Return basic statistics about the Redis cache.
    Returns a dict with keys like 'connected', 'used_memory', 'total_keys'.
    """
    r = await get_redis()
    if r is None:
        return {"connected": False}
    try:
        info = await r.info()
        total_keys = await r.dbsize()
        return {
            "connected": True,
            "used_memory_bytes": info.get("used_memory", 0),
            "total_keys": total_keys,
            "uptime_seconds": info.get("uptime_in_seconds", "N/A"),
            "redis_version": info.get("redis_version", "N/A"),
        }
    except Exception as exc:
        logger.debug("get_cache_stats error: %s", exc)
        return {"connected": False, "error": str(exc)}


async def record_message(user_id: int, chat_id: int, window: int = 5) -> int:
    """
    Increment the per-user message counter within the current window.
    Returns the current message count in the window.
    Useful for anti-flood that survives bot restarts.
    """
    key = _flood_key(user_id, chat_id)
    r = await get_redis()
    if r is None:
        return 0
    try:
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, window)
        return count
    except Exception:
        return 0
