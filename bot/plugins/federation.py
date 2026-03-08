"""bot/plugins/federation.py – Federation system."""
from __future__ import annotations
import uuid
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from sqlalchemy import select, delete
from bot.database.connection import get_session
from bot.database.models import Federation, FedBan


async def newfed_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    name = " ".join(ctx.args or [])
    if not name:
        await update.message.reply_text("Usage: /newfed <name>")
        return
    user_id = update.effective_user.id
    fed_id = str(uuid.uuid4())[:8]
    async with get_session() as db:
        fed = Federation(fed_id=fed_id, name=name, owner_id=user_id)
        db.add(fed)
        await db.commit()
    await update.message.reply_text(f"✅ Federation *{name}* created!\nID: `{fed_id}`", parse_mode="Markdown")


async def joinfed_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type == "private":
        await update.message.reply_text("Use this in a group.")
        return
    fed_id = ctx.args[0] if ctx.args else ""
    if not fed_id:
        await update.message.reply_text("Usage: /joinfed <fed_id>")
        return
    async with get_session() as db:
        fed = await db.get(Federation, fed_id)
        if not fed:
            await update.message.reply_text("❌ Federation not found.")
            return
        if fed.chat_id:
            fed.chat_id = f"{fed.chat_id},{update.effective_chat.id}"
        else:
            fed.chat_id = str(update.effective_chat.id)
        await db.commit()
    await update.message.reply_text(f"✅ Joined federation *{fed.name}*.", parse_mode="Markdown")


async def leavefed_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type == "private":
        await update.message.reply_text("Use this in a group.")
        return
    chat_id = str(update.effective_chat.id)
    async with get_session() as db:
        result = await db.execute(select(Federation))
        feds = result.scalars().all()
        found = None
        for f in feds:
            ids = (f.chat_id or "").split(",")
            if chat_id in ids:
                ids.remove(chat_id)
                f.chat_id = ",".join(ids)
                found = f
                break
        if found:
            await db.commit()
            await update.message.reply_text(f"✅ Left federation *{found.name}*.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ This group is not in any federation.")


async def fedinfo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with get_session() as db:
        result = await db.execute(select(Federation))
        feds = result.scalars().all()
        for f in feds:
            if chat_id in (f.chat_id or "").split(","):
                bans_r = await db.execute(select(FedBan).where(FedBan.fed_id == f.fed_id))
                ban_count = len(bans_r.scalars().all())
                await update.message.reply_text(
                    f"🏛 *{f.name}*\nID: `{f.fed_id}`\nOwner: `{f.owner_id}`\nBans: {ban_count}",
                    parse_mode="Markdown",
                )
                return
    await update.message.reply_text("This group is not in any federation.")


async def fban_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message and not ctx.args:
        await update.message.reply_text("Reply to a user or provide @username and optional reason.")
        return
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        target_id = target.id
        reason = " ".join(ctx.args or [])
    else:
        await update.message.reply_text("Please reply to the user to fban.")
        return
    chat_id = str(update.effective_chat.id)
    async with get_session() as db:
        result = await db.execute(select(Federation))
        feds = result.scalars().all()
        for f in feds:
            if chat_id in (f.chat_id or "").split(","):
                existing = await db.execute(select(FedBan).where(FedBan.fed_id == f.fed_id, FedBan.user_id == target_id))
                if not existing.scalar_one_or_none():
                    db.add(FedBan(fed_id=f.fed_id, user_id=target_id, reason=reason))
                    await db.commit()
                await update.message.reply_text(f"✅ FedBanned `{target_id}` in *{f.name}*.", parse_mode="Markdown")
                return
    await update.message.reply_text("This group is not in any federation.")


async def unfban_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the user to unfban.")
        return
    target_id = update.message.reply_to_message.from_user.id
    chat_id = str(update.effective_chat.id)
    async with get_session() as db:
        result = await db.execute(select(Federation))
        feds = result.scalars().all()
        for f in feds:
            if chat_id in (f.chat_id or "").split(","):
                await db.execute(delete(FedBan).where(FedBan.fed_id == f.fed_id, FedBan.user_id == target_id))
                await db.commit()
                await update.message.reply_text(f"✅ Removed FedBan for `{target_id}` in *{f.name}*.", parse_mode="Markdown")
                return
    await update.message.reply_text("This group is not in any federation.")


async def fedbanlist_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    async with get_session() as db:
        result = await db.execute(select(Federation))
        feds = result.scalars().all()
        for f in feds:
            if chat_id in (f.chat_id or "").split(","):
                bans_r = await db.execute(select(FedBan).where(FedBan.fed_id == f.fed_id))
                bans = bans_r.scalars().all()
                if not bans:
                    await update.message.reply_text("No fed bans.")
                    return
                lines = [f"`{b.user_id}` – {b.reason or 'No reason'}" for b in bans[:50]]
                await update.message.reply_text("🚫 FedBan list:\n" + "\n".join(lines), parse_mode="Markdown")
                return
    await update.message.reply_text("This group is not in any federation.")


def setup(app: Application) -> None:
    app.add_handler(CommandHandler("newfed", newfed_cmd))
    app.add_handler(CommandHandler("joinfed", joinfed_cmd))
    app.add_handler(CommandHandler("leavefed", leavefed_cmd))
    app.add_handler(CommandHandler("fedinfo", fedinfo_cmd))
    app.add_handler(CommandHandler("fban", fban_cmd))
    app.add_handler(CommandHandler("unfban", unfban_cmd))
    app.add_handler(CommandHandler("fedbanlist", fedbanlist_cmd))
