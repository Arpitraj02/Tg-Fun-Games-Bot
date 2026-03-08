"""Owner/sudo supreme controls plugin."""
import asyncio
import io
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stdout
from datetime import datetime

import psutil
from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.config import DATABASE_URL, LOG_FILE, OWNER_ID, SUDO_USERS
from bot.database.connection import get_session
from bot.database.models import (
    Analytics,
    Economy,
    GBan,
    Group,
    Leaderboard,
    User,
)
from bot.helpers.decorators import owner_only, sudo_only
from bot.helpers.formatters import (
    bold,
    code,
    escape_html,
    format_number,
    format_size,
    format_time,
    italic,
    pre,
)
from bot.helpers.utils import extract_user_and_reason

BOT_START_TIME = time.time()
MAINTENANCE_MODE = False
CO_OWNERS: list = []


# ── /gban ──────────────────────────────────────────────────────────────────────
@sudo_only
async def gban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, reason = await extract_user_and_reason(update, context)
    if not user_id:
        await update.message.reply_text("❌ Provide a user to gban.")
        return
    if user_id == OWNER_ID:
        await update.message.reply_text("❌ Can't gban the owner.")
        return
    reason = reason or "No reason provided"
    async with get_session() as session:
        existing = await session.get(GBan, user_id)
        if existing:
            await update.message.reply_text("⚠️ User is already gbanned.")
            return
        gban = GBan(user_id=user_id, reason=reason, banned_by=update.effective_user.id)
        session.add(gban)
        await session.commit()
        groups = (await session.execute(select(Group))).scalars().all()
    banned_count = 0
    for group in groups:
        try:
            await context.bot.ban_chat_member(group.chat_id, user_id)
            banned_count += 1
        except Exception:
            pass
    await update.message.reply_html(
        f"🔨 {bold('Global Ban Executed')}\n\n"
        f"👤 User: <code>{user_id}</code>\n"
        f"📝 Reason: {escape_html(reason)}\n"
        f"🌐 Banned from {bold(str(banned_count))} groups"
    )


# ── /ungban ────────────────────────────────────────────────────────────────────
@sudo_only
async def ungban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await extract_user_and_reason(update, context)
    if not user_id:
        await update.message.reply_text("❌ Provide a user to ungban.")
        return
    async with get_session() as session:
        gban = await session.get(GBan, user_id)
        if not gban:
            await update.message.reply_text("ℹ️ User is not globally banned.")
            return
        await session.delete(gban)
        await session.commit()
        groups = (await session.execute(select(Group))).scalars().all()
    unbanned = 0
    for group in groups:
        try:
            await context.bot.unban_chat_member(group.chat_id, user_id, only_if_banned=True)
            unbanned += 1
        except Exception:
            pass
    await update.message.reply_html(
        f"✅ {bold('Global Ban Removed')}\n\n"
        f"👤 User: <code>{user_id}</code>\n"
        f"🌐 Unbanned from {bold(str(unbanned))} groups"
    )


# ── /gbanlist ──────────────────────────────────────────────────────────────────
@sudo_only
async def gbanlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = int(context.args[0]) if context.args else 1
    per_page = 10
    async with get_session() as session:
        total = (await session.execute(select(func.count(GBan.user_id)))).scalar()
        gbans = (
            await session.execute(
                select(GBan).offset((page - 1) * per_page).limit(per_page)
            )
        ).scalars().all()
    if not gbans:
        await update.message.reply_text("✅ No globally banned users.")
        return
    lines = [f"🔨 {bold('Global Ban List')} (Page {page})\n"]
    for g in gbans:
        lines.append(f"• <code>{g.user_id}</code> — {escape_html(g.reason or 'N/A')}")
    lines.append(f"\n📊 Total: {total}")
    pages = (total + per_page - 1) // per_page
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"gbanlist:{page-1}"))
    if page < pages:
        buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"gbanlist:{page+1}"))
    markup = InlineKeyboardMarkup([buttons]) if buttons else None
    await update.message.reply_html("\n".join(lines), reply_markup=markup)


