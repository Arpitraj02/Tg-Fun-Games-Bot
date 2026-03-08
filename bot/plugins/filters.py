"""
bot/plugins/filters.py
──────────────────────
Keyword filters with custom responses and a word blacklist.
Supports regex triggers (prefix ~), file_id responses, and per-filter actions.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from sqlalchemy import delete, func, select
from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters as tg_filters,
)

from bot.database.connection import get_session
from bot.database.models import Blacklist, Filter, Group, Warning
from bot.helpers.decorators import admin_only, bot_admin_required, can_delete_messages, can_restrict_members
from bot.helpers.formatters import bold, code, escape_html, italic, user_mention
from bot.helpers.utils import get_file_id, get_file_type

logger = logging.getLogger(__name__)

_MUTE_PERMS = ChatPermissions(can_send_messages=False)


def _group_only(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def _reply(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


# ── Filter management commands ────────────────────────────────────────────────

@admin_only
async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /filter <trigger> [response] — Add a filter.
    Reply to a message to save its content as the response.
    Prefix trigger with ~ for regex matching.
    """
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    msg = update.effective_message
    args = context.args or []

    if not args:
        await _reply(
            update,
            f"❌ Usage: {code('/filter &lt;trigger&gt; [response]')}\n"
            f"Reply to a message to save its content as the filter response.\n"
            f"Prefix trigger with {code('~')} for regex matching.\n"
            f"Example: {code('/filter hello Hi there!')}",
        )
        return

    trigger = args[0].lower()
    response_text: Optional[str] = " ".join(args[1:]) if len(args) > 1 else None
    file_id: Optional[str] = None
    file_type: Optional[str] = None

    # If replying to a message, use it as response
    if msg.reply_to_message:
        reply = msg.reply_to_message
        if reply.text and not response_text:
            response_text = reply.text
        elif reply.caption and not response_text:
            response_text = reply.caption
        ftype = get_file_type(reply)
        fid = get_file_id(reply)
        if fid:
            file_id = fid
            file_type = ftype

    if not response_text and not file_id:
        await _reply(
            update,
            f"❌ Provide a response text or reply to a message to use as filter response.",
        )
        return

    # Parse inline buttons from response text
    buttons = _extract_buttons(response_text or "")

    async with get_session() as session:
        # Upsert: remove existing if any, then insert
        await session.execute(
            delete(Filter).where(
                Filter.chat_id == chat.id, Filter.trigger == trigger
            )
        )
        new_filter = Filter(
            chat_id=chat.id,
            trigger=trigger,
            response=response_text,
            file_id=file_id,
            file_type=file_type,
            buttons=buttons,
            type=file_type or "text",
        )
        session.add(new_filter)
        await session.commit()

    trigger_display = code(escape_html(trigger))
    await _reply(update, f"✅ Filter {trigger_display} saved.")


@admin_only
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stop <trigger> — Remove a filter."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    if not context.args:
        await _reply(update, f"❌ Usage: {code('/stop &lt;trigger&gt;')}")
        return

    trigger = context.args[0].lower()
    async with get_session() as session:
        result = await session.execute(
            delete(Filter).where(
                Filter.chat_id == chat.id, Filter.trigger == trigger
            )
        )
        await session.commit()
        deleted = result.rowcount

    if deleted:
        await _reply(update, f"✅ Filter {code(escape_html(trigger))} removed.")
    else:
        await _reply(update, f"❌ No filter found for {code(escape_html(trigger))}.")


async def filters_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/filters — List all active filters in this group."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    async with get_session() as session:
        result = await session.execute(
            select(Filter).where(Filter.chat_id == chat.id).order_by(Filter.trigger)
        )
        filter_list: List[Filter] = result.scalars().all()

    if not filter_list:
        await _reply(update, "📭 No filters active in this group.")
        return

    lines = [f"🔍 {bold('Active Filters')} in {escape_html(chat.title or 'this group')}:", ""]
    for f in filter_list:
        icon = "🔤" if f.type == "text" else "📎"
        action_tag = f" → {italic(f.action)}" if f.action else ""
        lines.append(f"{icon} {code(escape_html(f.trigger))}{action_tag}")

    lines.append(f"\n{italic(f'Total: {len(filter_list)} filter(s)')}")
    await _reply(update, "\n".join(lines))


