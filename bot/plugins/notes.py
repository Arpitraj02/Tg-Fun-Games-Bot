"""
bot/plugins/notes.py
────────────────────
Save and retrieve group notes (snippets) by keyword.
Notes support text, photo, video, document, audio, sticker with inline buttons.
#notename auto-trigger in messages.
/saved — personal saved messages in PM.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from sqlalchemy import delete, select
from telegram import (
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
from bot.database.models import Group, Note, SavedMessage
from bot.helpers.decorators import admin_only
from bot.helpers.formatters import bold, code, escape_html, italic
from bot.helpers.utils import get_file_id, get_file_type

logger = logging.getLogger(__name__)

# Regex to detect #notename in messages (word boundary, starts with #)
_NOTE_PATTERN = re.compile(r"(?<!\S)#([a-zA-Z][a-zA-Z0-9_]{0,63})(?!\S)")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _group_only(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def _reply(update: Update, text: str, markup=None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


def _parse_buttons_from_text(text: str):
    """
    Parse [Button Text](url) from note content.
    Returns (cleaned_text, list_of_rows) where each row is [[label, url], ...].
    """
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
    rows: List[List] = []
    clean_lines: List[str] = []

    for line in text.split("\n"):
        matches = pattern.findall(line)
        if matches:
            rows.append([[label, url] for label, url in matches])
            cleaned = pattern.sub("", line).strip()
            if cleaned:
                clean_lines.append(cleaned)
        else:
            clean_lines.append(line)

    return "\n".join(clean_lines).strip(), rows


def _build_keyboard(button_data: List) -> Optional[InlineKeyboardMarkup]:
    """Convert stored button_data rows to InlineKeyboardMarkup."""
    if not button_data:
        return None
    rows: List[List[InlineKeyboardButton]] = []
    for row in button_data:
        if isinstance(row, list) and row:
            if isinstance(row[0], list):
                rows.append([InlineKeyboardButton(b[0], url=b[1]) for b in row if len(b) == 2])
            elif len(row) == 2 and isinstance(row[0], str):
                rows.append([InlineKeyboardButton(row[0], url=row[1])])
    return InlineKeyboardMarkup(rows) if rows else None


async def _send_note(bot, chat_id: int, note: Note, reply_to: Optional[int] = None) -> None:
    """Send a note to a chat, handling all media types."""
    keyboard = _build_keyboard(note.buttons or [])
    content = note.content or ""
    clean_text, _ = _parse_buttons_from_text(content)

    common_kwargs = {
        "chat_id": chat_id,
        "reply_to_message_id": reply_to,
        "reply_markup": keyboard,
        "parse_mode": ParseMode.HTML,
    }

    try:
        if note.file_type == "photo" and note.file_id:
            await bot.send_photo(
                photo=note.file_id,
                caption=clean_text or None,
                **common_kwargs,
            )
        elif note.file_type == "video" and note.file_id:
            await bot.send_video(
                video=note.file_id,
                caption=clean_text or None,
                **common_kwargs,
            )
        elif note.file_type == "audio" and note.file_id:
            await bot.send_audio(
                audio=note.file_id,
                caption=clean_text or None,
                **common_kwargs,
            )
        elif note.file_type == "document" and note.file_id:
            await bot.send_document(
                document=note.file_id,
                caption=clean_text or None,
                **common_kwargs,
            )
        elif note.file_type == "sticker" and note.file_id:
            # Stickers don't support caption or parse_mode
            await bot.send_sticker(
                sticker=note.file_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to,
                reply_markup=keyboard,
            )
            if clean_text:
                await bot.send_message(
                    text=clean_text,
                    **common_kwargs,
                )
        elif note.file_type == "animation" and note.file_id:
            await bot.send_animation(
                animation=note.file_id,
                caption=clean_text or None,
                **common_kwargs,
            )
        elif note.file_type == "voice" and note.file_id:
            await bot.send_voice(
                voice=note.file_id,
                caption=clean_text or None,
                **common_kwargs,
            )
        else:
            # Text-only note
            text_to_send = clean_text or italic("(empty note)")
            await bot.send_message(
                text=text_to_send,
                **common_kwargs,
            )
    except TelegramError as e:
        logger.warning("Could not send note '%s': %s", note.name, e)
        # Fallback: send as text
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=clean_text or italic("(empty note)"),
                parse_mode=ParseMode.HTML,
                reply_to_message_id=reply_to,
            )
        except TelegramError:
            pass


async def _get_group_settings(chat_id: int) -> dict:
    """Load group settings dict from DB."""
    async with get_session() as session:
        result = await session.execute(select(Group).where(Group.chat_id == chat_id))
        group = result.scalar_one_or_none()
    return dict(group.settings or {}) if group else {}


# ── Commands ──────────────────────────────────────────────────────────────────

@admin_only
async def save_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /save <name> [text] — Save a note.
    Reply to a message to save its content (text, photo, video, etc.).
    Inline button syntax: [Button Label](url)
    """
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    msg = update.effective_message
    actor = update.effective_user
    args = context.args or []

    if not args:
        await _reply(
            update,
            f"❌ Usage: {code('/save &lt;name&gt; [text]')}\n"
            f"Or reply to a message: {code('/save mynotename')}",
        )
        return

    note_name = args[0].lower().strip("#")
    note_text: Optional[str] = " ".join(args[1:]) if len(args) > 1 else None
    file_id: Optional[str] = None
    file_type: Optional[str] = None

    # Content from replied message
    if msg.reply_to_message:
        reply = msg.reply_to_message
        if reply.text and not note_text:
            note_text = reply.text
        elif reply.caption and not note_text:
            note_text = reply.caption
        ftype = get_file_type(reply)
        fid = get_file_id(reply)
        if fid:
            file_id = fid
            file_type = ftype

    if not note_text and not file_id:
        await _reply(update, "❌ Provide note content or reply to a message.")
        return

    # Parse inline buttons from note text
    buttons: List[List] = []
    if note_text:
        _, buttons = _parse_buttons_from_text(note_text)

    async with get_session() as session:
        # Remove existing note with same name in this chat
        await session.execute(
            delete(Note).where(Note.chat_id == chat.id, Note.name == note_name)
        )
        note = Note(
            chat_id=chat.id,
            name=note_name,
            content=note_text,
            file_id=file_id,
            file_type=file_type,
            buttons=buttons,
            created_by=actor.id,
        )
        session.add(note)
        await session.commit()

    await _reply(
        update,
        f"📝 Note {code(escape_html(note_name))} saved.\n"
        f"Retrieve it with {code(f'#' + note_name)} or {code('/get ' + note_name)}.",
    )


