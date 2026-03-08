"""
bot/plugins/moderation.py
─────────────────────────
Ban, kick, mute, warn, purge and related moderation commands.
Temporary actions are lifted automatically via PTB JobQueue.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import delete, func, select
from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.database.connection import get_session
from bot.database.models import Group, Warning
from bot.helpers.decorators import admin_only, bot_admin_required, can_delete_messages, can_restrict_members
from bot.helpers.formatters import bold, code, escape_html, format_time, italic, user_mention
from bot.helpers.utils import extract_user_and_reason, extract_user_id, parse_time

logger = logging.getLogger(__name__)

# Permissions that represent a fully unmuted user
_FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
)
_MUTE_PERMISSIONS = ChatPermissions(can_send_messages=False)


def _group_only(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def _reply(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


async def _get_warn_settings(chat_id: int):
    """Return (warn_limit, warn_mode) from DB, with defaults."""
    async with get_session() as session:
        result = await session.execute(select(Group).where(Group.chat_id == chat_id))
        group = result.scalar_one_or_none()
    if group:
        return group.warn_limit, group.warn_mode
    return 3, "kick"


# ── JobQueue callbacks for temp actions ───────────────────────────────────────

async def _unban_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: lift a temporary ban."""
    data = context.job.data  # type: ignore[union-attr]
    chat_id: int = data["chat_id"]
    user_id: int = data["user_id"]
    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info("Temp ban lifted: user=%s chat=%s", user_id, chat_id)
    except TelegramError as e:
        logger.warning("Could not lift temp ban for user=%s: %s", user_id, e)


