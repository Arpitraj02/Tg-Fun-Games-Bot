"""
bot/plugins/social.py
──────────────────────
Social & profile system: profile cards, reputation, marriage, AFK, badges,
XP levels, and mention-triggered AFK notifications.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, MessageEntityType, ParseMode
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
from bot.database.models import Economy, Leaderboard, User, UserProfile
from bot.helpers.formatters import (
    bold,
    code,
    escape_html,
    format_number,
    italic,
    progress_bar,
    user_mention,
)
from bot.helpers.utils import extract_user_id

logger = logging.getLogger(__name__)

REP_COOLDOWN = 86_400  # 24 hours
MAX_BIO_LEN = 200

# Pending proposals: {proposer_id: {target_id, chat_id, name}}
_proposals: dict[int, dict] = {}


# ── DB Helpers ────────────────────────────────────────────────────────────────

async def _get_or_create_profile(user_id: int) -> UserProfile:
    async with get_session() as session:
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = UserProfile(user_id=user_id)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


async def _get_or_create_user(user_id: int, first_name: str = "") -> User:
    async with get_session() as session:
        stmt = select(User).where(User.user_id == user_id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = User(user_id=user_id, first_name=first_name)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


async def _reply(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


def _xp_to_level(xp: int) -> tuple[int, int, int]:
    """Return (level, xp_in_level, xp_needed_for_next)."""
    level = 1
    xp_needed = 100
    remaining = xp
    while remaining >= xp_needed:
        remaining -= xp_needed
        level += 1
        xp_needed = int(xp_needed * 1.2)
    return level, remaining, xp_needed


# ── /profile ──────────────────────────────────────────────────────────────────

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/profile [@user] — View user profile."""
    chat = update.effective_chat
    if not chat:
        return

    target_id, err = await extract_user_id(update, context)
    if err or target_id is None:
        await _reply(update, f"❌ {err or 'Could not find user.'}")
        return

    try:
        tg = await context.bot.get_chat(target_id)
        name = escape_html(tg.first_name or str(target_id))
        username = f"@{tg.username}" if tg.username else italic("No username")
    except TelegramError:
        name = str(target_id)
        username = italic("Unknown")

    profile = await _get_or_create_profile(target_id)
    db_user = await _get_or_create_user(target_id)

    xp = db_user.xp or 0
    level, xp_in_level, xp_needed = _xp_to_level(xp)
    bar = progress_bar(xp_in_level, xp_needed, length=10)

    badges: list = profile.badges or []
    badges_str = " ".join(badges) if badges else italic("None")

    couple_name = ""
    if profile.couple_id:
        try:
            couple_tg = await context.bot.get_chat(profile.couple_id)
            couple_name = escape_html(couple_tg.first_name or str(profile.couple_id))
        except TelegramError:
            couple_name = str(profile.couple_id)

    married_name = ""
    if profile.married_to:
        try:
            married_tg = await context.bot.get_chat(profile.married_to)
            married_name = escape_html(married_tg.first_name or str(profile.married_to))
        except TelegramError:
            married_name = str(profile.married_to)

    # Economy
    eco_balance = 0
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        async with get_session() as session:
            eco_stmt = select(Economy).where(Economy.user_id == target_id, Economy.chat_id == chat.id)
            eco = (await session.execute(eco_stmt)).scalar_one_or_none()
            if eco:
                eco_balance = (eco.balance or 0) + (eco.bank or 0)

    text = (
        f"👤 {bold(name)}\n"
        f"🆔 {code(str(target_id))} | {username}\n\n"
        f"📊 {bold('Level:')} {code(str(level))} {bar} {xp_in_level}/{xp_needed} XP\n"
        f"⭐ {bold('Total XP:')} {code(format_number(xp))}\n"
        f"💰 {bold('Coins:')} {code(format_number(eco_balance))}\n"
        f"🌟 {bold('Reputation:')} {code(str(db_user.reputation or 0))}\n"
        f"💬 {bold('Messages:')} {code(format_number(db_user.message_count or 0))}\n"
    )
    if profile.bio:
        text += f"\n📝 {bold('Bio:')} {italic(escape_html(profile.bio))}\n"
    if married_name:
        text += f"\n💍 {bold('Married to:')} {married_name}\n"
    elif couple_name:
        text += f"\n💕 {bold('Couple of the day:')} {couple_name}\n"
    if profile.relationship_status and profile.relationship_status != "single":
        text += f"💑 {bold('Status:')} {escape_html(profile.relationship_status)}\n"
    if badges:
        text += f"\n🏆 {bold('Badges:')} {badges_str}"

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🌟 Give Rep", callback_data=f"social:rep:{target_id}"),
        InlineKeyboardButton("📊 Analytics", callback_data=f"social:analytics:{target_id}"),
    ]])
    await _reply(update, text, markup)