async def gbanlist_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    per_page = 10
    async with get_session() as session:
        total = (await session.execute(select(func.count(GBan.user_id)))).scalar()
        gbans = (
            await session.execute(
                select(GBan).offset((page - 1) * per_page).limit(per_page)
            )
        ).scalars().all()
    lines = [f"🔨 {bold('Global Ban List')} (Page {page})\n"]
    for g in gbans:
        lines.append(f"• <code>{g.user_id}</code> — {escape_html(g.reason or 'N/A')}")
    pages = (total + per_page - 1) // per_page
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"gbanlist:{page-1}"))
    if page < pages:
        buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"gbanlist:{page+1}"))
    markup = InlineKeyboardMarkup([buttons]) if buttons else None
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=markup)


# ── /gbanstats ─────────────────────────────────────────────────────────────────
@sudo_only
async def gbanstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_session() as session:
        total = (await session.execute(select(func.count(GBan.user_id)))).scalar()
    await update.message.reply_html(
        f"📊 {bold('Global Ban Statistics')}\n\n"
        f"🔨 Total gbanned users: {bold(str(total))}"
    )


# ── /broadcast ─────────────────────────────────────────────────────────────────
@sudo_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <text>")
        return
    text = " ".join(context.args)
    async with get_session() as session:
        groups = (await session.execute(select(Group))).scalars().all()
    sent = failed = 0
    msg = await update.message.reply_html(f"📡 Broadcasting to {len(groups)} groups…")
    for group in groups:
        try:
            await context.bot.send_message(group.chat_id, text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1
    await msg.edit_text(
        f"📡 {bold('Broadcast Complete')}\n✅ Sent: {sent}\n❌ Failed: {failed}",
        parse_mode=ParseMode.HTML,
    )


# ── /broadcastpin ──────────────────────────────────────────────────────────────
@sudo_only
async def broadcastpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /broadcastpin <text>")
        return
    text = " ".join(context.args)
    async with get_session() as session:
        groups = (await session.execute(select(Group))).scalars().all()
    sent = pinned = failed = 0
    msg = await update.message.reply_html(f"📌 Broadcasting+pinning to {len(groups)} groups…")
    for group in groups:
        try:
            m = await context.bot.send_message(group.chat_id, text, parse_mode=ParseMode.HTML)
            sent += 1
            try:
                await context.bot.pin_chat_message(group.chat_id, m.message_id)
                pinned += 1
            except Exception:
                pass
        except Exception:
            failed += 1
    await msg.edit_text(
        f"📌 {bold('Broadcast+Pin Complete')}\n✅ Sent: {sent}\n📌 Pinned: {pinned}\n❌ Failed: {failed}",
        parse_mode=ParseMode.HTML,
    )


# ── /addowner / /removeowner ───────────────────────────────────────────────────
@owner_only
async def addowner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await extract_user_and_reason(update, context)
    if not user_id:
        await update.message.reply_text("❌ Provide a user.")
        return
    if user_id not in CO_OWNERS:
        CO_OWNERS.append(user_id)
    await update.message.reply_html(f"✅ <code>{user_id}</code> added as co-owner.")


@owner_only
async def removeowner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await extract_user_and_reason(update, context)
    if not user_id:
        await update.message.reply_text("❌ Provide a user.")
        return
    if user_id in CO_OWNERS:
        CO_OWNERS.remove(user_id)
    await update.message.reply_html(f"✅ <code>{user_id}</code> removed from co-owners.")


# ── /addsudo / /removesudo / /sudolist ─────────────────────────────────────────
@owner_only
async def addsudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await extract_user_and_reason(update, context)
    if not user_id:
        await update.message.reply_text("❌ Provide a user.")
        return
    if user_id not in SUDO_USERS:
        SUDO_USERS.append(user_id)
    await update.message.reply_html(f"✅ <code>{user_id}</code> added as sudo user.")


@owner_only
async def removesudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await extract_user_and_reason(update, context)
    if not user_id:
        await update.message.reply_text("❌ Provide a user.")
        return
    if user_id in SUDO_USERS:
        SUDO_USERS.remove(user_id)
    await update.message.reply_html(f"✅ <code>{user_id}</code> removed from sudo users.")


@sudo_only
async def sudolist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sudo_ids = SUDO_USERS
    if not sudo_ids:
        await update.message.reply_text("ℹ️ No sudo users configured.")
        return
    lines = [f"👑 {bold('Sudo Users')}\n"]
    for uid in sudo_ids:
        lines.append(f"• <code>{uid}</code>")
    await update.message.reply_html("\n".join(lines))


# ── /addcurrency / /removecurrency / /setcurrency ─────────────────────────────
@sudo_only
async def addcurrency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, extra = await extract_user_and_reason(update, context)
    if not user_id or not extra:
        await update.message.reply_text("Usage: /addcurrency @user <amount>")
        return
    try:
        amount = int(extra.strip().split()[0])
    except ValueError:
        await update.message.reply_text("❌ Amount must be an integer.")
        return
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            await update.message.reply_text("❌ User not found.")
            return
        user.coins = (user.coins or 0) + amount
        await session.commit()
    await update.message.reply_html(f"✅ Added {bold(str(amount))} coins to <code>{user_id}</code>. New balance: {user.coins}")


@sudo_only
async def removecurrency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, extra = await extract_user_and_reason(update, context)
    if not user_id or not extra:
        await update.message.reply_text("Usage: /removecurrency @user <amount>")
        return
    try:
        amount = int(extra.strip().split()[0])
    except ValueError:
        await update.message.reply_text("❌ Amount must be an integer.")
        return
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            await update.message.reply_text("❌ User not found.")
            return
        user.coins = max(0, (user.coins or 0) - amount)
        await session.commit()
    await update.message.reply_html(f"✅ Removed {bold(str(amount))} coins from <code>{user_id}</code>. New balance: {user.coins}")


@sudo_only
async def setcurrency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, extra = await extract_user_and_reason(update, context)
    if not user_id or not extra:
        await update.message.reply_text("Usage: /setcurrency @user <amount>")
        return
    try:
        amount = int(extra.strip().split()[0])
    except ValueError:
        await update.message.reply_text("❌ Amount must be an integer.")
        return
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            await update.message.reply_text("❌ User not found.")
            return
        user.coins = amount
        await session.commit()
    await update.message.reply_html(f"✅ Set coins for <code>{user_id}</code> to {bold(str(amount))}.")


# ── /botban / /unbotban ────────────────────────────────────────────────────────
@sudo_only
async def botban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, reason = await extract_user_and_reason(update, context)
    if not user_id:
        await update.message.reply_text("❌ Provide a user.")
        return
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            user = User(user_id=user_id)
            session.add(user)
        user.bot_banned = True
        user.bot_ban_reason = reason or "No reason"
        await session.commit()
    await update.message.reply_html(f"🚫 <code>{user_id}</code> is now bot-banned.\nReason: {escape_html(reason or 'No reason')}")


@sudo_only
async def unbotban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _ = await extract_user_and_reason(update, context)
    if not user_id:
        await update.message.reply_text("❌ Provide a user.")
        return
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            await update.message.reply_text("ℹ️ User not found.")
            return
        user.bot_banned = False
        user.bot_ban_reason = None
        await session.commit()
    await update.message.reply_html(f"✅ Bot-ban removed for <code>{user_id}</code>.")


# ── /maintenance ───────────────────────────────────────────────────────────────
@sudo_only
async def maintenance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MAINTENANCE_MODE
    if not context.args:
        await update.message.reply_text("Usage: /maintenance <on|off>")
        return
    state = context.args[0].lower()
    if state == "on":
        MAINTENANCE_MODE = True
        await update.message.reply_html(f"🔧 {bold('Maintenance mode ON')}\nOnly sudo users can use the bot.")
    elif state == "off":
        MAINTENANCE_MODE = False
        await update.message.reply_html(f"✅ {bold('Maintenance mode OFF')}\nBot is back to normal operation.")
    else:
        await update.message.reply_text("Usage: /maintenance <on|off>")


# ── /globalstats ───────────────────────────────────────────────────────────────
@sudo_only
async def globalstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_session() as session:
        total_users = (await session.execute(select(func.count(User.user_id)))).scalar()
        total_groups = (await session.execute(select(func.count(Group.chat_id)))).scalar()
        total_gbans = (await session.execute(select(func.count(GBan.user_id)))).scalar()
        total_coins = (await session.execute(select(func.sum(User.coins)))).scalar() or 0
    await update.message.reply_html(
        f"🌐 {bold('Global Statistics')}\n\n"
        f"👥 Total users: {bold(format_number(total_users))}\n"
        f"🏘️ Total groups: {bold(format_number(total_groups))}\n"
        f"🔨 Global bans: {bold(format_number(total_gbans))}\n"
        f"💰 Total coins in circulation: {bold(format_number(int(total_coins)))}"
    )


# ── /grouplist ─────────────────────────────────────────────────────────────────
@sudo_only
async def grouplist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = int(context.args[0]) if context.args else 1
    per_page = 10
    async with get_session() as session:
        total = (await session.execute(select(func.count(Group.chat_id)))).scalar()
        groups = (
            await session.execute(select(Group).offset((page - 1) * per_page).limit(per_page))
        ).scalars().all()
    if not groups:
        await update.message.reply_text("ℹ️ No groups registered.")
        return
    lines = [f"🏘️ {bold('Group List')} (Page {page}/{(total+per_page-1)//per_page})\n"]
    for g in groups:
        lines.append(f"• {escape_html(g.title or 'Unknown')} — <code>{g.chat_id}</code>")
    lines.append(f"\n📊 Total: {total}")
    pages = (total + per_page - 1) // per_page
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"grouplist:{page-1}"))
    if page < pages:
        buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"grouplist:{page+1}"))
    markup = InlineKeyboardMarkup([buttons]) if buttons else None
    await update.message.reply_html("\n".join(lines), reply_markup=markup)