async def get_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/get <name> — Retrieve a saved note."""
    if not _group_only(update):
        await _reply(update, "⚠️ This command works in groups.")
        return

    if not context.args:
        await _reply(update, f"❌ Usage: {code('/get &lt;name&gt;')}")
        return

    chat = update.effective_chat
    note_name = context.args[0].lower().strip("#")

    async with get_session() as session:
        result = await session.execute(
            select(Note).where(Note.chat_id == chat.id, Note.name == note_name)
        )
        note = result.scalar_one_or_none()

    if not note:
        await _reply(update, f"❌ Note {code(escape_html(note_name))} not found.")
        return

    # Check private notes setting
    settings = await _get_group_settings(chat.id)
    private_notes = settings.get("private_notes", False)

    if private_notes:
        user = update.effective_user
        if user:
            try:
                await _send_note(context.bot, user.id, note)
                await _reply(update, f"📬 Note {code(escape_html(note_name))} sent to your PM.")
            except TelegramError:
                # Can't send PM; fall back to group
                await _send_note(context.bot, chat.id, note, update.effective_message.message_id)
    else:
        await _send_note(context.bot, chat.id, note, update.effective_message.message_id)


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/notes — List all notes in this group."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    async with get_session() as session:
        result = await session.execute(
            select(Note).where(Note.chat_id == chat.id).order_by(Note.name)
        )
        note_list: List[Note] = result.scalars().all()

    if not note_list:
        await _reply(update, "📭 No notes saved in this group.")
        return

    lines = [
        f"📝 {bold('Notes in')} {escape_html(chat.title or 'this group')}:",
        "",
    ]
    for note in note_list:
        icon = {
            "photo": "🖼️",
            "video": "🎬",
            "audio": "🎵",
            "document": "📄",
            "sticker": "🎨",
            "animation": "🎞️",
        }.get(note.file_type or "", "📝")
        lines.append(f"{icon} {code(escape_html(note.name))}")

    lines.append(f"\n{italic(f'Total: {len(note_list)} note(s). Use #name or /get name to retrieve.')}")
    await _reply(update, "\n".join(lines))


@admin_only
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clear <name> — Delete a note."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    if not context.args:
        await _reply(update, f"❌ Usage: {code('/clear &lt;name&gt;')}")
        return

    note_name = context.args[0].lower().strip("#")
    async with get_session() as session:
        result = await session.execute(
            delete(Note).where(Note.chat_id == chat.id, Note.name == note_name)
        )
        await session.commit()
        deleted = result.rowcount

    if deleted:
        await _reply(update, f"🗑️ Note {code(escape_html(note_name))} deleted.")
    else:
        await _reply(update, f"❌ Note {code(escape_html(note_name))} not found.")


@admin_only
async def delnote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delnote <name> — Delete a note (admin alias of /clear)."""
    await clear_command(update, context)


