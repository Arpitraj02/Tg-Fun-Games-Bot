"""
bot/plugins/welcome.py
──────────────────────
Welcome / goodbye messages, captcha verification, group rules,
and member join/leave event handlers.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from telegram import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.database.connection import get_session
from bot.database.models import Group
from bot.helpers.decorators import admin_only, bot_admin_required, can_restrict_members
from bot.helpers.formatters import bold, code, escape_html, italic, user_mention

logger = logging.getLogger(__name__)

# ── In-memory captcha state ───────────────────────────────────────────────────
# key: (chat_id, user_id) → {"type", "answer", "message_id", "task"}
_pending_captchas: Dict[Tuple[int, int], Dict] = {}

_MUTE_PERMS = ChatPermissions(can_send_messages=False)
_FULL_PERMS = ChatPermissions(
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
    can_invite_users=True,
)

DEFAULT_WELCOME = "👋 Welcome to {chat}, {mention}! You are member #{count}."
DEFAULT_GOODBYE = "👋 {first} has left the group. Goodbye!"
DEFAULT_RULES = "No rules set. Use /setrules to configure."


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_group(session, chat_id: int, title: str = "") -> Group:
    """Fetch or create the Group DB record."""
    result = await session.execute(select(Group).where(Group.chat_id == chat_id))
    group = result.scalar_one_or_none()
    if not group:
        group = Group(chat_id=chat_id, title=title)
        session.add(group)
    return group


def _format_message(template: str, user, chat, member_count: int) -> str:
    """Replace {variable} placeholders in welcome/goodbye templates."""
    username = f"@{user.username}" if user.username else user.first_name
    mention = user_mention(user.id, escape_html(user.first_name))
    return (
        template
        .replace("{mention}", mention)
        .replace("{first}", escape_html(user.first_name or ""))
        .replace("{last}", escape_html(user.last_name or ""))
        .replace("{username}", escape_html(username))
        .replace("{count}", str(member_count))
        .replace("{chat}", escape_html(chat.title or "this group"))
        .replace("{id}", str(user.id))
    )


def _parse_inline_buttons(text: str) -> Tuple[str, InlineKeyboardMarkup | None]:
    """
    Parse [Button Text](url) markers from template text.
    Buttons on the same line form a row; different lines form different rows.
    Returns (cleaned_text, keyboard or None).
    """
    button_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
    lines = text.split("\n")
    clean_lines: List[str] = []
    rows: List[List[InlineKeyboardButton]] = []

    for line in lines:
        buttons_in_line = button_pattern.findall(line)
        if buttons_in_line:
            row = [InlineKeyboardButton(label, url=url) for label, url in buttons_in_line]
            rows.append(row)
            # Remove button markers from line
            cleaned = button_pattern.sub("", line).strip()
            if cleaned:
                clean_lines.append(cleaned)
        else:
            clean_lines.append(line)

    keyboard = InlineKeyboardMarkup(rows) if rows else None
    return "\n".join(clean_lines).strip(), keyboard


async def _get_member_count(bot, chat_id: int) -> int:
    try:
        return await bot.get_chat_member_count(chat_id)
    except TelegramError:
        return 0


async def _kick_on_captcha_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: kick user who failed captcha within timeout."""
    data = context.job.data  # type: ignore[union-attr]
    chat_id: int = data["chat_id"]
    user_id: int = data["user_id"]
    msg_id: int = data.get("message_id", 0)

    key = (chat_id, user_id)
    if key not in _pending_captchas:
        return  # Already verified

    _pending_captchas.pop(key, None)

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramError:
        pass

    if msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except TelegramError:
            pass

    logger.info("Captcha timeout: kicked user=%s from chat=%s", user_id, chat_id)


# ── Welcome / Goodbye settings ────────────────────────────────────────────────