async def _unmute_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: lift a temporary mute."""
    data = context.job.data  # type: ignore[union-attr]
    chat_id: int = data["chat_id"]
    user_id: int = data["user_id"]
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id, permissions=_FULL_PERMISSIONS
        )
        logger.info("Temp mute lifted: user=%s chat=%s", user_id, chat_id)
    except TelegramError as e:
        logger.warning("Could not lift temp mute for user=%s: %s", user_id, e)


# ── BAN ───────────────────────────────────────────────────────────────────────

@admin_only
@bot_admin_required
@can_restrict_members
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ban <user> [reason] — Permanently ban a user."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    actor = update.effective_user
    user_id, reason, err = await extract_user_and_reason(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    if user_id == actor.id:
        await _reply(update, "❌ You cannot ban yourself.")
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id)
    except TelegramError as e:
        await _reply(update, f"❌ Failed to ban: {escape_html(str(e))}")
        return

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    text = f"🔨 {mention} has been {bold('banned')}."
    if reason:
        text += f"\n📝 {bold('Reason:')} {escape_html(reason)}"
    await _reply(update, text)


@admin_only
@bot_admin_required
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unban <user> — Unban a user."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    try:
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user_id, only_if_banned=True)
    except TelegramError as e:
        await _reply(update, f"❌ Failed to unban: {escape_html(str(e))}")
        return

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    await _reply(update, f"✅ {mention} has been {bold('unbanned')}.")


@admin_only
@bot_admin_required
@can_restrict_members
async def tban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tban <user> <time> [reason] — Temporarily ban a user."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    actor = update.effective_user
    args = context.args or []

    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    if user_id == actor.id:
        await _reply(update, "❌ You cannot ban yourself.")
        return

    # Determine time arg: first arg after user (or first arg if reply-based)
    reply = update.effective_message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == user_id:
        time_args = args
    else:
        time_args = args[1:] if len(args) > 1 else []

    if not time_args:
        await _reply(update, f"❌ Usage: {code('/tban &lt;user&gt; &lt;time&gt; [reason]')}\nExample: {code('/tban @user 1h spam')}")
        return

    duration = parse_time(time_args[0])
    if not duration:
        await _reply(update, f"❌ Invalid time format. Examples: {code('30m')}, {code('2h')}, {code('1d')}, {code('1w')}")
        return

    reason = " ".join(time_args[1:]) if len(time_args) > 1 else None
    until = datetime.now(timezone.utc) + timedelta(seconds=duration)

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id, until_date=until)
    except TelegramError as e:
        await _reply(update, f"❌ Failed: {escape_html(str(e))}")
        return

    # Schedule unban job
    context.job_queue.run_once(
        _unban_job,
        when=duration,
        data={"chat_id": chat.id, "user_id": user_id},
        name=f"tban_{chat.id}_{user_id}",
    )

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    text = f"⏳ {mention} has been {bold('temporarily banned')} for {bold(format_time(duration))}."
    if reason:
        text += f"\n📝 {bold('Reason:')} {escape_html(reason)}"
    await _reply(update, text)


# ── KICK ──────────────────────────────────────────────────────────────────────

@admin_only
@bot_admin_required
@can_restrict_members
async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kick <user> [reason] — Kick a user (they can rejoin)."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    actor = update.effective_user
    user_id, reason, err = await extract_user_and_reason(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    if user_id == actor.id:
        await _reply(update, "❌ You cannot kick yourself. Use /kickme if you want to leave.")
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user_id)
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user_id)
    except TelegramError as e:
        await _reply(update, f"❌ Failed to kick: {escape_html(str(e))}")
        return

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    text = f"👢 {mention} has been {bold('kicked')}."
    if reason:
        text += f"\n📝 {bold('Reason:')} {escape_html(reason)}"
    await _reply(update, text)


async def kickme_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kickme — User kicks themselves from the group."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    user = update.effective_user
    if not user:
        return

    try:
        await _reply(update, f"👋 {user_mention(user.id, user.first_name)}, bye! You kicked yourself.")
        await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
        await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
    except TelegramError as e:
        await _reply(update, f"❌ Could not kick you: {escape_html(str(e))}")


# ── MUTE ──────────────────────────────────────────────────────────────────────

@admin_only
@bot_admin_required
@can_restrict_members
async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mute <user> [reason] — Permanently mute a user."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    actor = update.effective_user
    user_id, reason, err = await extract_user_and_reason(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    if user_id == actor.id:
        await _reply(update, "❌ You cannot mute yourself.")
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id, user_id=user_id, permissions=_MUTE_PERMISSIONS
        )
    except TelegramError as e:
        await _reply(update, f"❌ Failed to mute: {escape_html(str(e))}")
        return

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    text = f"🔇 {mention} has been {bold('muted')}."
    if reason:
        text += f"\n📝 {bold('Reason:')} {escape_html(reason)}"
    await _reply(update, text)


@admin_only
@bot_admin_required
async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unmute <user> — Unmute a user."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id, user_id=user_id, permissions=_FULL_PERMISSIONS
        )
    except TelegramError as e:
        await _reply(update, f"❌ Failed to unmute: {escape_html(str(e))}")
        return

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    await _reply(update, f"🔊 {mention} has been {bold('unmuted')}.")


@admin_only
@bot_admin_required
@can_restrict_members
async def tmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tmute <user> <time> [reason] — Temporarily mute."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    actor = update.effective_user
    args = context.args or []

    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    if user_id == actor.id:
        await _reply(update, "❌ You cannot mute yourself.")
        return

    reply = update.effective_message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == user_id:
        time_args = args
    else:
        time_args = args[1:] if len(args) > 1 else []

    if not time_args:
        await _reply(update, f"❌ Usage: {code('/tmute &lt;user&gt; &lt;time&gt; [reason]')}")
        return

    duration = parse_time(time_args[0])
    if not duration:
        await _reply(update, "❌ Invalid time format. E.g. 30m, 2h, 1d.")
        return

    reason = " ".join(time_args[1:]) if len(time_args) > 1 else None
    until = datetime.now(timezone.utc) + timedelta(seconds=duration)

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=_MUTE_PERMISSIONS,
            until_date=until,
        )
    except TelegramError as e:
        await _reply(update, f"❌ Failed: {escape_html(str(e))}")
        return

    context.job_queue.run_once(
        _unmute_job,
        when=duration,
        data={"chat_id": chat.id, "user_id": user_id},
        name=f"tmute_{chat.id}_{user_id}",
    )

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    text = f"🔇 {mention} has been {bold('temporarily muted')} for {bold(format_time(duration))}."
    if reason:
        text += f"\n📝 {bold('Reason:')} {escape_html(reason)}"
    await _reply(update, text)


# ── WARN ──────────────────────────────────────────────────────────────────────

@admin_only
async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/warn <user> [reason] — Warn a user. Auto-action at warn limit."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    actor = update.effective_user
    user_id, reason, err = await extract_user_and_reason(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    if user_id == actor.id:
        await _reply(update, "❌ You cannot warn yourself.")
        return

    warn_limit, warn_mode = await _get_warn_settings(chat.id)

    async with get_session() as session:
        warning = Warning(
            user_id=user_id,
            chat_id=chat.id,
            reason=reason,
            warned_by=actor.id,
        )
        session.add(warning)
        await session.commit()

        result = await session.execute(
            select(func.count(Warning.id)).where(
                Warning.user_id == user_id, Warning.chat_id == chat.id
            )
        )
        warn_count = result.scalar_one()

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    text = (
        f"⚠️ {mention} has been {bold('warned')}.\n"
        f"📊 {bold('Warns:')} {warn_count}/{warn_limit}"
    )
    if reason:
        text += f"\n📝 {bold('Reason:')} {escape_html(reason)}"

    if warn_count >= warn_limit:
        text += f"\n\n🚨 {bold('Warn limit reached!')} Taking action: {bold(warn_mode)}"
        await _reply(update, text)
        await _apply_warn_action(update, context, chat.id, user_id, warn_mode)
    else:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑️ Remove Last Warn", callback_data=f"warn:rm:{user_id}:{chat.id}"),
                    InlineKeyboardButton("🧹 Reset All Warns", callback_data=f"warn:reset:{user_id}:{chat.id}"),
                ]
            ]
        )
        await _reply(update, text, keyboard)


async def _apply_warn_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, mode: str
) -> None:
    """Execute the automated action when warn limit is reached."""
    try:
        if mode == "ban":
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        elif mode == "mute":
            await context.bot.restrict_chat_member(
                chat_id=chat_id, user_id=user_id, permissions=_MUTE_PERMISSIONS
            )
        elif mode == "kick":
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        # "none" — no action beyond the warn itself
    except TelegramError as e:
        logger.warning("Failed warn-limit action (%s) on user=%s: %s", mode, user_id, e)


async def warn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle warn:rm and warn:reset callbacks."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = query.data.split(":")
    action = parts[1]
    target_user_id = int(parts[2])
    chat_id = int(parts[3])

    from bot.helpers.utils import is_admin
    if not await is_admin(context.bot, chat_id, user.id):
        await query.answer("🚫 Admins only.", show_alert=True)
        return

    async with get_session() as session:
        if action == "rm":
            # Remove the most recent warning
            result = await session.execute(
                select(Warning)
                .where(Warning.user_id == target_user_id, Warning.chat_id == chat_id)
                .order_by(Warning.timestamp.desc())
                .limit(1)
            )
            warn = result.scalar_one_or_none()
            if warn:
                await session.delete(warn)
                await session.commit()
                try:
                    await query.edit_message_text(
                        query.message.text_html + f"\n\n✅ {bold('Last warning removed by')} {user_mention(user.id, user.first_name)}.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=None,
                    )
                except TelegramError:
                    pass
            else:
                await query.answer("No warnings found.", show_alert=True)
        elif action == "reset":
            await session.execute(
                delete(Warning).where(
                    Warning.user_id == target_user_id, Warning.chat_id == chat_id
                )
            )
            await session.commit()
            try:
                await query.edit_message_text(
                    query.message.text_html + f"\n\n🧹 {bold('All warnings cleared by')} {user_mention(user.id, user.first_name)}.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
            except TelegramError:
                pass


@admin_only
async def unwarn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unwarn <user> — Remove the most recent warning."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    async with get_session() as session:
        result = await session.execute(
            select(Warning)
            .where(Warning.user_id == user_id, Warning.chat_id == chat.id)
            .order_by(Warning.timestamp.desc())
            .limit(1)
        )
        warn = result.scalar_one_or_none()
        if warn:
            await session.delete(warn)
            await session.commit()
            try:
                target = await context.bot.get_chat(user_id)
                mention = user_mention(user_id, target.first_name or str(user_id))
            except TelegramError:
                mention = user_mention(user_id, str(user_id))
            await _reply(update, f"✅ Last warning removed from {mention}.")
        else:
            await _reply(update, "ℹ️ This user has no warnings.")


async def warns_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/warns <user> — Show warnings for a user."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    warn_limit, warn_mode = await _get_warn_settings(chat.id)

    async with get_session() as session:
        result = await session.execute(
            select(Warning)
            .where(Warning.user_id == user_id, Warning.chat_id == chat.id)
            .order_by(Warning.timestamp.desc())
        )
        warns: List[Warning] = result.scalars().all()

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    if not warns:
        await _reply(update, f"✅ {mention} has {bold('no warnings')}.")
        return

    lines = [
        f"⚠️ {bold('Warnings for')} {mention}",
        f"📊 {bold('Count:')} {len(warns)}/{warn_limit} (action: {warn_mode})",
        "",
    ]
    for i, w in enumerate(warns, 1):
        r = escape_html(w.reason) if w.reason else italic("No reason given")
        lines.append(f"{i}. {r}")
        lines.append(f"   {italic(w.timestamp.strftime('%Y-%m-%d %H:%M UTC') if w.timestamp else '')}")

    await _reply(update, "\n".join(lines))


@admin_only
async def resetwarns_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resetwarns <user> — Clear all warnings."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    async with get_session() as session:
        result = await session.execute(
            delete(Warning).where(Warning.user_id == user_id, Warning.chat_id == chat.id)
        )
        await session.commit()
        deleted = result.rowcount

    try:
        target = await context.bot.get_chat(user_id)
        mention = user_mention(user_id, target.first_name or str(user_id))
    except TelegramError:
        mention = user_mention(user_id, str(user_id))

    await _reply(update, f"🧹 Cleared {bold(str(deleted))} warning(s) for {mention}.")


@admin_only
async def setwarnlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setwarnlimit <num> — Set warning threshold (1-10)."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    if not context.args or not context.args[0].isdigit():
        await _reply(update, f"❌ Usage: {code('/setwarnlimit &lt;1-10&gt;')}")
        return

    limit = int(context.args[0])
    if not 1 <= limit <= 10:
        await _reply(update, "❌ Warn limit must be between 1 and 10.")
        return

    chat = update.effective_chat
    async with get_session() as session:
        result = await session.execute(select(Group).where(Group.chat_id == chat.id))
        group = result.scalar_one_or_none()
        if not group:
            group = Group(chat_id=chat.id, title=chat.title or "")
            session.add(group)
        group.warn_limit = limit
        await session.commit()

    await _reply(update, f"✅ Warn limit set to {bold(str(limit))}.")


@admin_only
async def warnmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/warnmode <ban|mute|kick> — Set action taken at warn limit."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    valid_modes = ("ban", "mute", "kick", "none")
    if not context.args or context.args[0].lower() not in valid_modes:
        await _reply(update, f"❌ Usage: {code('/warnmode &lt;ban|mute|kick|none&gt;')}")
        return

    mode = context.args[0].lower()
    chat = update.effective_chat

    async with get_session() as session:
        result = await session.execute(select(Group).where(Group.chat_id == chat.id))
        group = result.scalar_one_or_none()
        if not group:
            group = Group(chat_id=chat.id, title=chat.title or "")
            session.add(group)
        group.warn_mode = mode
        await session.commit()

    await _reply(update, f"✅ Warn mode set to {bold(mode)}.")


# ── PURGE ─────────────────────────────────────────────────────────────────────

async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: delete a single message (used for auto-cleanup)."""
    data = context.job.data  # type: ignore[union-attr]
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"], message_id=data["message_id"]
        )
    except TelegramError:
        pass