# ── /setbio ────────────────────────────────────────────────────────────────────

async def setbio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setbio <text> — Set personal bio."""
    user = update.effective_user
    if not user:
        return

    bio_text = " ".join(context.args) if context.args else ""
    if not bio_text:
        await _reply(update, f"❌ Usage: {code('/setbio &lt;your bio text&gt;')}")
        return
    if len(bio_text) > MAX_BIO_LEN:
        await _reply(update, f"❌ Bio must be {MAX_BIO_LEN} characters or less. Yours: {len(bio_text)}")
        return

    async with get_session() as session:
        stmt = select(UserProfile).where(UserProfile.user_id == user.id)
        profile = (await session.execute(stmt)).scalar_one_or_none()
        if profile is None:
            profile = UserProfile(user_id=user.id, bio=bio_text)
            session.add(profile)
        else:
            profile.bio = bio_text
        await session.commit()

    await _reply(update, f"✅ {bold('Bio Updated!')}\n\n{italic(escape_html(bio_text))}")


# ── /bio ──────────────────────────────────────────────────────────────────────

async def bio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bio [@user] — View user bio."""
    target_id, err = await extract_user_id(update, context)
    if err or target_id is None:
        await _reply(update, f"❌ {err or 'Could not find user.'}")
        return

    try:
        tg = await context.bot.get_chat(target_id)
        name = escape_html(tg.first_name or str(target_id))
    except TelegramError:
        name = str(target_id)

    profile = await _get_or_create_profile(target_id)
    if profile.bio:
        await _reply(update, f"📝 {bold(f'{name}\'s Bio')}\n\n{italic(escape_html(profile.bio))}")
    else:
        await _reply(update, f"📝 {bold(name)} hasn't set a bio yet.")


# ── /rep ──────────────────────────────────────────────────────────────────────