@admin_only
async def welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/welcome — Show the current welcome message."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")

    msg = group.welcome_msg or DEFAULT_WELCOME
    await update.effective_message.reply_text(
        f"👋 {bold('Current welcome message:')}\n\n{escape_html(msg)}\n\n"
        f"{italic('Variables: {mention}, {first}, {last}, {username}, {count}, {chat}')}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def setwelcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setwelcome <text> — Set welcome message with variable support."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    msg = update.effective_message
    text = " ".join(context.args) if context.args else ""

    # Support setting via replied message
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or msg.reply_to_message.caption or ""

    if not text:
        await msg.reply_text(
            f"❌ Usage: {code('/setwelcome &lt;text&gt;')}\n\n"
            f"Variables: {code('{mention}')} {code('{first}')} {code('{last}')} "
            f"{code('{username}')} {code('{count}')} {code('{chat}')}",
            parse_mode=ParseMode.HTML,
        )
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        group.welcome_msg = text

        # If replied message has a photo/document, save the file_id too
        if msg.reply_to_message:
            from bot.helpers.utils import get_file_id, get_file_type
            ftype = get_file_type(msg.reply_to_message)
            fid = get_file_id(msg.reply_to_message)
            if fid:
                group.welcome_file_id = fid
                group.welcome_file_type = ftype

        await session.commit()

    await msg.reply_text(
        f"✅ {bold('Welcome message updated!')}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def resetwelcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resetwelcome — Reset welcome message to default."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        group.welcome_msg = None
        group.welcome_file_id = None
        group.welcome_file_type = None
        await session.commit()

    await update.effective_message.reply_text(
        f"🔄 {bold('Welcome message reset to default.')}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def goodbye_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/goodbye — Show current goodbye message."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")

    msg = group.goodbye_msg or DEFAULT_GOODBYE
    await update.effective_message.reply_text(
        f"👋 {bold('Current goodbye message:')}\n\n{escape_html(msg)}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def setgoodbye_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setgoodbye <text> — Set goodbye message."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    msg = update.effective_message
    text = " ".join(context.args) if context.args else ""
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or ""

    if not text:
        await msg.reply_text(
            f"❌ Usage: {code('/setgoodbye &lt;text&gt;')}", parse_mode=ParseMode.HTML
        )
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        group.goodbye_msg = text
        await session.commit()

    await msg.reply_text(f"✅ {bold('Goodbye message updated!')}", parse_mode=ParseMode.HTML)


@admin_only
async def resetgoodbye_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resetgoodbye — Reset goodbye message to default."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        group.goodbye_msg = None
        await session.commit()

    await update.effective_message.reply_text(
        f"🔄 {bold('Goodbye message reset to default.')}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def welcometest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/welcometest — Preview the welcome message."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return
    if chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")

    template = group.welcome_msg or DEFAULT_WELCOME
    count = await _get_member_count(context.bot, chat.id)
    formatted, keyboard = _parse_inline_buttons(
        _format_message(template, user, chat, count)
    )

    if group.welcome_file_id and group.welcome_file_type:
        await _send_media_message(
            context.bot, chat.id, group.welcome_file_type, group.welcome_file_id,
            caption=formatted, keyboard=keyboard
        )
    else:
        await update.effective_message.reply_text(
            f"👁️ {bold('Welcome message preview:')}\n\n{formatted}",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def _send_media_message(bot, chat_id, file_type, file_id, caption="", keyboard=None):
    """Send a media message with caption."""
    send_map = {
        "photo": bot.send_photo,
        "video": bot.send_video,
        "audio": bot.send_audio,
        "document": bot.send_document,
        "sticker": bot.send_sticker,
        "animation": bot.send_animation,
    }
    fn = send_map.get(file_type)
    if fn:
        kwargs = {"chat_id": chat_id, file_type: file_id}
        if file_type != "sticker":
            kwargs["caption"] = caption
            kwargs["parse_mode"] = ParseMode.HTML
            kwargs["reply_markup"] = keyboard
        try:
            return await fn(**kwargs)
        except TelegramError:
            pass
    # Fallback to text
    return await bot.send_message(
        chat_id=chat_id,
        text=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ── Captcha settings ──────────────────────────────────────────────────────────

@admin_only
async def captcha_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/captcha <on|off> — Toggle captcha for new members."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text(
            f"❌ Usage: {code('/captcha &lt;on|off&gt;')}", parse_mode=ParseMode.HTML
        )
        return

    enabled = context.args[0].lower() == "on"
    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        group.captcha_enabled = enabled
        await session.commit()

    status = "✅ enabled" if enabled else "❌ disabled"
    await update.effective_message.reply_text(
        f"🔒 Captcha {bold(status)}.", parse_mode=ParseMode.HTML
    )


@admin_only
async def captchamode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/captchamode <button|math|text> — Set captcha type."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    valid = ("button", "math", "text")
    if not context.args or context.args[0].lower() not in valid:
        await update.effective_message.reply_text(
            f"❌ Usage: {code('/captchamode &lt;button|math|text&gt;')}", parse_mode=ParseMode.HTML
        )
        return

    mode = context.args[0].lower()
    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        group.captcha_type = mode
        await session.commit()

    await update.effective_message.reply_text(
        f"🔒 Captcha mode set to {bold(mode)}.", parse_mode=ParseMode.HTML
    )


@admin_only
async def captchatime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/captchatime <seconds> — Set captcha timeout (30-300s)."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            f"❌ Usage: {code('/captchatime &lt;30-300&gt;')}", parse_mode=ParseMode.HTML
        )
        return

    timeout = int(context.args[0])
    if not 30 <= timeout <= 300:
        await update.effective_message.reply_text("❌ Timeout must be between 30 and 300 seconds.")
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        settings = dict(group.settings or {})
        settings["captcha_time"] = timeout
        group.settings = settings
        await session.commit()

    await update.effective_message.reply_text(
        f"⏱️ Captcha timeout set to {bold(str(timeout))} seconds.", parse_mode=ParseMode.HTML
    )


@admin_only
async def welcomemute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/welcomemute <on|off> — Mute new members until captcha is solved."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text(
            f"❌ Usage: {code('/welcomemute &lt;on|off&gt;')}", parse_mode=ParseMode.HTML
        )
        return

    enabled = context.args[0].lower() == "on"
    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        settings = dict(group.settings or {})
        settings["welcome_mute"] = enabled
        group.settings = settings
        await session.commit()

    status = "✅ enabled" if enabled else "❌ disabled"
    await update.effective_message.reply_text(
        f"🔇 Welcome mute {bold(status)}. Members will be muted until they solve the captcha.",
        parse_mode=ParseMode.HTML,
    )


# ── Rules ─────────────────────────────────────────────────────────────────────

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rules — Display the group rules."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")

    rules_text = group.rules or DEFAULT_RULES
    await update.effective_message.reply_text(
        f"📋 {bold('Rules for')} {escape_html(chat.title or 'this group')}:\n\n{escape_html(rules_text)}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def setrules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setrules <text> — Set the group rules."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    msg = update.effective_message
    text = " ".join(context.args) if context.args else ""
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or ""

    if not text:
        await msg.reply_text(
            f"❌ Usage: {code('/setrules &lt;text&gt;')}", parse_mode=ParseMode.HTML
        )
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        group.rules = text
        await session.commit()

    await msg.reply_text(f"✅ {bold('Group rules updated!')}", parse_mode=ParseMode.HTML)


@admin_only
async def resetrules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resetrules — Clear group rules."""
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("⚠️ Groups only.")
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        group.rules = None
        await session.commit()

    await update.effective_message.reply_text(
        f"🔄 {bold('Group rules cleared.')}",
        parse_mode=ParseMode.HTML,
    )


# ── Member join / leave handlers ──────────────────────────────────────────────

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle new_chat_members events — send welcome and apply captcha."""
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg:
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        captcha_enabled = group.captcha_enabled
        captcha_type = group.captcha_type
        captcha_time = (group.settings or {}).get("captcha_time", 120)
        welcome_mute = (group.settings or {}).get("welcome_mute", False)
        welcome_template = group.welcome_msg or DEFAULT_WELCOME
        welcome_file_id = group.welcome_file_id
        welcome_file_type = group.welcome_file_type

    for new_user in (msg.new_chat_members or []):
        if new_user.is_bot:
            continue

        count = await _get_member_count(context.bot, chat.id)
        formatted, keyboard = _parse_inline_buttons(
            _format_message(welcome_template, new_user, chat, count)
        )

        if captcha_enabled:
            # Apply welcome mute if configured
            if welcome_mute:
                try:
                    await context.bot.restrict_chat_member(
                        chat_id=chat.id, user_id=new_user.id, permissions=_MUTE_PERMS
                    )
                except TelegramError:
                    pass

            sent = await _send_captcha(
                context, chat, new_user, captcha_type, captcha_time, formatted, keyboard
            )
        else:
            # Just send welcome message
            if welcome_file_id and welcome_file_type:
                await _send_media_message(
                    context.bot, chat.id, welcome_file_type, welcome_file_id,
                    caption=formatted, keyboard=keyboard
                )
            else:
                try:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=formatted,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                    )
                except TelegramError:
                    pass


async def _send_captcha(
    context: ContextTypes.DEFAULT_TYPE,
    chat,
    user,
    captcha_type: str,
    timeout: int,
    welcome_text: str,
    welcome_keyboard: Optional[InlineKeyboardMarkup],
) -> Optional[Message]:
    """Send captcha challenge and schedule timeout kick."""
    key = (chat.id, user.id)
    mention = user_mention(user.id, escape_html(user.first_name))

    if captcha_type == "math":
        a = random.randint(1, 20)
        b = random.randint(1, 20)
        op = random.choice(["+", "-", "*"])
        if op == "+":
            answer = str(a + b)
        elif op == "-":
            answer = str(a - b)
        else:
            answer = str(a * b)
        question = f"{a} {op} {b} = ?"
        captcha_text = (
            f"{welcome_text}\n\n"
            f"🧮 {bold('Captcha:')} Solve to verify!\n"
            f"❓ {bold(question)}\n"
            f"⏱️ You have {timeout} seconds. Reply with the answer."
        )
        _pending_captchas[key] = {
            "type": "math",
            "answer": answer,
            "chat_id": chat.id,
            "user_id": user.id,
        }
        try:
            sent = await context.bot.send_message(
                chat_id=chat.id,
                text=captcha_text,
                parse_mode=ParseMode.HTML,
            )
            _pending_captchas[key]["message_id"] = sent.message_id
        except TelegramError:
            return None

    elif captcha_type == "text":
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        answer = "".join(random.choices(chars, k=6))
        captcha_text = (
            f"{welcome_text}\n\n"
            f"🔤 {bold('Captcha:')} Type the following code to verify!\n"
            f"📝 Code: {code(answer)}\n"
            f"⏱️ You have {timeout} seconds."
        )
        _pending_captchas[key] = {
            "type": "text",
            "answer": answer,
            "chat_id": chat.id,
            "user_id": user.id,
        }
        try:
            sent = await context.bot.send_message(
                chat_id=chat.id,
                text=captcha_text,
                parse_mode=ParseMode.HTML,
            )
            _pending_captchas[key]["message_id"] = sent.message_id
        except TelegramError:
            return None

    else:  # button (default)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ I'm not a bot — Click to verify!",
                        callback_data=f"captcha_verify:{chat.id}:{user.id}",
                    )
                ]
            ]
        )
        captcha_text = (
            f"{welcome_text}\n\n"
            f"🔒 {mention}, please click the button below to verify you are human.\n"
            f"⏱️ You have {timeout} seconds."
        )
        _pending_captchas[key] = {
            "type": "button",
            "answer": None,
            "chat_id": chat.id,
            "user_id": user.id,
        }
        try:
            sent = await context.bot.send_message(
                chat_id=chat.id,
                text=captcha_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            _pending_captchas[key]["message_id"] = sent.message_id
        except TelegramError:
            return None

    # Schedule kick on timeout
    context.job_queue.run_once(
        _kick_on_captcha_timeout,
        when=timeout,
        data={"chat_id": chat.id, "user_id": user.id, "message_id": _pending_captchas[key].get("message_id", 0)},
        name=f"captcha_{chat.id}_{user.id}",
    )
    return None


async def captcha_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button captcha verification."""
    query = update.callback_query
    if not query:
        return

    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("Invalid captcha data.", show_alert=True)
        return

    chat_id = int(parts[1])
    user_id = int(parts[2])

    # Only the targeted user can click
    if query.from_user.id != user_id:
        await query.answer("🚫 This captcha is not for you!", show_alert=True)
        return

    key = (chat_id, user_id)
    if key not in _pending_captchas:
        await query.answer("✅ Already verified or expired.", show_alert=True)
        return

    _pending_captchas.pop(key, None)

    # Cancel timeout job
    current_jobs = context.job_queue.get_jobs_by_name(f"captcha_{chat_id}_{user_id}")
    for job in current_jobs:
        job.schedule_removal()

    # Unmute the user
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id, permissions=_FULL_PERMS
        )
    except TelegramError:
        pass

    try:
        await query.answer("✅ Verified! Welcome!", show_alert=False)
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass


async def captcha_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check messages for math/text captcha answers."""
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message
    if not chat or not user or not msg or not msg.text:
        return

    key = (chat.id, user.id)
    captcha_data = _pending_captchas.get(key)
    if not captcha_data or captcha_data.get("type") not in ("math", "text"):
        return

    answer = captcha_data.get("answer", "")
    if msg.text.strip() == answer:
        _pending_captchas.pop(key, None)

        # Cancel timeout job
        for job in context.job_queue.get_jobs_by_name(f"captcha_{chat.id}_{user.id}"):
            job.schedule_removal()

        # Unmute
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id, user_id=user.id, permissions=_FULL_PERMS
            )
        except TelegramError:
            pass

        # Delete captcha question and user's answer
        captcha_msg_id = captcha_data.get("message_id")
        for mid in filter(None, [captcha_msg_id, msg.message_id]):
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=mid)
            except TelegramError:
                pass

        try:
            confirmation = await context.bot.send_message(
                chat_id=chat.id,
                text=f"✅ {user_mention(user.id, escape_html(user.first_name))} verified! Welcome!",
                parse_mode=ParseMode.HTML,
            )
            # Auto-delete after 5s
            await asyncio.sleep(5)
            try:
                await confirmation.delete()
            except TelegramError:
                pass
        except TelegramError:
            pass


