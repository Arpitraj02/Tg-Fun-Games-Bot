"""
bot/database/__init__.py
────────────────────────
Re-exports every model and the connection helpers so that other modules
can do:

    from bot.database import User, Group, get_session, init_db
"""
from bot.database.connection import (
    AsyncSessionFactory,
    check_db_connection,
    close_db,
    engine,
    get_read_session,
    get_session,
    init_db,
)
from bot.database.models import (
    Achievement,
    Analytics,
    Base,
    Blacklist,
    CustomCommand,
    Economy,
    FedBan,
    Federation,
    Filter,
    GBan,
    GameSession,
    Group,
    Leaderboard,
    Note,
    Reminder,
    Report,
    SavedMessage,
    Schedule,
    StickerPack,
    User,
    UserProfile,
    Warning,
)

__all__ = [
    # Connection helpers
    "engine",
    "AsyncSessionFactory",
    "get_session",
    "get_read_session",
    "init_db",
    "close_db",
    "check_db_connection",
    # Base
    "Base",
    # Models
    "User",
    "Group",
    "Warning",
    "Filter",
    "Note",
    "Economy",
    "Leaderboard",
    "GBan",
    "Federation",
    "FedBan",
    "StickerPack",
    "SavedMessage",
    "CustomCommand",
    "Schedule",
    "Reminder",
    "Analytics",
    "UserProfile",
    "Achievement",
    "GameSession",
    "Report",
    "Blacklist",
]
