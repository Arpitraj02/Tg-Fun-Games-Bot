"""
bot/plugins/stickers.py
────────────────────────
Sticker management: /kang, /stickerinfo, /sticker2img, /img2sticker,
/stickerpack, /delsticker.
"""
from __future__ import annotations

import io
import logging
import os
import re
import tempfile
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode, StickerType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.database.connection import get_session
from bot.database.models import StickerPack
from bot.helpers.formatters import bold, code, escape_html, italic

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads"
)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


async def _reply(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


def _sanitize_pack_name(username: str) -> str:
    """Create a valid sticker pack short name."""
    clean = re.sub(r"[^a-zA-Z0-9_]", "", username)[:32]
    return f"{clean}_by_" + "_pack"


# ── /kang ─────────────────────────────────────────────────────────────────────

async def kang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kang [emoji] — Steal replied sticker/image to personal pack."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    replied = msg.reply_to_message
    if not replied:
        await _reply(update, f"❌ Reply to a sticker or image to kang it!\nUsage: {code('/kang [emoji]')}")
        return

    # Determine emoji to use
    custom_emoji = context.args[0] if context.args else "🌟"

    # Get the bot info for pack names
    me = await context.bot.get_me()
    bot_username = me.username or "bot"

    # Build pack short name: must end with _by_{bot_username}
    clean_user = re.sub(r"[^a-zA-Z0-9]", "", user.username or user.first_name or str(user.id))[:20]
    pack_name = f"{clean_user}_kang_by_{bot_username}"
    pack_title = f"{escape_html(user.first_name or str(user.id))}'s Pack"

    # Check for existing pack in DB
    async with get_session() as session:
        stmt = select(StickerPack).where(StickerPack.user_id == user.id).limit(1)
        db_pack = (await session.execute(stmt)).scalar_one_or_none()
        if db_pack:
            pack_name = db_pack.pack_name
            pack_title = db_pack.pack_link  # reuse field for title

    sticker = replied.sticker
    photo = replied.photo[-1] if replied.photo else None
    document = replied.document

    if not sticker and not photo and not document:
        await _reply(update, "❌ Please reply to a sticker or image.")
        return

    # Download file and prepare sticker
    tmp_path: Optional[str] = None
    is_animated = False
    is_video = False
    sticker_type = StickerType.REGULAR

    try:
        if sticker:
            is_animated = sticker.is_animated
            is_video = sticker.is_video
            sticker_type = sticker.type if hasattr(sticker, 'type') else StickerType.REGULAR
            file = await context.bot.get_file(sticker.file_id)
        elif photo:
            file = await context.bot.get_file(photo.file_id)
        elif document:
            file = await context.bot.get_file(document.file_id)
        else:
            return

        ext = ".webp" if sticker else ".jpg"
        if is_animated:
            ext = ".tgs"
        elif is_video:
            ext = ".webm"

        tmp_path = os.path.join(DOWNLOADS_DIR, f"kang_{user.id}{ext}")
        await file.download_to_drive(tmp_path)

        # Convert image to WebP for static stickers
        if not sticker and _PIL_AVAILABLE and not is_animated and not is_video:
            img = Image.open(tmp_path).convert("RGBA")
            img.thumbnail((512, 512), Image.LANCZOS)
            webp_path = tmp_path.replace(ext, ".webp")
            img.save(webp_path, "WEBP")
            if os.path.exists(tmp_path) and tmp_path != webp_path:
                os.remove(tmp_path)
            tmp_path = webp_path

        # Try adding to existing pack, or create new
        with open(tmp_path, "rb") as f:
            file_data = f.read()

        try:
            await context.bot.add_sticker_to_set(
                user_id=user.id,
                name=pack_name,
                sticker={
                    "sticker": io.BytesIO(file_data),
                    "emoji_list": [custom_emoji],
                    "format": "animated" if is_animated else ("video" if is_video else "static"),
                },
            )
            # Update sticker count in DB
            async with get_session() as session:
                stmt = select(StickerPack).where(StickerPack.user_id == user.id)
                db_pack = (await session.execute(stmt)).scalar_one_or_none()
                if db_pack:
                    db_pack.sticker_count = (db_pack.sticker_count or 0) + 1
                    await session.commit()

            await _reply(update,
                f"✅ {bold('Sticker kanged!')} Added to your pack.\n"
                f"📦 Pack: {bold(escape_html(pack_title))}\n"
                f"🔗 t.me/addstickers/{pack_name}"
            )
        except TelegramError as e:
            if "STICKERSET_INVALID" in str(e) or "not found" in str(e).lower():
                # Create new pack
                try:
                    fmt = "animated" if is_animated else ("video" if is_video else "static")
                    await context.bot.create_new_sticker_set(
                        user_id=user.id,
                        name=pack_name,
                        title=f"{user.first_name or 'User'}'s Pack",
                        stickers=[{
                            "sticker": io.BytesIO(file_data),
                            "emoji_list": [custom_emoji],
                            "format": fmt,
                        }],
                        sticker_format=fmt,
                    )
                    # Save to DB
                    async with get_session() as session:
                        new_pack = StickerPack(
                            user_id=user.id,
                            pack_name=pack_name,
                            pack_link=f"t.me/addstickers/{pack_name}",
                            sticker_count=1,
                        )
                        session.add(new_pack)
                        await session.commit()

                    await _reply(update,
                        f"🎉 {bold('Pack created and sticker added!')}\n"
                        f"📦 Pack: {bold(f'{user.first_name}\'s Pack')}\n"
                        f"🔗 t.me/addstickers/{pack_name}"
                    )
                except TelegramError as e2:
                    await _reply(update, f"❌ Failed to create pack: {escape_html(str(e2))}")
            else:
                await _reply(update, f"❌ Failed to add sticker: {escape_html(str(e))}")

    except TelegramError as e:
        await _reply(update, f"❌ Error: {escape_html(str(e))}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ── /stickerinfo ──────────────────────────────────────────────────────────────

async def stickerinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stickerinfo — Info about replied sticker."""
    msg = update.effective_message
    if not msg:
        return

    replied = msg.reply_to_message
    if not replied or not replied.sticker:
        await _reply(update, "❌ Reply to a sticker to get its info.")
        return

    s = replied.sticker
    pack_name = s.set_name or italic("No pack")
    is_animated = "Yes 🌀" if s.is_animated else "No"
    is_video = "Yes 🎬" if s.is_video else "No"
    emoji = s.emoji or "❓"

    text = (
        f"🎴 {bold('Sticker Info')}\n\n"
        f"📦 {bold('Pack:')} {code(escape_html(pack_name)) if s.set_name else pack_name}\n"
        f"😀 {bold('Emoji:')} {emoji}\n"
        f"🌀 {bold('Animated:')} {is_animated}\n"
        f"🎬 {bold('Video:')} {is_video}\n"
        f"📐 {bold('Size:')} {s.width}×{s.height}px\n"
        f"🆔 {bold('File ID:')} {code(s.file_id[:30])}…"
    )

    markup = None
    if s.set_name:
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("📦 View Pack", url=f"https://t.me/addstickers/{s.set_name}")
        ]])

    await _reply(update, text, markup)


# ── /sticker2img ──────────────────────────────────────────────────────────────

async def sticker2img_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sticker2img — Convert sticker to PNG image."""
    msg = update.effective_message
    if not msg:
        return

    replied = msg.reply_to_message
    if not replied or not replied.sticker:
        await _reply(update, "❌ Reply to a static sticker to convert it.")
        return

    s = replied.sticker
    if s.is_animated or s.is_video:
        await _reply(update, "❌ Only static (.webp) stickers can be converted to PNG.")
        return

    try:
        file = await context.bot.get_file(s.file_id)
        tmp_in = os.path.join(DOWNLOADS_DIR, f"s2i_{msg.message_id}.webp")
        await file.download_to_drive(tmp_in)

        if not _PIL_AVAILABLE:
            await _reply(update, "❌ Image processing library not available.")
            return

        img = Image.open(tmp_in).convert("RGBA")
        out_buf = io.BytesIO()
        img.save(out_buf, "PNG")
        out_buf.seek(0)

        await msg.reply_document(
            document=out_buf,
            filename="sticker.png",
            caption="🖼️ Here's your sticker as PNG!",
        )
    except Exception as e:
        await _reply(update, f"❌ Conversion failed: {escape_html(str(e))}")
    finally:
        if os.path.exists(tmp_in):
            try:
                os.remove(tmp_in)
            except OSError:
                pass


# ── /img2sticker ──────────────────────────────────────────────────────────────

async def img2sticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/img2sticker — Convert replied image to sticker."""
    msg = update.effective_message
    if not msg:
        return

    replied = msg.reply_to_message
    photo = (replied.photo[-1] if replied and replied.photo else None) if replied else None
    document = replied.document if replied else None

    if not photo and not document:
        await _reply(update, "❌ Reply to a photo or image file.")
        return

    if not _PIL_AVAILABLE:
        await _reply(update, "❌ Image processing library not available.")
        return

    try:
        file = await context.bot.get_file(photo.file_id if photo else document.file_id)
        tmp_in = os.path.join(DOWNLOADS_DIR, f"i2s_{msg.message_id}.jpg")
        await file.download_to_drive(tmp_in)

        img = Image.open(tmp_in).convert("RGBA")
        img.thumbnail((512, 512), Image.LANCZOS)

        out_path = os.path.join(DOWNLOADS_DIR, f"i2s_{msg.message_id}.webp")
        img.save(out_path, "WEBP")

        with open(out_path, "rb") as f:
            await msg.reply_sticker(sticker=f)

    except Exception as e:
        await _reply(update, f"❌ Conversion failed: {escape_html(str(e))}")
    finally:
        for p in [tmp_in, out_path if 'out_path' in dir() else ""]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


# ── /stickerpack ──────────────────────────────────────────────────────────────

async def stickerpack_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stickerpack — Manage personal packs."""
    user = update.effective_user
    if not user:
        return

    async with get_session() as session:
        stmt = select(StickerPack).where(StickerPack.user_id == user.id).limit(5)
        packs = (await session.execute(stmt)).scalars().all()

    if not packs:
        await _reply(update,
            f"📦 {bold('Your Sticker Packs')}\n\n"
            f"You don't have any packs yet!\n"
            f"Use {code('/kang')} to create your first pack."
        )
        return

    lines = [f"📦 {bold('Your Sticker Packs')}\n"]
    buttons = []
    for pack in packs:
        lines.append(
            f"• {bold(escape_html(pack.pack_name))}\n"
            f"  📌 {pack.sticker_count or 0} stickers\n"
            f"  🔗 {pack.pack_link}"
        )
        buttons.append([
            InlineKeyboardButton(f"📎 {pack.pack_name[:20]}", url=f"https://{pack.pack_link}"),
        ])

    await _reply(update, "\n".join(lines), InlineKeyboardMarkup(buttons) if buttons else None)


# ── /delsticker ───────────────────────────────────────────────────────────────

async def delsticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delsticker — Delete sticker from pack (reply to sticker)."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    replied = msg.reply_to_message
    if not replied or not replied.sticker:
        await _reply(update, "❌ Reply to a sticker to delete it from its pack.")
        return

    s = replied.sticker
    try:
        await context.bot.delete_sticker_from_set(s.file_id)
        await _reply(update, f"✅ Sticker deleted from pack!")
    except TelegramError as e:
        await _reply(update, f"❌ Failed to delete sticker: {escape_html(str(e))}\n\n{italic('Make sure this sticker is in your pack and the bot is the pack owner.')}")


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("kang", kang_command))
    app.add_handler(CommandHandler("steal", kang_command))
    app.add_handler(CommandHandler("stickerinfo", stickerinfo_command))
    app.add_handler(CommandHandler("sticker2img", sticker2img_command))
    app.add_handler(CommandHandler("img2sticker", img2sticker_command))
    app.add_handler(CommandHandler("stickerpack", stickerpack_command))
    app.add_handler(CommandHandler("mystickers", stickerpack_command))
    app.add_handler(CommandHandler("delsticker", delsticker_command))
