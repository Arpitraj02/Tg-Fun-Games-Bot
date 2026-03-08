"""
bot/database/models.py
──────────────────────
SQLAlchemy 2.0 ORM models for the entire bot schema.
All models use Mapped[] annotations (2.0-style) and JSON columns
backed by sqlalchemy.types.JSON (works with both SQLite and PostgreSQL).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


# ── Base ──────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    # Use JSONB on PostgreSQL, plain JSON elsewhere
    type_annotation_map: dict = {
        Dict[str, Any]: JSON(),
        List[Any]: JSON(),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Models ────────────────────────────────────────────────────────────────────

class User(Base):
    """Global user profile (one row per Telegram user)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    last_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    coins: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bank: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    xp: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reputation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_gbanned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gban_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_botbanned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    voice_duration: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_active: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    join_date: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )
    settings: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, default=dict)

    warnings: Mapped[List["Warning"]] = relationship(
        "Warning", back_populates="user_rel", foreign_keys="Warning.user_id",
        primaryjoin="User.user_id == Warning.user_id", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<User id={self.user_id} name={self.first_name!r}>"


class Group(Base):
    """Per-group configuration and feature flags."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    welcome_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    welcome_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    welcome_file_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    goodbye_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logs_channel: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    fed_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    antiflood_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    warn_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    warn_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="kick")
    captcha_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    captcha_type: Mapped[str] = mapped_column(String(32), nullable=False, default="button")
    antilink: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    antiforward: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    antinsfw: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    antiraid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settings: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, default=dict)

    def __repr__(self) -> str:
        return f"<Group id={self.chat_id} title={self.title!r}>"


class Warning(Base):
    """Individual warning issued to a user in a group."""

    __tablename__ = "warnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warned_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )

    user_rel: Mapped["User"] = relationship(
        "User", back_populates="warnings",
        primaryjoin="Warning.user_id == User.user_id",
        foreign_keys=[user_id], viewonly=True
    )

    def __repr__(self) -> str:
        return f"<Warning user={self.user_id} chat={self.chat_id}>"


class Filter(Base):
    """Keyword / trigger filters for automatic responses."""

    __tablename__ = "filters"
    __table_args__ = (UniqueConstraint("chat_id", "trigger", name="uq_filter_chat_trigger"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(256), nullable=False)
    response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    file_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    buttons: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True, default=list)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    action: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:
        return f"<Filter chat={self.chat_id} trigger={self.trigger!r}>"


class Note(Base):
    """Saved notes / snippets retrievable with #notename."""

    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("chat_id", "name", name="uq_note_chat_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    file_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    buttons: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True, default=list)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:
        return f"<Note chat={self.chat_id} name={self.name!r}>"


class Economy(Base):
    """Per-user, per-group economy wallet."""

    __tablename__ = "economy"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_economy_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bank: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    inventory: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, default=dict)
    daily_claimed: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    weekly_claimed: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    work_claimed: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    crime_claimed: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Economy user={self.user_id} chat={self.chat_id} balance={self.balance}>"


class Leaderboard(Base):
    """Activity leaderboard data per user per group."""

    __tablename__ = "leaderboard"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_lb_user_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    messages_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_week: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_month: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_all: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    voice_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    media_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_reset: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class GBan(Base):
    """Global bans — users banned from all federated groups."""

    __tablename__ = "gbans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    banned_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<GBan user={self.user_id}>"


class Federation(Base):
    """Federation — a named group of chats sharing ban lists."""

    __tablename__ = "federations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fed_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    admins: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True, default=list)
    groups: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True, default=list)
    banned_users: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True, default=dict
    )

    fed_bans: Mapped[List["FedBan"]] = relationship("FedBan", back_populates="federation")

    def __repr__(self) -> str:
        return f"<Federation id={self.fed_id} name={self.name!r}>"


class FedBan(Base):
    """A ban entry within a specific Federation."""

    __tablename__ = "fed_bans"
    __table_args__ = (UniqueConstraint("fed_id", "user_id", name="uq_fedban_fed_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fed_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("federations.fed_id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    banned_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )

    federation: Mapped["Federation"] = relationship("Federation", back_populates="fed_bans")

    def __repr__(self) -> str:
        return f"<FedBan fed={self.fed_id} user={self.user_id}>"


class StickerPack(Base):
    """User-created sticker packs managed by the bot."""

    __tablename__ = "sticker_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    pack_name: Mapped[str] = mapped_column(String(128), nullable=False)
    pack_link: Mapped[str] = mapped_column(String(256), nullable=False)
    sticker_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<StickerPack user={self.user_id} name={self.pack_name!r}>"


class SavedMessage(Base):
    """Messages saved by users for later retrieval."""

    __tablename__ = "saved_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    file_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tags: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True, default=list)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<SavedMessage user={self.user_id} type={self.message_type}>"


class CustomCommand(Base):
    """Custom slash-commands defined per group."""

    __tablename__ = "custom_commands"
    __table_args__ = (UniqueConstraint("chat_id", "trigger", name="uq_cmd_chat_trigger"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:
        return f"<CustomCommand chat={self.chat_id} trigger={self.trigger!r}>"


class Schedule(Base):
    """Scheduled messages sent to a group at fixed intervals."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    next_run: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    repeat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Schedule chat={self.chat_id} next={self.next_run}>"


class Reminder(Base):
    """One-time user reminders."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Reminder user={self.user_id} at={self.remind_at}>"


class Analytics(Base):
    """Daily analytics snapshots per group."""

    __tablename__ = "analytics"
    __table_args__ = (UniqueConstraint("chat_id", "date", name="uq_analytics_chat_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    joins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leaves: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    media_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commands_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<Analytics chat={self.chat_id} date={self.date.date()}>"


class UserProfile(Base):
    """Extended social profile for each user."""

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    badges: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True, default=list)
    couple_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    married_to: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    relationship_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="single"
    )
    last_rep_given: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rep_cooldown: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_streak: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    afk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    afk_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    afk_since: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<UserProfile user={self.user_id}>"


class Achievement(Base):
    """Unlocked achievements per user."""

    __tablename__ = "achievements"
    __table_args__ = (
        UniqueConstraint("user_id", "achievement_id", name="uq_achievement_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    achievement_id: Mapped[str] = mapped_column(String(64), nullable=False)
    achieved_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Achievement user={self.user_id} id={self.achievement_id}>"


class GameSession(Base):
    """Active or completed in-group game sessions."""

    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    game_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<GameSession chat={self.chat_id} type={self.game_type} active={self.active}>"


class Report(Base):
    """User-submitted reports about rule-breaking messages/members."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    reporter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reported_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now()
    )
    handled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<Report chat={self.chat_id} reported={self.reported_user_id} handled={self.handled}>"


class Blacklist(Base):
    """Per-group blacklisted words / patterns."""

    __tablename__ = "blacklist"
    __table_args__ = (UniqueConstraint("chat_id", "word", name="uq_blacklist_chat_word"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    word: Mapped[str] = mapped_column(String(256), nullable=False)
    action: Mapped[str] = mapped_column(
        String(32), nullable=False, default="delete"
    )  # delete|warn|mute|kick|ban

    def __repr__(self) -> str:
        return f"<Blacklist chat={self.chat_id} word={self.word!r}>"