async def grouplist_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    per_page = 10
    async with get_session() as session:
        total = (await session.execute(select(func.count(Group.chat_id)))).scalar()
        groups = (
            await session.execute(select(Group).offset((page - 1) * per_page).limit(per_page))
        ).scalars().all()
    lines = [f"🏘️ {bold('Group List')} (Page {page})\n"]
    for g in groups:
        lines.append(f"• {escape_html(g.title or 'Unknown')} — <code>{g.chat_id}</code>")
    pages = (total + per_page - 1) // per_page
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"grouplist:{page-1}"))
    if page < pages:
        buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"grouplist:{page+1}"))
    markup = InlineKeyboardMarkup([buttons]) if buttons else None
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=markup)


# ── /leavegroup ────────────────────────────────────────────────────────────────
@sudo_only
async def leavegroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /leavegroup <chat_id>")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id.")
        return
    try:
        await context.bot.leave_chat(chat_id)
        await update.message.reply_html(f"✅ Left chat <code>{chat_id}</code>.")
    except Exception as e:
        await update.message.reply_html(f"❌ Failed: {escape_html(str(e))}")


# ── /whitelist ─────────────────────────────────────────────────────────────────
@sudo_only
async def whitelist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /whitelist <chat_id>")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id.")
        return
    async with get_session() as session:
        group = await session.get(Group, chat_id)
        if not group:
            group = Group(chat_id=chat_id)
            session.add(group)
        group.whitelisted = True
        await session.commit()
    await update.message.reply_html(f"✅ Chat <code>{chat_id}</code> whitelisted from global actions.")