@admin_only
async def addfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addfilter <trigger> — Advanced filter setup with action.
    After trigger, pass action as second arg: warn|mute|ban|delete
    Example: /addfilter badword ban
    """
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    args = context.args or []
    if not args:
        await _reply(
            update,
            f"❌ Usage: {code('/addfilter &lt;trigger&gt; [warn|mute|ban|delete]')}\n"
            f"Reply to a message to use it as the response.",
        )
        return

    trigger = args[0].lower()
    action = args[1].lower() if len(args) > 1 and args[1].lower() in ("warn", "mute", "ban", "delete") else "delete"
    response_text: Optional[str] = " ".join(args[2:]) if len(args) > 2 else None
    file_id: Optional[str] = None
    file_type: Optional[str] = None

    msg = update.effective_message
    if msg.reply_to_message:
        reply = msg.reply_to_message
        response_text = response_text or reply.text or reply.caption
        ftype = get_file_type(reply)
        fid = get_file_id(reply)
        if fid:
            file_id = fid
            file_type = ftype

    buttons = _extract_buttons(response_text or "")

    async with get_session() as session:
        await session.execute(
            delete(Filter).where(Filter.chat_id == chat.id, Filter.trigger == trigger)
        )
        new_filter = Filter(
            chat_id=chat.id,
            trigger=trigger,
            response=response_text,
            file_id=file_id,
            file_type=file_type,
            buttons=buttons,
            type=file_type or "text",
            action=action,
        )
        session.add(new_filter)
        await session.commit()

    await _reply(
        update,
        f"✅ Filter {code(escape_html(trigger))} saved with action {bold(action)}.",
    )


@admin_only
async def filtermode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/filtermode <warn|mute|ban|delete> — Set the default action for filters."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    valid = ("warn", "mute", "ban", "delete")
    if not context.args or context.args[0].lower() not in valid:
        await _reply(
            update,
            f"❌ Usage: {code('/filtermode &lt;warn|mute|ban|delete&gt;')}",
        )
        return

    mode = context.args[0].lower()
    chat = update.effective_chat

    async with get_session() as session:
        result = await session.execute(select(Group).where(Group.chat_id == chat.id))
        group = result.scalar_one_or_none()
        if not group:
            group = Group(chat_id=chat.id, title=chat.title or "")
            session.add(group)
        settings = dict(group.settings or {})
        settings["filter_mode"] = mode
        group.settings = settings
        await session.commit()

    await _reply(update, f"✅ Default filter action set to {bold(mode)}.")


# ── Blacklist management ──────────────────────────────────────────────────────

@admin_only
async def filterwords_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/filterwords <word1> [word2...] — Add words to the blacklist."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    if not context.args:
        await _reply(
            update,
            f"❌ Usage: {code('/filterwords &lt;word1&gt; [word2] ...')}",
        )
        return

    added: List[str] = []
    async with get_session() as session:
        for word in context.args:
            word_lower = word.lower()
            # Check if already exists
            result = await session.execute(
                select(Blacklist).where(
                    Blacklist.chat_id == chat.id, Blacklist.word == word_lower
                )
            )
            if not result.scalar_one_or_none():
                session.add(Blacklist(chat_id=chat.id, word=word_lower, action="delete"))
                added.append(word_lower)
        await session.commit()

    if added:
        await _reply(
            update,
            f"✅ Added {bold(str(len(added)))} word(s) to blacklist: "
            + ", ".join(code(escape_html(w)) for w in added),
        )
    else:
        await _reply(update, "ℹ️ All specified words are already blacklisted.")


@admin_only
async def unfilterword_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unfilterword <word> — Remove a word from the blacklist."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    if not context.args:
        await _reply(update, f"❌ Usage: {code('/unfilterword &lt;word&gt;')}")
        return

    word = context.args[0].lower()
    async with get_session() as session:
        result = await session.execute(
            delete(Blacklist).where(
                Blacklist.chat_id == chat.id, Blacklist.word == word
            )
        )
        await session.commit()
        deleted = result.rowcount

    if deleted:
        await _reply(update, f"✅ Removed {code(escape_html(word))} from blacklist.")
    else:
        await _reply(update, f"❌ Word {code(escape_html(word))} not found in blacklist.")


async def blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/blacklist — Show all blacklisted words."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    async with get_session() as session:
        result = await session.execute(
            select(Blacklist).where(Blacklist.chat_id == chat.id).order_by(Blacklist.word)
        )
        words: List[Blacklist] = result.scalars().all()

    if not words:
        await _reply(update, "📭 No blacklisted words in this group.")
        return

    lines = [f"🚫 {bold('Blacklisted Words')} in {escape_html(chat.title or 'this group')}:", ""]
    for entry in words:
        action_tag = f" ({italic(entry.action)})" if entry.action != "delete" else ""
        lines.append(f"• {code(escape_html(entry.word))}{action_tag}")

    lines.append(f"\n{italic(f'Total: {len(words)} word(s)')}")
    await _reply(update, "\n".join(lines))


# ── Button parsing helper ─────────────────────────────────────────────────────

def _extract_buttons(text: str) -> List[List]:
    """
    Extract [Button Text](url) inline button definitions from text.
    Returns a list of rows, each row is a list of [label, url] pairs.
    """
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
    rows: List[List] = []
    for line in text.split("\n"):
        matches = pattern.findall(line)
        if matches:
            rows.append([[label, url] for label, url in matches])
    return rows


def _build_keyboard_from_buttons(button_data: List[List]) -> Optional[InlineKeyboardMarkup]:
    """Convert stored button data back to InlineKeyboardMarkup."""
    if not button_data:
        return None
    rows: List[List[InlineKeyboardButton]] = []
    for row in button_data:
        if isinstance(row, list) and row:
            if isinstance(row[0], list):
                # Row of buttons: [[label, url], ...]
                rows.append([InlineKeyboardButton(b[0], url=b[1]) for b in row if len(b) == 2])
            elif len(row) == 2 and isinstance(row[0], str):
                # Single button [label, url]
                rows.append([InlineKeyboardButton(row[0], url=row[1])])
    return InlineKeyboardMarkup(rows) if rows else None


# ── Filter text matching ──────────────────────────────────────────────────────

def _matches_filter(text: str, trigger: str) -> bool:
    """Return True if text matches a filter trigger (regex or word match)."""
    text_lower = text.lower()
    if trigger.startswith("~"):
        # Regex mode
        pattern = trigger[1:]
        try:
            return bool(re.search(pattern, text_lower, re.IGNORECASE))
        except re.error:
            return False
    # Exact word match (as separate word)
    return bool(re.search(r"\b" + re.escape(trigger) + r"\b", text_lower))


def _contains_blacklisted(text: str, blacklisted_words: List[str]) -> Optional[str]:
    """Return the first blacklisted word found in text, or None."""
    text_lower = text.lower()
    for word in blacklisted_words:
        if re.search(r"\b" + re.escape(word.lower()) + r"\b", text_lower):
            return word
    return None


# ── Message handler ───────────────────────────────────────────────────────────

async def message_filter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check every group message against active filters and blacklist."""
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user
    if not chat or not msg or not user:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    # Don't apply to admins
    from bot.helpers.utils import is_admin
    if await is_admin(context.bot, chat.id, user.id):
        return

    text = msg.text or msg.caption or ""
    if not text:
        return

    # --- Check blacklist ---
    async with get_session() as session:
        bl_result = await session.execute(
            select(Blacklist).where(Blacklist.chat_id == chat.id)
        )
        blacklisted: List[Blacklist] = bl_result.scalars().all()

        # Load filters
        f_result = await session.execute(
            select(Filter).where(Filter.chat_id == chat.id)
        )
        active_filters: List[Filter] = f_result.scalars().all()

    # Get default filter mode
    async with get_session() as session:
        g_result = await session.execute(select(Group).where(Group.chat_id == chat.id))
        group = g_result.scalar_one_or_none()
    default_filter_action = (group.settings or {}).get("filter_mode", "delete") if group else "delete"

    # Check blacklist
    bl_words = [entry.word for entry in blacklisted]
    matched_word = _contains_blacklisted(text, bl_words)
    if matched_word:
        entry = next((e for e in blacklisted if e.word == matched_word), None)
        action = entry.action if entry else "delete"
        await _take_filter_action(
            context, msg, chat, user, action,
            f"blacklisted word: {code(escape_html(matched_word))}"
        )
        return

    # Check keyword filters
    for f in active_filters:
        if not _matches_filter(text, f.trigger):
            continue

        action = f.action or default_filter_action

        if action == "delete":
            # Just delete and optionally send response
            try:
                await msg.delete()
            except TelegramError:
                pass
        else:
            await _take_filter_action(context, msg, chat, user, action, f"filter: {f.trigger}")

        # Send the filter response if present
        keyboard = _build_keyboard_from_buttons(f.buttons or [])
        if f.file_id and f.file_type:
            await _send_filter_media(context.bot, chat.id, f, keyboard)
        elif f.response:
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=f.response,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            except TelegramError:
                pass
        break  # Only process first matching filter per message