async def saved_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/saved — List your personally saved messages (works in PM)."""
    user = update.effective_user
    if not user:
        return

    chat = update.effective_chat
    if chat and chat.type != ChatType.PRIVATE:
        # If used in a group, notify and redirect to PM
        me = await context.bot.get_me()
        await _reply(
            update,
            f"📬 Your saved messages are available in my PM.\n"
            f"Start a chat: @{me.username}",
        )
        return

    async with get_session() as session:
        result = await session.execute(
            select(SavedMessage)
            .where(SavedMessage.user_id == user.id)
            .order_by(SavedMessage.timestamp.desc())
            .limit(20)
        )
        saved: List[SavedMessage] = result.scalars().all()

    if not saved:
        await _reply(
            update,
            "📭 You have no saved messages.\n\n"
            "Forward any message to me to save it, or use /save in a group.",
        )
        return

    lines = [f"💾 {bold('Your saved messages')} (last 20):", ""]
    for i, sm in enumerate(saved, 1):
        icon = {
            "photo": "🖼️",
            "video": "🎬",
            "audio": "🎵",
            "document": "📄",
            "sticker": "🎨",
            "text": "📝",
        }.get(sm.message_type or "text", "📎")
        preview = (sm.content or "")[:60].replace("\n", " ")
        ts = sm.timestamp.strftime("%Y-%m-%d") if sm.timestamp else ""
        lines.append(f"{i}. {icon} {escape_html(preview)}{' …' if len(sm.content or '') > 60 else ''} {italic(ts)}")

    await _reply(update, "\n".join(lines))


@admin_only
async def privatenotes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/privatenotes <on|off> — Send notes in PM instead of the group."""
    if not _group_only(update):
        await _reply(update, "⚠️ Groups only.")
        return

    chat = update.effective_chat
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await _reply(update, f"❌ Usage: {code('/privatenotes &lt;on|off&gt;')}")
        return

    enabled = context.args[0].lower() == "on"
    async with get_session() as session:
        result = await session.execute(select(Group).where(Group.chat_id == chat.id))
        group = result.scalar_one_or_none()
        if not group:
            group = Group(chat_id=chat.id, title=chat.title or "")
            session.add(group)
        settings = dict(group.settings or {})
        settings["private_notes"] = enabled
        group.settings = settings
        await session.commit()

    status = "✅ enabled" if enabled else "❌ disabled"
    await _reply(update, f"📬 Private notes {bold(status)}.")


# ── #notename message handler ─────────────────────────────────────────────────

async def note_trigger_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Automatically respond to #notename triggers in group messages."""
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    text = msg.text or msg.caption or ""
    matches = _NOTE_PATTERN.findall(text)
    if not matches:
        return

    settings = await _get_group_settings(chat.id)
    private_notes = settings.get("private_notes", False)

    seen: set = set()
    for note_name in matches:
        note_name_lower = note_name.lower()
        if note_name_lower in seen:
            continue
        seen.add(note_name_lower)

        async with get_session() as session:
            result = await session.execute(
                select(Note).where(Note.chat_id == chat.id, Note.name == note_name_lower)
            )
            note = result.scalar_one_or_none()

        if not note:
            continue

        if private_notes:
            user = update.effective_user
            if user:
                try:
                    await _send_note(context.bot, user.id, note)
                    try:
                        await msg.reply_text(
                            f"📬 Note {code(escape_html(note_name_lower))} sent to your PM.",
                            parse_mode=ParseMode.HTML,
                        )
                    except TelegramError:
                        pass
                except TelegramError:
                    await _send_note(context.bot, chat.id, note, msg.message_id)
        else:
            await _send_note(context.bot, chat.id, note, msg.message_id)


# ── Forwarded message saving (in PM) ─────────────────────────────────────────

async def save_forwarded_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In PM, any forwarded message is saved to the user's saved messages."""
    chat = update.effective_chat
    msg = update.effective_message
    user = update.effective_user
    if not chat or not msg or not user:
        return
    if chat.type != ChatType.PRIVATE:
        return
    if not (msg.forward_from or msg.forward_from_chat or msg.forward_sender_name):
        return

    message_type = get_file_type(msg) or "text"
    file_id = get_file_id(msg)
    content = msg.text or msg.caption or ""

    async with get_session() as session:
        saved = SavedMessage(
            user_id=user.id,
            message_type=message_type,
            content=content,
            file_id=file_id,
            file_type=message_type if file_id else None,
        )
        session.add(saved)
        await session.commit()

    try:
        await msg.reply_text(
            f"✅ Message saved! Use /saved to view your saved messages.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("save", save_command))
    app.add_handler(CommandHandler("get", get_command))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler(["clear", "delnote"], clear_command))
    app.add_handler(CommandHandler("saved", saved_command))
    app.add_handler(CommandHandler("privatenotes", privatenotes_command))

    # #notename auto-trigger in group messages
    app.add_handler(
        MessageHandler(
            (tg_filters.TEXT | tg_filters.CAPTION)
            & tg_filters.ChatType.GROUPS
            & ~tg_filters.COMMAND,
            note_trigger_handler,
        ),
        group=6,
    )

    # Save forwarded messages in PM
    app.add_handler(
        MessageHandler(
            tg_filters.FORWARDED & tg_filters.ChatType.PRIVATE,
            save_forwarded_handler,
        )
    )