# ── /update ────────────────────────────────────────────────────────────────────
@sudo_only
async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        f"🔄 {bold('Bot Update Info')}\n\n"
        f"📦 Version: {bold('2.0.0')}\n"
        f"🐍 Python: {bold(platform.python_version())}\n"
        f"📡 PTB: {bold('20.x')}\n"
        f"🖥️ OS: {bold(platform.system())} {platform.release()}\n"
        f"⏱️ Uptime: {bold(format_time(int(time.time() - BOT_START_TIME)))}"
    )


# ── /restart ───────────────────────────────────────────────────────────────────
@owner_only
async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(f"🔄 {bold('Restarting bot…')}")
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ── /exec ──────────────────────────────────────────────────────────────────────
@owner_only
async def exec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /exec <code>")
        return
    code_str = " ".join(context.args)
    stdout_capture = io.StringIO()
    result = ""
    try:
        with redirect_stdout(stdout_capture):
            exec(code_str, {"context": context, "update": update})  # noqa: S102
        result = stdout_capture.getvalue() or "✅ Executed (no output)"
    except Exception:
        result = traceback.format_exc()
    await update.message.reply_html(f"<pre>{escape_html(result[:3000])}</pre>")


# ── /shell ─────────────────────────────────────────────────────────────────────
@owner_only
async def shell_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /shell <cmd>")
        return
    cmd = " ".join(context.args)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode()[:3000] or "(no output)"
    except asyncio.TimeoutError:
        output = "❌ Command timed out (30s)"
    except Exception as e:
        output = str(e)
    await update.message.reply_html(f"<pre>{escape_html(output)}</pre>")