async def rep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rep [@user] — Give reputation point (24h cooldown)."""
    user = update.effective_user
    if not user:
        return

    target_id, err = await extract_user_id(update, context)
    if err or target_id is None:
        await _reply(update, f"❌ {err or 'Please specify a user to rep.'}")
        return
    if target_id == user.id:
        await _reply(update, "😏 You can't rep yourself!")
        return

    giver_profile = await _get_or_create_profile(user.id)
    now = datetime.now(timezone.utc)

    if giver_profile.rep_cooldown:
        last = giver_profile.rep_cooldown.replace(tzinfo=timezone.utc) if giver_profile.rep_cooldown.tzinfo is None else giver_profile.rep_cooldown
        elapsed = (now - last).total_seconds()
        if elapsed < REP_COOLDOWN:
            remaining = int(REP_COOLDOWN - elapsed)
            h, r = divmod(remaining, 3600)
            m, s = divmod(r, 60)
            time_str = f"{h}h {m}m" if h else f"{m}m {s}s"
            await _reply(update, f"⏳ You can give rep again in {bold(time_str)}.")
            return

    # Give rep
    async with get_session() as session:
        # Update giver cooldown
        giver_stmt = select(UserProfile).where(UserProfile.user_id == user.id)
        giver = (await session.execute(giver_stmt)).scalar_one_or_none()
        if giver is None:
            giver = UserProfile(user_id=user.id)
            session.add(giver)
        giver.rep_cooldown = now

        # Update receiver
        recv_stmt = select(User).where(User.user_id == target_id)
        recv_user = (await session.execute(recv_stmt)).scalar_one_or_none()
        if recv_user is None:
            recv_user = User(user_id=target_id, first_name=str(target_id))
            session.add(recv_user)
        recv_user.reputation = (recv_user.reputation or 0) + 1
        await session.commit()
        new_rep = recv_user.reputation

    try:
        tg = await context.bot.get_chat(target_id)
        target_name = escape_html(tg.first_name or str(target_id))
    except TelegramError:
        target_name = str(target_id)

    await _reply(update,
        f"⬆️ {bold(escape_html(user.first_name))} gave rep to {bold(target_name)}!\n"
        f"🌟 {bold(target_name)}'s reputation: {code(str(new_rep))}"
    )


# ── /toprep ───────────────────────────────────────────────────────────────────

async def toprep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/toprep — Top reputation users."""
    async with get_session() as session:
        stmt = select(User).order_by(User.reputation.desc()).limit(10)
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        await _reply(update, f"🌟 {bold('Top Reputation')}\n\n{italic('No data yet.')}")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"🌟 {bold('Top Reputation')}\n"]
    for i, u in enumerate(rows):
        name = escape_html(u.first_name or str(u.user_id))
        lines.append(f"{medals[i]} {bold(name)}: {code(str(u.reputation or 0))} rep")

    await _reply(update, "\n".join(lines))


# ── /couple ───────────────────────────────────────────────────────────────────

async def couple_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/couple — Today's random couple of the day."""
    chat = update.effective_chat
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await _reply(update, "❌ This command is for groups only.")
        return

    # Use date as seed for deterministic daily couple
    today = datetime.now(timezone.utc).date()
    seed = int(today.strftime("%Y%m%d")) + chat.id
    rand = random.Random(seed)

    try:
        members_count = await context.bot.get_chat_member_count(chat.id)
    except TelegramError:
        await _reply(update, "❌ Could not get member list.")
        return

    # Get admins as a proxy for member list (we can't get all members in supergroups)
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        non_bot_admins = [a.user for a in admins if not a.user.is_bot]
    except TelegramError:
        non_bot_admins = []

    if len(non_bot_admins) < 2:
        await _reply(update, "💕 Not enough active members for a couple today!")
        return

    p1, p2 = rand.sample(non_bot_admins, 2)
    name1 = escape_html(p1.first_name)
    name2 = escape_html(p2.first_name)

    await _reply(update,
        f"💕 {bold('Couple of the Day!')}\n\n"
        f"Today's couple is:\n\n"
        f"💝 {user_mention(p1.id, p1.first_name)} & {user_mention(p2.id, p2.first_name)}\n\n"
        f"💫 May your day be wonderful together!"
    )


# ── /propose / /accept / /reject ──────────────────────────────────────────────

