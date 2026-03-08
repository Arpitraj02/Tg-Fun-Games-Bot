"""
bot/plugins/admin.py
────────────────────
Group administration commands. All commands require admin privileges.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from telegram import (
    ChatMember,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.database.connection import get_session
from bot.database.models import Analytics, User, Warning
from bot.helpers.decorators import admin_only, bot_admin_required, can_restrict_members
from bot.helpers.formatters import (
    bold,
    code,
    escape_html,
    format_datetime,
    format_number,
    italic,
    time_ago,
    user_mention,
)
from bot.helpers.utils import extract_user_id, get_admin_list, invalidate_admin_cache

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _group_only_check(update: Update) -> bool:
    """Return True if the effective chat is a group/supergroup."""
    chat = update.effective_chat
    return chat is not None and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def _reply(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


# ── Promote / Demote / Title ──────────────────────────────────────────────────

@admin_only
@bot_admin_required
async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/promote <user> [title] — Promote user to admin."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    # Extract optional title from args (skip first arg if it was the user)
    args = context.args or []
    title: Optional[str] = None
    reply = update.effective_message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == user_id:
        title = " ".join(args) if args else None
    elif args and len(args) > 1:
        title = " ".join(args[1:])

    try:
        await context.bot.promote_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            can_manage_chat=True,
            can_change_info=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_manage_video_chats=True,
        )

        if title:
            title = title[:16]  # Telegram limit
            try:
                await context.bot.set_chat_administrator_custom_title(
                    chat_id=chat.id, user_id=user_id, custom_title=title
                )
            except TelegramError:
                pass  # Title might fail if user is already owner

        invalidate_admin_cache(chat.id)

        try:
            target = await context.bot.get_chat(user_id)
            mention = user_mention(user_id, target.first_name or str(user_id))
        except TelegramError:
            mention = user_mention(user_id, str(user_id))

        msg = f"⭐ {mention} has been {bold('promoted to admin')}."
        if title:
            msg += f"\n🏷️ Custom title: {code(escape_html(title))}"
        await _reply(update, msg)

    except TelegramError as e:
        await _reply(update, f"❌ Failed to promote user: {escape_html(str(e))}")


@admin_only
@bot_admin_required
async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/demote <user> — Remove admin rights."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    try:
        await context.bot.promote_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            can_manage_chat=False,
            can_change_info=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_manage_video_chats=False,
        )
        invalidate_admin_cache(chat.id)

        try:
            target = await context.bot.get_chat(user_id)
            mention = user_mention(user_id, target.first_name or str(user_id))
        except TelegramError:
            mention = user_mention(user_id, str(user_id))

        await _reply(update, f"🔽 {mention} has been {bold('demoted')}.")

    except TelegramError as e:
        await _reply(update, f"❌ Failed to demote user: {escape_html(str(e))}")


@admin_only
@bot_admin_required
async def title_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/title <user> <title> — Set a custom admin title."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not determine target user.'}")
        return

    args = context.args or []
    reply = update.effective_message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == user_id:
        title_parts = args
    else:
        title_parts = args[1:] if len(args) > 1 else []

    if not title_parts:
        await _reply(update, f"❌ Please provide a title.\nUsage: {code('/title &lt;user&gt; &lt;title&gt;')}")
        return

    title = " ".join(title_parts)[:16]

    try:
        await context.bot.set_chat_administrator_custom_title(
            chat_id=chat.id, user_id=user_id, custom_title=title
        )

        try:
            target = await context.bot.get_chat(user_id)
            mention = user_mention(user_id, target.first_name or str(user_id))
        except TelegramError:
            mention = user_mention(user_id, str(user_id))

        await _reply(update, f"🏷️ {mention}'s admin title set to: {code(escape_html(title))}")

    except TelegramError as e:
        await _reply(update, f"❌ Failed to set title: {escape_html(str(e))}")


# ── Pin / Unpin ───────────────────────────────────────────────────────────────

@admin_only
@bot_admin_required
async def pin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pin [loud] — Pin the replied message."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    msg = update.effective_message
    if not msg.reply_to_message:
        await _reply(update, "❌ Reply to a message to pin it.")
        return

    chat = update.effective_chat
    loud = bool(context.args and context.args[0].lower() == "loud")

    try:
        await context.bot.pin_chat_message(
            chat_id=chat.id,
            message_id=msg.reply_to_message.message_id,
            disable_notification=not loud,
        )
        notify_text = "with notification" if loud else "silently"
        await _reply(update, f"📌 Message {bold('pinned')} {notify_text}.")
    except TelegramError as e:
        await _reply(update, f"❌ Could not pin message: {escape_html(str(e))}")


@admin_only
@bot_admin_required
async def unpin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unpin — Unpin the replied message."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    msg = update.effective_message

    target_id = msg.reply_to_message.message_id if msg.reply_to_message else None

    try:
        if target_id:
            await context.bot.unpin_chat_message(chat_id=chat.id, message_id=target_id)
        else:
            await context.bot.unpin_chat_message(chat_id=chat.id)
        await _reply(update, "📌 Message unpinned.")
    except TelegramError as e:
        await _reply(update, f"❌ Could not unpin: {escape_html(str(e))}")


@admin_only
@bot_admin_required
async def unpinall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unpinall — Unpin all messages (shows confirmation button)."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes, unpin all", callback_data=f"unpinall_confirm:{chat.id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="unpinall_cancel"),
            ]
        ]
    )
    await _reply(
        update,
        f"⚠️ {bold('Confirm:')} Unpin {bold('ALL')} pinned messages in this group?",
        keyboard,
    )


async def unpinall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unpinall confirmation callback."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    await query.answer()

    if query.data == "unpinall_cancel":
        try:
            await query.edit_message_text("❌ Cancelled.")
        except TelegramError:
            pass
        return

    chat_id = int(query.data.split(":")[1])

    # Verify the user pressing the button is an admin
    from bot.helpers.utils import is_admin  # avoid circular at module level
    if not await is_admin(context.bot, chat_id, user.id):
        await query.answer("🚫 Only admins can confirm this.", show_alert=True)
        return

    try:
        await context.bot.unpin_all_chat_messages(chat_id=chat_id)
        try:
            await query.edit_message_text(f"📌 All messages have been {bold('unpinned')}.", parse_mode=ParseMode.HTML)
        except TelegramError:
            pass
    except TelegramError as e:
        try:
            await query.edit_message_text(f"❌ Failed: {escape_html(str(e))}", parse_mode=ParseMode.HTML)
        except TelegramError:
            pass


# ── Invite links ──────────────────────────────────────────────────────────────

@admin_only
@bot_admin_required
async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/invite — Get the group's invite link."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    try:
        chat_obj = await context.bot.get_chat(chat.id)
        link = chat_obj.invite_link
        if not link:
            link = await context.bot.export_chat_invite_link(chat.id)
        await _reply(update, f"🔗 {bold('Invite link for')} {escape_html(chat.title or 'this group')}:\n\n{link}")
    except TelegramError as e:
        await _reply(update, f"❌ Could not get invite link: {escape_html(str(e))}")


@admin_only
@bot_admin_required
async def revokeinvite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/revokeinvite — Revoke current invite link and generate a new one."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    try:
        new_link = await context.bot.export_chat_invite_link(chat.id)
        await _reply(
            update,
            f"🔄 {bold('Invite link revoked and regenerated:')}\n\n{new_link}",
        )
    except TelegramError as e:
        await _reply(update, f"❌ Failed: {escape_html(str(e))}")


# ── Chat settings ─────────────────────────────────────────────────────────────

@admin_only
@bot_admin_required
async def setdescription_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setdescription <text> — Set group description."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    if not context.args:
        await _reply(update, f"❌ Usage: {code('/setdescription &lt;text&gt;')}")
        return

    chat = update.effective_chat
    description = " ".join(context.args)
    try:
        await context.bot.set_chat_description(chat_id=chat.id, description=description)
        await _reply(update, f"✅ {bold('Group description updated.')} ")
    except TelegramError as e:
        await _reply(update, f"❌ Failed: {escape_html(str(e))}")


@admin_only
@bot_admin_required
async def settitle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/settitle <text> — Set group title."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    if not context.args:
        await _reply(update, f"❌ Usage: {code('/settitle &lt;text&gt;')}")
        return

    chat = update.effective_chat
    title = " ".join(context.args)[:255]
    try:
        await context.bot.set_chat_title(chat_id=chat.id, title=title)
        await _reply(update, f"✅ {bold('Group title updated to:')} {escape_html(title)}")
    except TelegramError as e:
        await _reply(update, f"❌ Failed: {escape_html(str(e))}")


# ── Admin list ────────────────────────────────────────────────────────────────

async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admins — List all admins in the group."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
    except TelegramError as e:
        await _reply(update, f"❌ Could not fetch admin list: {escape_html(str(e))}")
        return

    lines = [f"👑 {bold('Admins in')} {escape_html(chat.title or 'this group')}:", ""]
    for member in sorted(admins, key=lambda m: (m.status != ChatMemberStatus.OWNER, m.user.full_name)):
        icon = "👑" if member.status == ChatMemberStatus.OWNER else "⭐"
        name = escape_html(member.user.full_name)
        title_tag = ""
        if hasattr(member, "custom_title") and member.custom_title:
            title_tag = f" — {italic(escape_html(member.custom_title))}"
        is_bot = " 🤖" if member.user.is_bot else ""
        lines.append(f"{icon} {user_mention(member.user.id, name)}{is_bot}{title_tag}")

    lines.append(f"\n{italic(f'Total: {len(admins)} admin(s)')}")
    await _reply(update, "\n".join(lines))


async def adminlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/adminlist — Detailed admin list (alias of /admins)."""
    await admins_command(update, context)