@admin_only
@can_delete_messages
@bot_admin_required
async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/purge [n] — Delete messages from reply to now, or last N messages."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    msg = update.effective_message

    # Delete the /purge command itself first
    try:
        await msg.delete()
    except TelegramError:
        pass

    if context.args and context.args[0].isdigit():
        count = min(int(context.args[0]), 100)
        end_id = msg.message_id - 1
        start_id = end_id - count + 1
    elif msg.reply_to_message:
        start_id = msg.reply_to_message.message_id
        end_id = msg.message_id - 1
    else:
        await _reply(update, f"❌ Reply to a message to purge from it, or use {code('/purge &lt;n&gt;')}.")
        return

    ids_to_delete = list(range(start_id, end_id + 1))
    if not ids_to_delete:
        return

    # Delete in batches of 100 (Telegram limit)
    deleted = 0
    for i in range(0, len(ids_to_delete), 100):
        batch = ids_to_delete[i : i + 100]
        tasks = [
            context.bot.delete_message(chat_id=chat.id, message_id=mid) for mid in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        deleted += sum(1 for r in results if not isinstance(r, Exception))

    note = await context.bot.send_message(
        chat_id=chat.id,
        text=f"🗑️ Purged {bold(str(deleted))} message(s).",
        parse_mode=ParseMode.HTML,
    )
    # Auto-delete the confirmation after 5 seconds via JobQueue
    context.job_queue.run_once(
        _delete_message_job,
        when=5,
        data={"chat_id": chat.id, "message_id": note.message_id},
    )


@admin_only
@can_delete_messages
@bot_admin_required
async def del_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/del — Delete the replied message."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    msg = update.effective_message
    if not msg.reply_to_message:
        await _reply(update, "❌ Reply to a message to delete it.")
        return

    try:
        await msg.reply_to_message.delete()
        await msg.delete()
    except TelegramError as e:
        await _reply(update, f"❌ Could not delete: {escape_html(str(e))}")


# ── ZOMBIES ───────────────────────────────────────────────────────────────────

@admin_only
async def zombies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/zombies — List deleted accounts in the group."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    sent = await _reply(update, "🔍 Scanning for deleted accounts… Please wait.")

    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        admin_ids = {m.user.id for m in admins}
    except TelegramError:
        admin_ids = set()

    zombie_ids: List[int] = []

    # Telegram doesn't provide a full member list via Bot API for large groups.
    # We can only scan through recently seen members stored in updates context.
    # As a practical approach we report admins who are deleted.
    for member in await context.bot.get_chat_administrators(chat.id):
        if member.user.first_name == "Deleted Account":
            zombie_ids.append(member.user.id)

    if not zombie_ids:
        try:
            if sent:
                await sent.edit_text("✅ No deleted accounts found among admins.")
        except TelegramError:
            await _reply(update, "✅ No deleted accounts found among admins.")
        return

    lines = [f"🧟 {bold('Deleted accounts found:')} {len(zombie_ids)}", ""]
    for zid in zombie_ids:
        lines.append(f"• {code(str(zid))}")

    lines.append(f"\n{italic('Use /kickzombies to remove them.')}")
    try:
        if sent:
            await sent.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except TelegramError:
        await _reply(update, "\n".join(lines))


@admin_only
@bot_admin_required
@can_restrict_members
async def kickzombies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kickzombies — Kick all deleted accounts from the group."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    sent = await _reply(update, "🔍 Scanning and removing deleted accounts…")

    kicked = 0
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        for member in admins:
            if member.user.first_name == "Deleted Account":
                try:
                    await context.bot.ban_chat_member(chat_id=chat.id, user_id=member.user.id)
                    await context.bot.unban_chat_member(chat_id=chat.id, user_id=member.user.id)
                    kicked += 1
                except TelegramError:
                    pass
    except TelegramError as e:
        await _reply(update, f"❌ Failed: {escape_html(str(e))}")
        return

    msg = f"🧹 Kicked {bold(str(kicked))} deleted account(s)." if kicked else "✅ No deleted accounts to remove."
    try:
        if sent:
            await sent.edit_text(msg, parse_mode=ParseMode.HTML)
    except TelegramError:
        await _reply(update, msg)


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("tban", tban_command))
    app.add_handler(CommandHandler("kick", kick_command))
    app.add_handler(CommandHandler("kickme", kickme_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(CommandHandler("unmute", unmute_command))
    app.add_handler(CommandHandler("tmute", tmute_command))
    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler("unwarn", unwarn_command))
    app.add_handler(CommandHandler("warns", warns_command))
    app.add_handler(CommandHandler("resetwarns", resetwarns_command))
    app.add_handler(CommandHandler("setwarnlimit", setwarnlimit_command))
    app.add_handler(CommandHandler("warnmode", warnmode_command))
    app.add_handler(CommandHandler("purge", purge_command))
    app.add_handler(CommandHandler("del", del_command))
    app.add_handler(CommandHandler("zombies", zombies_command))
    app.add_handler(CommandHandler("kickzombies", kickzombies_command))
    app.add_handler(CallbackQueryHandler(warn_callback, pattern=r"^warn:"))