async def propose_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/propose @user — Propose to user."""
    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    if not user or not msg or not chat:
        return

    target_id, err = await extract_user_id(update, context)
    if err or target_id is None:
        await _reply(update, f"❌ {err or 'Please tag someone to propose to.'}")
        return
    if target_id == user.id:
        await _reply(update, "😂 You can't propose to yourself!")
        return

    # Check if already married
    profile = await _get_or_create_profile(user.id)
    if profile.married_to:
        await _reply(update, "💍 You're already married! Use /divorce first.")
        return

    try:
        tg = await context.bot.get_chat(target_id)
        target_name = escape_html(tg.first_name or str(target_id))
    except TelegramError:
        target_name = str(target_id)

    _proposals[user.id] = {
        "target_id": target_id,
        "chat_id": chat.id,
        "proposer_name": escape_html(user.first_name),
        "target_name": target_name,
    }

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("💍 Accept", callback_data=f"social:accept:{user.id}"),
        InlineKeyboardButton("💔 Reject", callback_data=f"social:reject:{user.id}"),
    ]])

    await _reply(update,
        f"💍 {bold(escape_html(user.first_name))} is proposing to {user_mention(target_id, target_name)}!\n\n"
        f"Will you accept? 💕",
        markup
    )


async def accept_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/accept — Accept a marriage proposal."""
    user = update.effective_user
    if not user:
        return

    # Find proposal targeting this user
    proposal = next(
        (p for uid, p in _proposals.items() if p["target_id"] == user.id),
        None
    )
    if not proposal:
        await _reply(update, "❌ No pending proposal for you!")
        return

    proposer_id = next(uid for uid, p in _proposals.items() if p["target_id"] == user.id)
    _proposals.pop(proposer_id, None)

    async with get_session() as session:
        # Update both profiles
        p1_stmt = select(UserProfile).where(UserProfile.user_id == proposer_id)
        p1 = (await session.execute(p1_stmt)).scalar_one_or_none()
        if p1 is None:
            p1 = UserProfile(user_id=proposer_id)
            session.add(p1)

        p2_stmt = select(UserProfile).where(UserProfile.user_id == user.id)
        p2 = (await session.execute(p2_stmt)).scalar_one_or_none()
        if p2 is None:
            p2 = UserProfile(user_id=user.id)
            session.add(p2)

        p1.married_to = user.id
        p1.relationship_status = "married"
        p2.married_to = proposer_id
        p2.relationship_status = "married"
        await session.commit()

    await _reply(update,
        f"💒 {bold('Congratulations!')}\n\n"
        f"💍 {bold(proposal['proposer_name'])} and {bold(escape_html(user.first_name))} are now married!\n\n"
        f"🎉 Wishing you a lifetime of happiness! 💕"
    )


async def reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reject — Reject a marriage proposal."""
    user = update.effective_user
    if not user:
        return

    proposal = next(
        (p for uid, p in _proposals.items() if p["target_id"] == user.id),
        None
    )
    if not proposal:
        await _reply(update, "❌ No pending proposal for you!")
        return

    proposer_id = next(uid for uid, p in _proposals.items() if p["target_id"] == user.id)
    _proposals.pop(proposer_id, None)

    await _reply(update,
        f"💔 {bold(escape_html(user.first_name))} rejected {bold(proposal['proposer_name'])}'s proposal.\n\n"
        f"😢 Ouch! Better luck next time."
    )


# ── /divorce ──────────────────────────────────────────────────────────────────

async def divorce_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/divorce — End marriage."""
    user = update.effective_user
    if not user:
        return

    profile = await _get_or_create_profile(user.id)
    if not profile.married_to:
        await _reply(update, "💍 You're not married!")
        return

    partner_id = profile.married_to

    async with get_session() as session:
        p1_stmt = select(UserProfile).where(UserProfile.user_id == user.id)
        p1 = (await session.execute(p1_stmt)).scalar_one_or_none()
        if p1:
            p1.married_to = None
            p1.relationship_status = "single"

        p2_stmt = select(UserProfile).where(UserProfile.user_id == partner_id)
        p2 = (await session.execute(p2_stmt)).scalar_one_or_none()
        if p2:
            p2.married_to = None
            p2.relationship_status = "single"

        await session.commit()

    try:
        tg = await context.bot.get_chat(partner_id)
        partner_name = escape_html(tg.first_name or str(partner_id))
    except TelegramError:
        partner_name = str(partner_id)

    await _reply(update,
        f"💔 {bold(escape_html(user.first_name))} and {bold(partner_name)} have divorced.\n\n"
        f"😢 Sometimes things just don't work out..."
    )


# ── /marry / /married ─────────────────────────────────────────────────────────