async def _take_filter_action(context, msg: Message, chat, user, action: str, reason: str) -> None:
    """Apply a moderation action from a filter match."""
    mention = user_mention(user.id, escape_html(user.first_name))

    try:
        if action == "delete":
            try:
                await msg.delete()
            except TelegramError:
                pass
            return

        if action == "ban":
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"🔨 {mention} was {bold('banned')} for triggering a filter ({reason}).",
                parse_mode=ParseMode.HTML,
            )
        elif action == "kick":
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"👢 {mention} was {bold('kicked')} for triggering a filter ({reason}).",
                parse_mode=ParseMode.HTML,
            )
        elif action == "mute":
            await context.bot.restrict_chat_member(
                chat_id=chat.id, user_id=user.id, permissions=_MUTE_PERMS
            )
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"🔇 {mention} was {bold('muted')} for triggering a filter ({reason}).",
                parse_mode=ParseMode.HTML,
            )
        elif action == "warn":
            # Increment warning in DB
            async with get_session() as session:
                session.add(
                    Warning(
                        user_id=user.id,
                        chat_id=chat.id,
                        reason=f"Auto-warn: {reason}",
                        warned_by=context.bot.id,
                    )
                )
                await session.commit()
                res = await session.execute(
                    select(func.count(Warning.id)).where(
                        Warning.user_id == user.id, Warning.chat_id == chat.id
                    )
                )
                warn_count = res.scalar_one()

            async with get_session() as session:
                g = await session.execute(select(Group).where(Group.chat_id == chat.id))
                grp = g.scalar_one_or_none()
            warn_limit = grp.warn_limit if grp else 3

            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    f"⚠️ {mention} was {bold('warned')} for {reason}.\n"
                    f"📊 Warns: {warn_count}/{warn_limit}"
                ),
                parse_mode=ParseMode.HTML,
            )

        try:
            await msg.delete()
        except TelegramError:
            pass

    except TelegramError as e:
        logger.warning("Filter action '%s' failed for user=%s: %s", action, user.id, e)