# ── /ping ──────────────────────────────────────────────────────────────────────
@sudo_only
async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.monotonic()
    msg = await update.message.reply_text("🏓 Pinging…")
    tg_ms = (time.monotonic() - start) * 1000
    db_start = time.monotonic()
    async with get_session() as session:
        await session.execute(select(func.count(User.user_id)))
    db_ms = (time.monotonic() - db_start) * 1000
    await msg.edit_text(
        f"🏓 {bold('Pong!')}\n\n"
        f"📡 Telegram: {bold(f'{tg_ms:.1f}ms')}\n"
        f"🗄️ Database: {bold(f'{db_ms:.1f}ms')}",
        parse_mode=ParseMode.HTML,
    )


# ── /speedtest ─────────────────────────────────────────────────────────────────
@sudo_only
async def speedtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_html(f"⏳ {bold('Running speedtest…')}")
    await asyncio.sleep(2)
    await msg.edit_text(
        f"📶 {bold('Speedtest Results')}\n\n"
        f"⬇️ Download: {bold('~95 Mbps')} (simulated)\n"
        f"⬆️ Upload: {bold('~45 Mbps')} (simulated)\n"
        f"📍 Ping: {bold('~12 ms')} (simulated)",
        parse_mode=ParseMode.HTML,
    )


# ── /systeminfo ────────────────────────────────────────────────────────────────
@sudo_only
async def systeminfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    await update.message.reply_html(
        f"🖥️ {bold('System Information')}\n\n"
        f"🔲 CPU: {bold(f'{cpu:.1f}%')}\n"
        f"🧠 RAM: {bold(format_size(ram.used))} / {format_size(ram.total)} ({ram.percent:.1f}%)\n"
        f"💾 Disk: {bold(format_size(disk.used))} / {format_size(disk.total)} ({disk.percent:.1f}%)\n"
        f"🐍 Python: {bold(platform.python_version())}\n"
        f"🖥️ OS: {bold(platform.system())} {platform.release()}\n"
        f"🏗️ Machine: {bold(platform.machine())}"
    )


# ── /uptime ────────────────────────────────────────────────────────────────────
@sudo_only
async def uptime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    elapsed = int(time.time() - BOT_START_TIME)
    await update.message.reply_html(
        f"⏱️ {bold('Bot Uptime')}: {bold(format_time(elapsed))}"
    )


# ── /usage ─────────────────────────────────────────────────────────────────────
@sudo_only
async def usage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proc = psutil.Process(os.getpid())
    cpu = proc.cpu_percent(interval=0.5)
    mem = proc.memory_info()
    await update.message.reply_html(
        f"📊 {bold('Resource Usage')}\n\n"
        f"🔲 Process CPU: {bold(f'{cpu:.1f}%')}\n"
        f"🧠 Process RAM: {bold(format_size(mem.rss))}\n"
        f"🧵 Threads: {bold(str(proc.num_threads()))}"
    )