async def marry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/marry @user — Alias for propose."""
    await propose_command(update, context)


async def married_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/married — Check who you're married to."""
    user = update.effective_user
    if not user:
        return

    profile = await _get_or_create_profile(user.id)
    if not profile.married_to:
        await _reply(update, f"💍 {bold(escape_html(user.first_name))} is {italic('single')}. 😊")
        return

    try:
        tg = await context.bot.get_chat(profile.married_to)
        partner_name = escape_html(tg.first_name or str(profile.married_to))
    except TelegramError:
        partner_name = str(profile.married_to)

    await _reply(update,
        f"💍 {bold(escape_html(user.first_name))} is married to {bold(partner_name)}! 💕"
    )


# ── /setafk / AFK system ──────────────────────────────────────────────────────

async def setafk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setafk [reason] — Set AFK status."""
    user = update.effective_user
    if not user:
        return

    reason = " ".join(context.args) if context.args else None

    async with get_session() as session:
        stmt = select(UserProfile).where(UserProfile.user_id == user.id)
        profile = (await session.execute(stmt)).scalar_one_or_none()
        if profile is None:
            profile = UserProfile(user_id=user.id)
            session.add(profile)
        profile.afk = True
        profile.afk_reason = reason
        profile.afk_since = datetime.now(timezone.utc)
        await session.commit()

    msg = f"😴 {bold(escape_html(user.first_name))} is now AFK"
    if reason:
        msg += f"\n💬 Reason: {italic(escape_html(reason))}"
    await _reply(update, msg)


async def afk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/afk — Toggle AFK status."""
    user = update.effective_user
    if not user:
        return

    profile = await _get_or_create_profile(user.id)

    async with get_session() as session:
        stmt = select(UserProfile).where(UserProfile.user_id == user.id)
        p = (await session.execute(stmt)).scalar_one_or_none()
        if p is None:
            p = UserProfile(user_id=user.id)
            session.add(p)

        if p.afk:
            p.afk = False
            p.afk_reason = None
            now = datetime.now(timezone.utc)
            afk_since = p.afk_since
            p.afk_since = None
            await session.commit()

            duration = ""
            if afk_since:
                since_utc = afk_since.replace(tzinfo=timezone.utc) if afk_since.tzinfo is None else afk_since
                secs = int((now - since_utc).total_seconds())
                h, r = divmod(secs, 3600)
                m, s = divmod(r, 60)
                duration = f" (was AFK for {h}h {m}m {s}s)" if h else f" (was AFK for {m}m {s}s)"

            await _reply(update, f"👋 Welcome back, {bold(escape_html(user.first_name))}!{duration}")
        else:
            p.afk = True
            p.afk_since = datetime.now(timezone.utc)
            await session.commit()
            await _reply(update, f"😴 {bold(escape_html(user.first_name))} is now AFK.")