async def _send_filter_media(bot, chat_id: int, f: Filter, keyboard) -> None:
    """Send a filter's media response."""
    send_map = {
        "photo": bot.send_photo,
        "video": bot.send_video,
        "audio": bot.send_audio,
        "document": bot.send_document,
        "sticker": bot.send_sticker,
        "animation": bot.send_animation,
        "voice": bot.send_voice,
    }
    fn = send_map.get(f.file_type or "")
    if fn:
        kwargs: Dict = {"chat_id": chat_id, f.file_type: f.file_id}
        if f.file_type != "sticker":
            if f.response:
                kwargs["caption"] = f.response
                kwargs["parse_mode"] = ParseMode.HTML
            kwargs["reply_markup"] = keyboard
        try:
            await fn(**kwargs)
        except TelegramError as e:
            logger.warning("Could not send filter media: %s", e)


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("filter", filter_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("filters", filters_command))
    app.add_handler(CommandHandler("addfilter", addfilter_command))
    app.add_handler(CommandHandler("filtermode", filtermode_command))
    app.add_handler(CommandHandler("filterwords", filterwords_command))
    app.add_handler(CommandHandler("unfilterword", unfilterword_command))
    app.add_handler(CommandHandler("blacklist", blacklist_command))

    # Message handler for filter/blacklist checking
    app.add_handler(
        MessageHandler(
            (tg_filters.TEXT | tg_filters.CAPTION) & tg_filters.ChatType.GROUPS & ~tg_filters.COMMAND,
            message_filter_handler,
        ),
        group=5,
    )