# ── User / chat info ──────────────────────────────────────────────────────────

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/id [user] — Get Telegram IDs."""
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user
    if not chat or not msg or not user:
        return

    lines = []

    if msg.reply_to_message:
        ru = msg.reply_to_message.from_user
        if ru:
            lines.append(f"👤 {bold('User ID:')} {code(str(ru.id))}")
            lines.append(f"   {bold('Name:')} {escape_html(ru.full_name)}")
            if ru.username:
                lines.append(f"   {bold('Username:')} @{ru.username}")
        if msg.reply_to_message.forward_from:
            ff = msg.reply_to_message.forward_from
            lines.append(f"↩️ {bold('Forwarded from ID:')} {code(str(ff.id))}")
        lines.append(f"💬 {bold('Message ID:')} {code(str(msg.reply_to_message.message_id))}")
    elif context.args:
        user_id, err = await extract_user_id(update, context)
        if err or user_id is None:
            await _reply(update, f"❌ {err}")
            return
        lines.append(f"👤 {bold('User ID:')} {code(str(user_id))}")
    else:
        lines.append(f"👤 {bold('Your ID:')} {code(str(user.id))}")

    lines.append(f"💬 {bold('Chat ID:')} {code(str(chat.id))}")
    if chat.type != ChatType.PRIVATE:
        lines.append(f"   {bold('Chat title:')} {escape_html(chat.title or '')}")

    await _reply(update, "\n".join(lines))


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/info [user] — Detailed user information."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err}")
        return

    try:
        target = await context.bot.get_chat(user_id)
    except TelegramError as e:
        await _reply(update, f"❌ Could not fetch user: {escape_html(str(e))}")
        return

    lines = [
        f"👤 {bold('User Information')}",
        "",
        f"• {bold('Name:')} {user_mention(target.id, escape_html(target.full_name or str(target.id)))}",
        f"• {bold('ID:')} {code(str(target.id))}",
    ]
    if target.username:
        lines.append(f"• {bold('Username:')} @{target.username}")

    # Chat member status (if in a group)
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            member = await context.bot.get_chat_member(chat.id, user_id)
            status_map = {
                ChatMemberStatus.OWNER: "👑 Owner",
                ChatMemberStatus.ADMINISTRATOR: "⭐ Admin",
                ChatMemberStatus.MEMBER: "👤 Member",
                ChatMemberStatus.RESTRICTED: "🔇 Restricted",
                ChatMemberStatus.LEFT: "👻 Left",
                ChatMemberStatus.BANNED: "🚫 Banned",
            }
            lines.append(f"• {bold('Status:')} {status_map.get(member.status, member.status)}")
        except TelegramError:
            pass

    # Warnings from DB
    async with get_session() as session:
        result = await session.execute(
            select(Warning).where(
                Warning.user_id == user_id, Warning.chat_id == chat.id
            )
        )
        warns = result.scalars().all()
    if warns:
        lines.append(f"• {bold('Warnings:')} {len(warns)}")

    if target.is_bot:
        lines.append(f"• {bold('Account type:')} 🤖 Bot")

    await _reply(update, "\n".join(lines))


async def chatinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chatinfo — Information about the current chat."""
    chat = update.effective_chat
    if not chat:
        return

    try:
        full_chat = await context.bot.get_chat(chat.id)
        member_count = await context.bot.get_chat_member_count(chat.id)
    except TelegramError as e:
        await _reply(update, f"❌ Could not fetch chat info: {escape_html(str(e))}")
        return

    admins = await get_admin_list(context.bot, chat.id)

    lines = [
        f"💬 {bold('Chat Information')}",
        "",
        f"• {bold('Title:')} {escape_html(full_chat.title or 'N/A')}",
        f"• {bold('ID:')} {code(str(full_chat.id))}",
        f"• {bold('Type:')} {full_chat.type.capitalize()}",
    ]
    if full_chat.username:
        lines.append(f"• {bold('Username:')} @{full_chat.username}")
    if full_chat.description:
        lines.append(f"• {bold('Description:')} {escape_html(full_chat.description[:200])}")
    lines.append(f"• {bold('Members:')} {format_number(member_count)}")
    lines.append(f"• {bold('Admins:')} {len(admins)}")

    if full_chat.invite_link:
        lines.append(f"• {bold('Invite link:')} {full_chat.invite_link}")

    await _reply(update, "\n".join(lines))