async def check_afk_mention(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check if mentioned users are AFK."""
    msg = update.effective_message
    if not msg:
        return
    entities = msg.entities or []
    mentioned_ids = set()
    for entity in entities:
        if entity.type == MessageEntityType.MENTION:
            username = (msg.text or "")[entity.offset:entity.offset + entity.length].lstrip("@")
            try:
                tg = await context.bot.get_chat(f"@{username}")
                mentioned_ids.add(tg.id)
            except TelegramError:
                pass
        elif entity.type == MessageEntityType.TEXT_MENTION and entity.user:
            mentioned_ids.add(entity.user.id)

    if not mentioned_ids:
        return

    async with get_session() as session:
        for uid in mentioned_ids:
            if uid == (update.effective_user.id if update.effective_user else None):
                continue
            stmt = select(UserProfile).where(UserProfile.user_id == uid, UserProfile.afk == True)
            profile = (await session.execute(stmt)).scalar_one_or_none()
            if profile:
                try:
                    tg = await context.bot.get_chat(uid)
                    afk_name = escape_html(tg.first_name or str(uid))
                except TelegramError:
                    afk_name = str(uid)

                afk_msg = f"😴 {bold(afk_name)} is currently AFK"
                if profile.afk_reason:
                    afk_msg += f"\n💬 {italic(escape_html(profile.afk_reason))}"
                if profile.afk_since:
                    since = profile.afk_since.replace(tzinfo=timezone.utc) if profile.afk_since.tzinfo is None else profile.afk_since
                    secs = int((datetime.now(timezone.utc) - since).total_seconds())
                    h, r = divmod(secs, 3600)
                    m, s = divmod(r, 60)
                    afk_msg += f"\n⏰ Since {h}h {m}m ago" if h else f"\n⏰ Since {m}m {s}s ago"

                await msg.reply_text(afk_msg, parse_mode=ParseMode.HTML)


# ── /badges ───────────────────────────────────────────────────────────────────

BADGE_DESCRIPTIONS = {
    "🥇": "Gold Rank",
    "🌟": "Star User",
    "💎": "Diamond Member",
    "🎮": "Gamer",
    "💰": "Rich",
    "🏆": "Champion",
    "🎖️": "Veteran",
    "❤️": "Beloved",
    "🌹": "Romance",
    "🎓": "Scholar",
}


async def badges_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/badges — View earned badges."""
    user = update.effective_user
    if not user:
        return

    profile = await _get_or_create_profile(user.id)
    badges: list = profile.badges or []

    if not badges:
        await _reply(update,
            f"🏆 {bold('Badges')}\n\n"
            f"You haven't earned any badges yet!\n"
            f"{italic('Keep being active to earn them.')}"
        )
        return

    lines = [f"🏆 {bold(f'{escape_html(user.first_name)}\'s Badges')}\n"]
    for badge in badges:
        desc = BADGE_DESCRIPTIONS.get(badge, "Special Badge")
        lines.append(f"{badge} {desc}")

    await _reply(update, "\n".join(lines))


# ── /level ────────────────────────────────────────────────────────────────────

async def level_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/level — Check XP level progress bar."""
    user = update.effective_user
    if not user:
        return

    db_user = await _get_or_create_user(user.id, user.first_name)
    xp = db_user.xp or 0
    level, xp_in_level, xp_needed = _xp_to_level(xp)
    bar = progress_bar(xp_in_level, xp_needed, length=15)

    await _reply(update,
        f"⭐ {bold(f'{escape_html(user.first_name)}\'s Level')}\n\n"
        f"🏆 {bold('Level:')} {code(str(level))}\n"
        f"📊 {bold('Progress:')} {bar}\n"
        f"✨ {code(str(xp_in_level))}/{code(str(xp_needed))} XP\n"
        f"🌟 {bold('Total XP:')} {code(format_number(xp))}"
    )


# ── /toplevels ────────────────────────────────────────────────────────────────

async def toplevels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/toplevels — Top XP earners."""
    async with get_session() as session:
        stmt = select(User).order_by(User.xp.desc()).limit(10)
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        await _reply(update, f"⭐ {bold('Top Levels')}\n\n{italic('No data yet.')}")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"⭐ {bold('Top XP Earners')}\n"]
    for i, u in enumerate(rows):
        name = escape_html(u.first_name or str(u.user_id))
        xp = u.xp or 0
        level, _, _ = _xp_to_level(xp)
        lines.append(f"{medals[i]} {bold(name)}: Lv.{level} ({format_number(xp)} XP)")

    await _reply(update, "\n".join(lines))


# ── Callback handler ──────────────────────────────────────────────────────────