# ── /logs ──────────────────────────────────────────────────────────────────────
@sudo_only
async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = int(context.args[0]) if context.args else 20
    log_file = LOG_FILE
    if not os.path.exists(log_file):
        await update.message.reply_text("ℹ️ Log file not found.")
        return
    with open(log_file) as f:
        lines = f.readlines()
    tail = "".join(lines[-n:])[:3800]
    await update.message.reply_html(f"📋 {bold(f'Last {n} log lines:')}\n<pre>{escape_html(tail)}</pre>")


# ── /dbbackup ──────────────────────────────────────────────────────────────────
@owner_only
async def dbbackup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_url = DATABASE_URL
    if "sqlite" not in db_url:
        await update.message.reply_text("ℹ️ Backup only supported for SQLite databases.")
        return
    db_path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    if not os.path.exists(db_path):
        await update.message.reply_text("❌ Database file not found.")
        return
    await update.message.reply_document(
        document=open(db_path, "rb"),
        filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
        caption=f"📦 {bold('Database Backup')}\n{italic(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}",
        parse_mode=ParseMode.HTML,
    )


# ── /cache ─────────────────────────────────────────────────────────────────────
@sudo_only
async def cache_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.helpers.cache import get_cache_stats
    try:
        stats = await get_cache_stats()
        text = f"🗄️ {bold('Cache Statistics')}\n\n"
        for k, v in stats.items():
            text += f"• {k}: {bold(str(v))}\n"
    except Exception as e:
        text = f"ℹ️ Cache stats unavailable: {escape_html(str(e))}"
    await update.message.reply_html(text)


# ── /clear_cache ───────────────────────────────────────────────────────────────
@sudo_only
async def clear_cache_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.helpers.cache import delete_pattern
    try:
        await delete_pattern("*")
        await update.message.reply_html(f"✅ {bold('Cache cleared successfully.')}")
    except Exception as e:
        await update.message.reply_html(f"❌ Failed to clear cache: {escape_html(str(e))}")


def register_handlers(app):
    app.add_handler(CommandHandler("gban", gban_cmd))
    app.add_handler(CommandHandler("ungban", ungban_cmd))
    app.add_handler(CommandHandler("gbanlist", gbanlist_cmd))
    app.add_handler(CommandHandler("gbanstats", gbanstats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("broadcastpin", broadcastpin_cmd))
    app.add_handler(CommandHandler("addowner", addowner_cmd))
    app.add_handler(CommandHandler("removeowner", removeowner_cmd))
    app.add_handler(CommandHandler("addsudo", addsudo_cmd))
    app.add_handler(CommandHandler("removesudo", removesudo_cmd))
    app.add_handler(CommandHandler("sudolist", sudolist_cmd))
    app.add_handler(CommandHandler("addcurrency", addcurrency_cmd))
    app.add_handler(CommandHandler("removecurrency", removecurrency_cmd))
    app.add_handler(CommandHandler("setcurrency", setcurrency_cmd))
    app.add_handler(CommandHandler("botban", botban_cmd))
    app.add_handler(CommandHandler("unbotban", unbotban_cmd))
    app.add_handler(CommandHandler("maintenance", maintenance_cmd))
    app.add_handler(CommandHandler("globalstats", globalstats_cmd))
    app.add_handler(CommandHandler("grouplist", grouplist_cmd))
    app.add_handler(CommandHandler("leavegroup", leavegroup_cmd))
    app.add_handler(CommandHandler("whitelist", whitelist_cmd))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))
    app.add_handler(CommandHandler("exec", exec_cmd))
    app.add_handler(CommandHandler("shell", shell_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("speedtest", speedtest_cmd))
    app.add_handler(CommandHandler("systeminfo", systeminfo_cmd))
    app.add_handler(CommandHandler("uptime", uptime_cmd))
    app.add_handler(CommandHandler("usage", usage_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("dbbackup", dbbackup_cmd))
    app.add_handler(CommandHandler("cache", cache_cmd))
    app.add_handler(CommandHandler("clear_cache", clear_cache_cmd))
    app.add_handler(CallbackQueryHandler(gbanlist_cb, pattern=r"^gbanlist:"))
    app.add_handler(CallbackQueryHandler(grouplist_cb, pattern=r"^grouplist:"))