async def members_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/members — Show group member count."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    try:
        count = await context.bot.get_chat_member_count(chat.id)
        await _reply(
            update,
            f"👥 {bold(escape_html(chat.title or 'This group'))} has {bold(format_number(count))} member(s).",
        )
    except TelegramError as e:
        await _reply(update, f"❌ Failed: {escape_html(str(e))}")


async def bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bots — List all bots in the group (from admin list)."""
    if not _group_only_check(update):
        await _reply(update, "⚠️ This command can only be used in groups.")
        return

    chat = update.effective_chat
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        bots = [m for m in admins if m.user.is_bot]
    except TelegramError as e:
        await _reply(update, f"❌ Failed: {escape_html(str(e))}")
        return

    if not bots:
        await _reply(update, "🤖 No bots with admin rights found in this group.")
        return

    lines = [f"🤖 {bold('Bots in this group')} (admin bots):", ""]
    for bot_member in bots:
        name = escape_html(bot_member.user.full_name)
        lines.append(f"• {user_mention(bot_member.user.id, name)}")
        if bot_member.user.username:
            lines.append(f"  └ @{bot_member.user.username}")

    await _reply(update, "\n".join(lines))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — Show chat activity statistics from DB."""
    chat = update.effective_chat
    if not chat:
        return

    today = datetime.now(timezone.utc).date()

    async with get_session() as session:
        result = await session.execute(
            select(Analytics).where(Analytics.chat_id == chat.id).order_by(Analytics.date.desc())
        )
        records = result.scalars().all()

    if not records:
        await _reply(update, "📊 No statistics available yet for this group.")
        return

    total_msgs = sum(r.messages for r in records)
    total_joins = sum(r.joins for r in records)
    total_leaves = sum(r.leaves for r in records)
    total_commands = sum(r.commands_used for r in records)

    # Today's stats
    today_record = next((r for r in records if r.date.date() == today), None)
    today_msgs = today_record.messages if today_record else 0

    lines = [
        f"📊 {bold('Statistics for')} {escape_html(chat.title or 'this group')}",
        "",
        f"📅 {bold('Today:')} {format_number(today_msgs)} messages",
        f"💬 {bold('Total messages:')} {format_number(total_msgs)}",
        f"👥 {bold('Total joins:')} {format_number(total_joins)}",
        f"👋 {bold('Total leaves:')} {format_number(total_leaves)}",
        f"⌨️ {bold('Commands used:')} {format_number(total_commands)}",
        f"📆 {bold('Days tracked:')} {len(records)}",
    ]

    await _reply(update, "\n".join(lines))


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("promote", promote_command))
    app.add_handler(CommandHandler("demote", demote_command))
    app.add_handler(CommandHandler("title", title_command))
    app.add_handler(CommandHandler("pin", pin_command))
    app.add_handler(CommandHandler("unpin", unpin_command))
    app.add_handler(CommandHandler("unpinall", unpinall_command))
    app.add_handler(CommandHandler("invite", invite_command))
    app.add_handler(CommandHandler("revokeinvite", revokeinvite_command))
    app.add_handler(CommandHandler("setdescription", setdescription_command))
    app.add_handler(CommandHandler("settitle", settitle_command))
    app.add_handler(CommandHandler(["admins", "adminlist"], admins_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("chatinfo", chatinfo_command))
    app.add_handler(CommandHandler("members", members_command))
    app.add_handler(CommandHandler("bots", bots_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(unpinall_callback, pattern=r"^unpinall_"))