async def social_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle social:* callbacks."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "rep":
        target_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        if target_id == user.id:
            await query.answer("😏 You can't rep yourself!", show_alert=True)
            return

        profile = await _get_or_create_profile(user.id)
        now = datetime.now(timezone.utc)
        if profile.rep_cooldown:
            last = profile.rep_cooldown.replace(tzinfo=timezone.utc) if profile.rep_cooldown.tzinfo is None else profile.rep_cooldown
            if (now - last).total_seconds() < REP_COOLDOWN:
                await query.answer("⏳ You can only give rep once every 24 hours!", show_alert=True)
                return

        async with get_session() as session:
            giver_stmt = select(UserProfile).where(UserProfile.user_id == user.id)
            giver = (await session.execute(giver_stmt)).scalar_one_or_none()
            if giver is None:
                giver = UserProfile(user_id=user.id)
                session.add(giver)
            giver.rep_cooldown = now

            recv_stmt = select(User).where(User.user_id == target_id)
            recv = (await session.execute(recv_stmt)).scalar_one_or_none()
            if recv:
                recv.reputation = (recv.reputation or 0) + 1
            await session.commit()
            new_rep = recv.reputation if recv else 1

        await query.answer(f"✅ Rep given! They now have {new_rep} rep.", show_alert=True)

    elif action == "accept":
        proposer_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        proposal = _proposals.get(proposer_id)
        if not proposal or proposal["target_id"] != user.id:
            await query.answer("❌ This proposal is not for you or has expired!", show_alert=True)
            return
        _proposals.pop(proposer_id, None)

        async with get_session() as session:
            p1_stmt = select(UserProfile).where(UserProfile.user_id == proposer_id)
            p1 = (await session.execute(p1_stmt)).scalar_one_or_none()
            if p1 is None:
                p1 = UserProfile(user_id=proposer_id)
                session.add(p1)
            p1.married_to = user.id
            p1.relationship_status = "married"

            p2_stmt = select(UserProfile).where(UserProfile.user_id == user.id)
            p2 = (await session.execute(p2_stmt)).scalar_one_or_none()
            if p2 is None:
                p2 = UserProfile(user_id=user.id)
                session.add(p2)
            p2.married_to = proposer_id
            p2.relationship_status = "married"
            await session.commit()

        try:
            await query.edit_message_text(
                f"💒 {bold('Congratulations!')}\n\n"
                f"💍 {bold(proposal['proposer_name'])} and {bold(escape_html(user.first_name))} are now married! 💕",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass

    elif action == "reject":
        proposer_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        proposal = _proposals.get(proposer_id)
        if not proposal or proposal["target_id"] != user.id:
            await query.answer("❌ This proposal is not for you!", show_alert=True)
            return
        _proposals.pop(proposer_id, None)
        try:
            await query.edit_message_text(
                f"💔 {bold(escape_html(user.first_name))} rejected the proposal. 😢",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass

    elif action == "analytics":
        target_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else user.id
        async with get_session() as session:
            stmt = select(Leaderboard).where(Leaderboard.user_id == target_id)
            lb = (await session.execute(stmt)).scalar_one_or_none()
        if lb:
            await query.answer(
                f"Messages: {lb.messages_all or 0} | Voice: {lb.voice_seconds or 0}s | Media: {lb.media_count or 0}",
                show_alert=True,
            )
        else:
            await query.answer("No activity data found.", show_alert=True)


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("me", profile_command))
    app.add_handler(CommandHandler("setbio", setbio_command))
    app.add_handler(CommandHandler("bio", bio_command))
    app.add_handler(CommandHandler("rep", rep_command))
    app.add_handler(CommandHandler("toprep", toprep_command))
    app.add_handler(CommandHandler("couple", couple_command))
    app.add_handler(CommandHandler("propose", propose_command))
    app.add_handler(CommandHandler("accept", accept_command))
    app.add_handler(CommandHandler("reject", reject_command))
    app.add_handler(CommandHandler("divorce", divorce_command))
    app.add_handler(CommandHandler("marry", marry_command))
    app.add_handler(CommandHandler("married", married_command))
    app.add_handler(CommandHandler("setafk", setafk_command))
    app.add_handler(CommandHandler("afk", afk_command))
    app.add_handler(CommandHandler("badges", badges_command))
    app.add_handler(CommandHandler("level", level_command))
    app.add_handler(CommandHandler("toplevels", toplevels_command))

    # AFK mention detection
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.COMMAND & (filters.Entity(MessageEntityType.MENTION) | filters.Entity(MessageEntityType.TEXT_MENTION)),
            check_afk_mention,
        ),
        group=11,
    )

    app.add_handler(CallbackQueryHandler(social_callback, pattern=r"^social:"))