async def left_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle left_chat_member events — send goodbye message."""
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg or not msg.left_chat_member:
        return

    user = msg.left_chat_member
    if user.is_bot:
        return

    async with get_session() as session:
        group = await _get_group(session, chat.id, chat.title or "")
        goodbye_template = group.goodbye_msg or DEFAULT_GOODBYE

    count = await _get_member_count(context.bot, chat.id)
    formatted, keyboard = _parse_inline_buttons(
        _format_message(goodbye_template, user, chat, count)
    )

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=formatted,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except TelegramError:
        pass


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("welcome", welcome_command))
    app.add_handler(CommandHandler("setwelcome", setwelcome_command))
    app.add_handler(CommandHandler("resetwelcome", resetwelcome_command))
    app.add_handler(CommandHandler("goodbye", goodbye_command))
    app.add_handler(CommandHandler("setgoodbye", setgoodbye_command))
    app.add_handler(CommandHandler("resetgoodbye", resetgoodbye_command))
    app.add_handler(CommandHandler("welcometest", welcometest_command))
    app.add_handler(CommandHandler("captcha", captcha_command))
    app.add_handler(CommandHandler("captchamode", captchamode_command))
    app.add_handler(CommandHandler("captchatime", captchatime_command))
    app.add_handler(CommandHandler("welcomemute", welcomemute_command))
    app.add_handler(CommandHandler("rules", rules_command))
    app.add_handler(CommandHandler("setrules", setrules_command))
    app.add_handler(CommandHandler("resetrules", resetrules_command))

    # Member events
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler)
    )
    app.add_handler(
        MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, left_member_handler)
    )

    # Captcha button callback
    app.add_handler(
        CallbackQueryHandler(captcha_verify_callback, pattern=r"^captcha_verify:")
    )

    # Captcha text/math answer handler (low priority — should not conflict with other handlers)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            captcha_answer_handler,
        ),
        group=10,  # lower priority group
    )
