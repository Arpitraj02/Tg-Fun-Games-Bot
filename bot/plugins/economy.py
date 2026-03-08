"""
bot/plugins/economy.py
──────────────────────
Virtual economy system with wallet, bank, daily rewards, work, crime, gambling,
shop, inventory, transfers, and leaderboard.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.database.connection import get_session
from bot.database.models import Economy, User
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

# ── Constants ─────────────────────────────────────────────────────────────────

DAILY_MIN, DAILY_MAX = 500, 1000
WEEKLY_REWARD = 5000
WORK_MIN, WORK_MAX = 100, 500
BEG_MIN, BEG_MAX = 0, 100
CRIME_WIN_MIN, CRIME_WIN_MAX = 200, 800
CRIME_LOSE_MIN, CRIME_LOSE_MAX = 100, 300
ROB_MIN_BALANCE = 500
ROB_SUCCESS_CHANCE = 30  # percent
GAMBLE_WIN_MULTIPLIER = 2.0
TRANSFER_FEE_PERCENT = 5

DAILY_CD = 86_400
WEEKLY_CD = 604_800
WORK_CD = 14_400  # 4 hours
CRIME_CD = 14_400
ROB_CD = 43_200  # 12 hours

SHOP_ITEMS: dict[str, dict] = {
    "vip": {"name": "👑 VIP Role", "price": 10_000, "desc": "Exclusive VIP badge & perks"},
    "charm": {"name": "🍀 Lucky Charm", "price": 500, "desc": "+10% economy gains for 24h"},
    "shield": {"name": "🛡️ Shield", "price": 2_000, "desc": "Protects from robbery once"},
}

JOB_LIST = [
    ("🍕 Pizza delivery", 120, 280),
    ("💻 Freelance coding", 250, 500),
    ("🚗 Uber driver", 100, 300),
    ("🛒 Grocery store clerk", 100, 200),
    ("🐶 Dog walker", 80, 180),
    ("🎨 Street artist", 150, 350),
    ("📦 Package delivery", 110, 250),
    ("🔧 Plumber", 200, 450),
    ("📸 Photographer", 180, 400),
    ("🌮 Taco truck chef", 130, 280),
]

CRIME_SCENARIOS = [
    "robbed a convenience store",
    "picked pockets on the subway",
    "hacked into a corporation",
    "forged luxury goods",
    "ran a pyramid scheme",
    "smuggled rare artifacts",
]

BEG_SUCCESS = [
    "A kind stranger gave you {amount} coins! 🪙",
    "Someone took pity on you and dropped {amount} coins! 💸",
    "You found {amount} coins on the ground! 🤑",
    "A generous passerby gave you {amount} coins! ✨",
]

BEG_FAIL = [
    "No one wants to give you any money. 😢",
    "You got ignored. Try looking more pathetic. 🥺",
    "Someone told you to get a job. 😤",
    "You got laughed at. Zero coins for you! 😅",
]

SLOT_EMOJIS = ["🍒", "🍋", "🍊", "🍇", "⭐", "💎", "🎰", "7️⃣"]
SLOT_MULTIPLIERS = {
    "💎💎💎": 10,
    "7️⃣7️⃣7️⃣": 7,
    "⭐⭐⭐": 5,
    "🎰🎰🎰": 4,
    "🍇🍇🍇": 3,
    "🍊🍊🍊": 3,
    "🍋🍋🍋": 2,
    "🍒🍒🍒": 2,
}


# ── DB Helpers ────────────────────────────────────────────────────────────────

async def _get_or_create_economy(user_id: int, chat_id: int) -> Economy:
    """Fetch or create an Economy row for (user_id, chat_id)."""
    async with get_session() as session:
        stmt = select(Economy).where(
            Economy.user_id == user_id, Economy.chat_id == chat_id
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = Economy(user_id=user_id, chat_id=chat_id, balance=0, bank=0, inventory={})
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


async def _save_economy(eco: Economy) -> None:
    async with get_session() as session:
        stmt = select(Economy).where(Economy.id == eco.id)
        db_eco = (await session.execute(stmt)).scalar_one_or_none()
        if db_eco:
            db_eco.balance = eco.balance
            db_eco.bank = eco.bank
            db_eco.inventory = eco.inventory
            db_eco.daily_claimed = eco.daily_claimed
            db_eco.weekly_claimed = eco.weekly_claimed
            db_eco.work_claimed = eco.work_claimed
            db_eco.crime_claimed = eco.crime_claimed
            await session.commit()


def _seconds_remaining(last: Optional[datetime], cooldown: int) -> int:
    if last is None:
        return 0
    now = datetime.now(timezone.utc)
    last_utc = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
    elapsed = (now - last_utc).total_seconds()
    remaining = cooldown - elapsed
    return max(0, int(remaining))


def _fmt_cooldown(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


async def _reply(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


# ── /balance ──────────────────────────────────────────────────────────────────

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/balance [@user] — Check coin balance."""
    chat = update.effective_chat
    if not chat:
        return
    user_id, err = await extract_user_id(update, context)
    if err or user_id is None:
        await _reply(update, f"❌ {err or 'Could not find user.'}")
        return

    eco = await _get_or_create_economy(user_id, chat.id)
    try:
        target = await context.bot.get_chat(user_id)
        name = escape_html(target.first_name or str(user_id))
    except TelegramError:
        name = str(user_id)

    total = eco.balance + eco.bank
    inv = eco.inventory or {}
    item_names = [SHOP_ITEMS[k]["name"] for k in inv if k in SHOP_ITEMS] if inv else []

    text = (
        f"💰 {bold(f'{name}\'s Wallet')}\n\n"
        f"👛 {bold('Wallet:')} {code(format_number(eco.balance))} coins\n"
        f"🏦 {bold('Bank:')} {code(format_number(eco.bank))} coins\n"
        f"📊 {bold('Total:')} {code(format_number(total))} coins\n"
    )
    if item_names:
        text += f"\n🎒 {bold('Inventory:')} {', '.join(item_names)}"

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏦 Bank", callback_data=f"eco:bank:{user_id}"),
        InlineKeyboardButton("🛒 Shop", callback_data="eco:shop"),
    ]])
    await _reply(update, text, markup)


# ── /daily ────────────────────────────────────────────────────────────────────

async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/daily — Daily reward with streak bonus."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    remaining = _seconds_remaining(eco.daily_claimed, DAILY_CD)
    if remaining > 0:
        await _reply(update, f"⏳ You already claimed your daily reward!\nCome back in {bold(_fmt_cooldown(remaining))}.")
        return

    # Streak bonus
    streak = 0
    async with get_session() as session:
        stmt = select(User).where(User.user_id == user.id)
        db_user = (await session.execute(stmt)).scalar_one_or_none()
        if db_user:
            now = datetime.now(timezone.utc)
            if db_user.last_streak:
                last = db_user.last_streak.replace(tzinfo=timezone.utc) if db_user.last_streak.tzinfo is None else db_user.last_streak
                diff = (now - last).total_seconds()
                if diff < DAILY_CD * 2:
                    streak = db_user.streak + 1
                else:
                    streak = 1
            else:
                streak = 1
            db_user.streak = streak
            db_user.last_streak = now
            await session.commit()

    base = random.randint(DAILY_MIN, DAILY_MAX)
    bonus = min(streak * 50, 500)
    total = base + bonus

    eco.balance += total
    eco.daily_claimed = datetime.now(timezone.utc)
    await _save_economy(eco)

    text = (
        f"🎁 {bold('Daily Reward Claimed!')}\n\n"
        f"💰 Base reward: {code(format_number(base))} coins\n"
    )
    if bonus:
        text += f"🔥 Streak bonus (×{streak}): {code(format_number(bonus))} coins\n"
    text += (
        f"\n✨ {bold('Total earned:')} {code(format_number(total))} coins\n"
        f"👛 New balance: {code(format_number(eco.balance))} coins\n"
        f"📅 Streak: {code(str(streak))} day{'s' if streak != 1 else ''}"
    )
    await _reply(update, text)


# ── /weekly ───────────────────────────────────────────────────────────────────

async def weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/weekly — Weekly bonus reward."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    remaining = _seconds_remaining(eco.weekly_claimed, WEEKLY_CD)
    if remaining > 0:
        await _reply(update, f"⏳ Weekly reward already claimed!\nCome back in {bold(_fmt_cooldown(remaining))}.")
        return

    eco.balance += WEEKLY_REWARD
    eco.weekly_claimed = datetime.now(timezone.utc)
    await _save_economy(eco)

    await _reply(update,
        f"🎉 {bold('Weekly Bonus!')}\n\n"
        f"💰 You received {code(format_number(WEEKLY_REWARD))} coins!\n"
        f"👛 New balance: {code(format_number(eco.balance))} coins\n"
        f"⏰ Next weekly in: {bold('7 days')}"
    )


# ── /work ─────────────────────────────────────────────────────────────────────

async def work_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/work — Work for coins (4h cooldown)."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    remaining = _seconds_remaining(eco.work_claimed, WORK_CD)
    if remaining > 0:
        await _reply(update, f"😓 You're too tired to work again!\nRest for {bold(_fmt_cooldown(remaining))}.")
        return

    job, jmin, jmax = random.choice(JOB_LIST)
    earned = random.randint(jmin, jmax)
    eco.balance += earned
    eco.work_claimed = datetime.now(timezone.utc)
    await _save_economy(eco)

    await _reply(update,
        f"💼 {bold('Work Complete!')}\n\n"
        f"You worked as: {job}\n"
        f"💵 Earned: {code(format_number(earned))} coins\n"
        f"👛 Balance: {code(format_number(eco.balance))} coins\n"
        f"⏰ Next shift in: {bold('4 hours')}"
    )


# ── /beg ──────────────────────────────────────────────────────────────────────

async def beg_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/beg — Beg for coins."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    amount = random.randint(BEG_MIN, BEG_MAX)

    if amount == 0 or random.random() < 0.35:
        await _reply(update, f"🤲 {random.choice(BEG_FAIL)}")
        return

    eco.balance += amount
    await _save_economy(eco)
    msg = random.choice(BEG_SUCCESS).format(amount=format_number(amount))
    await _reply(update, f"🤲 {msg}\n👛 Balance: {code(format_number(eco.balance))} coins")


# ── /crime ────────────────────────────────────────────────────────────────────

async def crime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/crime — Commit crime (50% success rate)."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    remaining = _seconds_remaining(eco.crime_claimed, CRIME_CD)
    if remaining > 0:
        await _reply(update, f"🚔 Lay low for a while!\nPolice are watching. Wait {bold(_fmt_cooldown(remaining))}.")
        return

    eco.crime_claimed = datetime.now(timezone.utc)
    scenario = random.choice(CRIME_SCENARIOS)
    success = random.random() < 0.50

    if success:
        earned = random.randint(CRIME_WIN_MIN, CRIME_WIN_MAX)
        eco.balance += earned
        await _save_economy(eco)
        await _reply(update,
            f"🦹 {bold('Crime Successful!')}\n\n"
            f"You {scenario} and got away!\n"
            f"💰 Earned: {code(format_number(earned))} coins\n"
            f"👛 Balance: {code(format_number(eco.balance))} coins"
        )
    else:
        lost = random.randint(CRIME_LOSE_MIN, CRIME_LOSE_MAX)
        lost = min(lost, eco.balance)
        eco.balance -= lost
        await _save_economy(eco)
        await _reply(update,
            f"🚓 {bold('Caught by the Police!')}\n\n"
            f"You tried to {scenario} but got caught!\n"
            f"💸 Fine: {code(format_number(lost))} coins\n"
            f"👛 Balance: {code(format_number(eco.balance))} coins"
        )


# ── /rob ──────────────────────────────────────────────────────────────────────

async def rob_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rob @user — Rob another user."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    target_id, err = await extract_user_id(update, context)
    if err or target_id is None:
        await _reply(update, f"❌ {err or 'Please specify a user to rob.'}")
        return
    if target_id == user.id:
        await _reply(update, "😂 You can't rob yourself!")
        return

    robber_eco = await _get_or_create_economy(user.id, chat.id)
    if robber_eco.balance < ROB_MIN_BALANCE:
        await _reply(update, f"💸 You need at least {bold(format_number(ROB_MIN_BALANCE))} coins in your wallet to rob someone.")
        return

    # Cooldown stored in work_claimed (reuse field; a proper system would use a separate field)
    # We'll use a simple in-memory approach here via context.chat_data
    rob_key = f"rob_cd_{user.id}"
    last_rob = context.chat_data.get(rob_key)
    if last_rob:
        elapsed = (datetime.now(timezone.utc) - last_rob).total_seconds()
        if elapsed < ROB_CD:
            await _reply(update, f"⏳ You need to wait {bold(_fmt_cooldown(int(ROB_CD - elapsed)))} before robbing again.")
            return

    victim_eco = await _get_or_create_economy(target_id, chat.id)
    inv = victim_eco.inventory or {}

    if "shield" in inv:
        inv.pop("shield")
        victim_eco.inventory = inv
        await _save_economy(victim_eco)
        await _reply(update, f"🛡️ Your robbery was blocked! The victim had a {bold('Shield')}!")
        return

    try:
        target = await context.bot.get_chat(target_id)
        victim_name = escape_html(target.first_name or str(target_id))
    except TelegramError:
        victim_name = str(target_id)

    success = random.randint(1, 100) <= ROB_SUCCESS_CHANCE
    context.chat_data[rob_key] = datetime.now(timezone.utc)

    if success:
        stolen = random.randint(50, max(50, victim_eco.balance // 4))
        stolen = min(stolen, victim_eco.balance)
        if stolen == 0:
            await _reply(update, f"😅 {bold(victim_name)} is broke! Nothing to steal.")
            return
        victim_eco.balance -= stolen
        robber_eco.balance += stolen
        await _save_economy(victim_eco)
        await _save_economy(robber_eco)
        await _reply(update,
            f"🦹 {bold('Robbery Successful!')}\n\n"
            f"You robbed {bold(victim_name)} for {code(format_number(stolen))} coins!\n"
            f"👛 Your balance: {code(format_number(robber_eco.balance))} coins"
        )
    else:
        fine = random.randint(50, 150)
        fine = min(fine, robber_eco.balance)
        robber_eco.balance -= fine
        await _save_economy(robber_eco)
        await _reply(update,
            f"🚔 {bold('Robbery Failed!')}\n\n"
            f"You failed to rob {bold(victim_name)} and got caught!\n"
            f"💸 Fine: {code(format_number(fine))} coins\n"
            f"👛 Balance: {code(format_number(robber_eco.balance))} coins"
        )


# ── /deposit ──────────────────────────────────────────────────────────────────

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/deposit <amount|all> — Deposit coins to bank."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    args = context.args or []
    if not args:
        await _reply(update, f"❌ Usage: {code('/deposit &lt;amount|all&gt;')}")
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    raw = args[0].lower()

    if raw == "all":
        amount = eco.balance
    else:
        try:
            amount = int(raw)
        except ValueError:
            await _reply(update, "❌ Please provide a valid number or 'all'.")
            return

    if amount <= 0:
        await _reply(update, "❌ Amount must be positive.")
        return
    if amount > eco.balance:
        await _reply(update, f"❌ Insufficient wallet balance! You have {code(format_number(eco.balance))} coins.")
        return

    eco.balance -= amount
    eco.bank += amount
    await _save_economy(eco)

    await _reply(update,
        f"🏦 {bold('Deposit Successful!')}\n\n"
        f"💰 Deposited: {code(format_number(amount))} coins\n"
        f"👛 Wallet: {code(format_number(eco.balance))} coins\n"
        f"🏦 Bank: {code(format_number(eco.bank))} coins"
    )


# ── /withdraw ─────────────────────────────────────────────────────────────────

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/withdraw <amount|all> — Withdraw from bank."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    args = context.args or []
    if not args:
        await _reply(update, f"❌ Usage: {code('/withdraw &lt;amount|all&gt;')}")
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    raw = args[0].lower()

    if raw == "all":
        amount = eco.bank
    else:
        try:
            amount = int(raw)
        except ValueError:
            await _reply(update, "❌ Please provide a valid number or 'all'.")
            return

    if amount <= 0:
        await _reply(update, "❌ Amount must be positive.")
        return
    if amount > eco.bank:
        await _reply(update, f"❌ Insufficient bank balance! You have {code(format_number(eco.bank))} coins in the bank.")
        return

    eco.bank -= amount
    eco.balance += amount
    await _save_economy(eco)

    await _reply(update,
        f"💵 {bold('Withdrawal Successful!')}\n\n"
        f"💰 Withdrawn: {code(format_number(amount))} coins\n"
        f"👛 Wallet: {code(format_number(eco.balance))} coins\n"
        f"🏦 Bank: {code(format_number(eco.bank))} coins"
    )


# ── /transfer ─────────────────────────────────────────────────────────────────

async def transfer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/transfer @user <amount> — Transfer coins to another user."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    args = context.args or []
    msg = update.effective_message
    reply = msg.reply_to_message if msg else None

    # Determine target and amount
    if reply and reply.from_user:
        target_id = reply.from_user.id
        amount_str = args[0] if args else None
    elif len(args) >= 2:
        target_raw = args[0].lstrip("@")
        if target_raw.isdigit():
            target_id = int(target_raw)
        else:
            try:
                tg = await context.bot.get_chat(f"@{target_raw}")
                target_id = tg.id
            except TelegramError:
                await _reply(update, f"❌ Could not find user '@{escape_html(target_raw)}'.")
                return
        amount_str = args[1]
    else:
        await _reply(update, f"❌ Usage: {code('/transfer @user &lt;amount&gt;')}")
        return

    if target_id == user.id:
        await _reply(update, "😂 You can't transfer to yourself!")
        return

    try:
        amount = int(amount_str)
    except (ValueError, TypeError):
        await _reply(update, "❌ Please provide a valid amount.")
        return

    if amount < 1:
        await _reply(update, "❌ Minimum transfer is 1 coin.")
        return

    sender_eco = await _get_or_create_economy(user.id, chat.id)
    fee = max(1, amount * TRANSFER_FEE_PERCENT // 100)
    total_cost = amount + fee

    if total_cost > sender_eco.balance:
        await _reply(update,
            f"❌ Insufficient funds!\n"
            f"Amount: {format_number(amount)} + Fee: {format_number(fee)} = {format_number(total_cost)} coins\n"
            f"Your wallet: {format_number(sender_eco.balance)} coins"
        )
        return

    receiver_eco = await _get_or_create_economy(target_id, chat.id)
    sender_eco.balance -= total_cost
    receiver_eco.balance += amount
    await _save_economy(sender_eco)
    await _save_economy(receiver_eco)

    try:
        target = await context.bot.get_chat(target_id)
        target_name = escape_html(target.first_name or str(target_id))
    except TelegramError:
        target_name = str(target_id)

    await _reply(update,
        f"💸 {bold('Transfer Complete!')}\n\n"
        f"Sent {code(format_number(amount))} coins to {bold(target_name)}\n"
        f"🏷️ Fee ({TRANSFER_FEE_PERCENT}%): {code(format_number(fee))} coins\n"
        f"👛 Your balance: {code(format_number(sender_eco.balance))} coins"
    )


# ── /shop ─────────────────────────────────────────────────────────────────────

def _shop_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for item_id, item in SHOP_ITEMS.items():
        buttons.append([InlineKeyboardButton(
            f"{item['name']} — {format_number(item['price'])} 🪙",
            callback_data=f"eco:buy:{item_id}"
        )])
    buttons.append([InlineKeyboardButton("🎒 My Inventory", callback_data="eco:inventory")])
    return InlineKeyboardMarkup(buttons)


async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shop — Browse the item shop."""
    lines = [f"🛒 {bold('Item Shop')}\n"]
    for item_id, item in SHOP_ITEMS.items():
        lines.append(
            f"{item['name']}\n"
            f"  💰 Price: {code(format_number(item['price']))} coins\n"
            f"  📝 {item['desc']}\n"
            f"  🔑 ID: {code(item_id)}"
        )
    await _reply(update, "\n".join(lines), _shop_keyboard())


# ── /buy ──────────────────────────────────────────────────────────────────────

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/buy <item_id> — Purchase an item."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    args = context.args or []
    if not args:
        await _reply(update, f"❌ Usage: {code('/buy &lt;item_id&gt;')}\nUse {code('/shop')} to see available items.")
        return

    item_id = args[0].lower()
    if item_id not in SHOP_ITEMS:
        await _reply(update, f"❌ Unknown item: {code(escape_html(item_id))}. Use {code('/shop')} to browse.")
        return

    item = SHOP_ITEMS[item_id]
    eco = await _get_or_create_economy(user.id, chat.id)

    if eco.balance < item["price"]:
        await _reply(update, f"❌ Not enough coins!\n{item['name']} costs {code(format_number(item['price']))} coins.\nYou have {code(format_number(eco.balance))} coins.")
        return

    inv = eco.inventory or {}
    if item_id in inv:
        await _reply(update, f"⚠️ You already own {item['name']}!")
        return

    eco.balance -= item["price"]
    inv[item_id] = {"purchased_at": datetime.now(timezone.utc).isoformat()}
    eco.inventory = inv
    await _save_economy(eco)

    await _reply(update,
        f"✅ {bold('Purchase Successful!')}\n\n"
        f"🛒 Bought: {item['name']}\n"
        f"💸 Spent: {code(format_number(item['price']))} coins\n"
        f"👛 Balance: {code(format_number(eco.balance))} coins\n"
        f"📝 {item['desc']}"
    )


# ── /inventory ────────────────────────────────────────────────────────────────

async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/inventory — View owned items."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    inv = eco.inventory or {}

    if not inv:
        await _reply(update, f"🎒 {bold('Inventory')}\n\nYour inventory is empty! Visit {code('/shop')} to buy items.")
        return

    lines = [f"🎒 {bold('Your Inventory')}\n"]
    for item_id, data in inv.items():
        item = SHOP_ITEMS.get(item_id, {"name": item_id, "desc": "Unknown item"})
        purchased = data.get("purchased_at", "Unknown")[:10] if isinstance(data, dict) else "Unknown"
        lines.append(f"• {item['name']}\n  📅 Purchased: {purchased}")

    await _reply(update, "\n".join(lines))


# ── /give ─────────────────────────────────────────────────────────────────────

async def give_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/give @user <amount> — Gift coins to another user."""
    # For simplicity, treat this as a transfer with no fee (admin) or with fee (regular)
    await transfer_command(update, context)


# ── /gamble ───────────────────────────────────────────────────────────────────

async def gamble_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/gamble <amount> — Gamble coins on a coin flip."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    args = context.args or []
    if not args:
        await _reply(update, f"❌ Usage: {code('/gamble &lt;amount&gt;')}")
        return

    eco = await _get_or_create_economy(user.id, chat.id)
    raw = args[0].lower()

    try:
        amount = eco.balance if raw == "all" else int(raw)
    except ValueError:
        await _reply(update, "❌ Please provide a valid amount.")
        return

    if amount <= 0:
        await _reply(update, "❌ Amount must be positive.")
        return
    if amount > eco.balance:
        await _reply(update, f"❌ Not enough coins in wallet! You have {code(format_number(eco.balance))} coins.")
        return

    result = random.choice(["heads", "tails"])
    player_choice = random.choice(["heads", "tails"])
    won = result == player_choice

    if won:
        winnings = int(amount * GAMBLE_WIN_MULTIPLIER) - amount
        eco.balance += winnings
        text = (
            f"🎲 {bold('You Won!')}\n\n"
            f"🪙 Coin: {bold(result.capitalize())}\n"
            f"✅ Correct guess!\n"
            f"💰 Won: {code(format_number(winnings))} coins\n"
            f"👛 Balance: {code(format_number(eco.balance))} coins"
        )
    else:
        eco.balance -= amount
        text = (
            f"🎲 {bold('You Lost!')}\n\n"
            f"🪙 Coin: {bold(result.capitalize())}\n"
            f"❌ Wrong guess!\n"
            f"💸 Lost: {code(format_number(amount))} coins\n"
            f"👛 Balance: {code(format_number(eco.balance))} coins"
        )

    await _save_economy(eco)
    await _reply(update, text)


# ── /slots ────────────────────────────────────────────────────────────────────

async def slots_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/slots — Slot machine game."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    args = context.args or []
    bet = 100
    if args:
        try:
            bet = int(args[0])
        except ValueError:
            pass

    eco = await _get_or_create_economy(user.id, chat.id)
    if bet <= 0:
        await _reply(update, "❌ Bet must be positive.")
        return
    if bet > eco.balance:
        await _reply(update, f"❌ Not enough coins! You have {code(format_number(eco.balance))} coins.")
        return

    reels = [random.choice(SLOT_EMOJIS) for _ in range(3)]
    combo = "".join(reels)
    multiplier = SLOT_MULTIPLIERS.get(combo, 0)

    eco.balance -= bet
    display = f"[ {reels[0]} | {reels[1]} | {reels[2]} ]"

    if multiplier > 0:
        winnings = bet * multiplier
        eco.balance += winnings
        text = (
            f"🎰 {bold('Slot Machine')}\n\n"
            f"{display}\n\n"
            f"🎉 {bold('WINNER!')} (×{multiplier})\n"
            f"💰 Won: {code(format_number(winnings))} coins\n"
            f"👛 Balance: {code(format_number(eco.balance))} coins"
        )
    else:
        text = (
            f"🎰 {bold('Slot Machine')}\n\n"
            f"{display}\n\n"
            f"😞 No match!\n"
            f"💸 Lost: {code(format_number(bet))} coins\n"
            f"👛 Balance: {code(format_number(eco.balance))} coins"
        )

    await _save_economy(eco)
    await _reply(update, text)


# ── /richest ──────────────────────────────────────────────────────────────────

async def richest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/richest — Top 10 richest users in the group."""
    chat = update.effective_chat
    if not chat:
        return

    async with get_session() as session:
        stmt = (
            select(Economy)
            .where(Economy.chat_id == chat.id)
            .order_by((Economy.balance + Economy.bank).desc())
            .limit(10)
        )
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        await _reply(update, "📊 No economy data yet!")
        return

    lines = [f"🏆 {bold('Richest Members')}\n"]
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7

    for i, eco in enumerate(rows):
        try:
            member = await context.bot.get_chat(eco.user_id)
            name = escape_html(member.first_name or str(eco.user_id))
        except TelegramError:
            name = str(eco.user_id)

        total = eco.balance + eco.bank
        lines.append(f"{medals[i]} {bold(name)}: {code(format_number(total))} coins")

    await _reply(update, "\n".join(lines))


# ── Callback handler ──────────────────────────────────────────────────────────

async def economy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle eco:* callback queries."""
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if not query or not user or not chat:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "shop":
        lines = [f"🛒 {bold('Item Shop')}\n"]
        for item_id, item in SHOP_ITEMS.items():
            lines.append(
                f"{item['name']}\n"
                f"  💰 {code(format_number(item['price']))} coins\n"
                f"  📝 {item['desc']}\n"
                f"  🔑 {code(item_id)}"
            )
        try:
            await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=_shop_keyboard())
        except TelegramError:
            pass

    elif action == "buy":
        item_id = parts[2] if len(parts) > 2 else ""
        if item_id not in SHOP_ITEMS:
            await query.answer("❌ Unknown item.", show_alert=True)
            return
        item = SHOP_ITEMS[item_id]
        eco = await _get_or_create_economy(user.id, chat.id)
        if eco.balance < item["price"]:
            await query.answer(f"❌ Not enough coins! Need {format_number(item['price'])}.", show_alert=True)
            return
        inv = eco.inventory or {}
        if item_id in inv:
            await query.answer("⚠️ You already own this item!", show_alert=True)
            return
        eco.balance -= item["price"]
        inv[item_id] = {"purchased_at": datetime.now(timezone.utc).isoformat()}
        eco.inventory = inv
        await _save_economy(eco)
        await query.answer(f"✅ Bought {item['name']} for {format_number(item['price'])} coins!", show_alert=True)

    elif action == "inventory":
        eco = await _get_or_create_economy(user.id, chat.id)
        inv = eco.inventory or {}
        if not inv:
            await query.answer("🎒 Your inventory is empty!", show_alert=True)
            return
        item_list = ", ".join(SHOP_ITEMS[k]["name"] for k in inv if k in SHOP_ITEMS)
        await query.answer(f"🎒 Items: {item_list}", show_alert=True)

    elif action == "bank":
        uid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else user.id
        eco = await _get_or_create_economy(uid, chat.id)
        await query.answer(
            f"🏦 Bank: {format_number(eco.bank)} coins\n👛 Wallet: {format_number(eco.balance)} coins",
            show_alert=True
        )


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("bal", balance_command))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler("weekly", weekly_command))
    app.add_handler(CommandHandler("work", work_command))
    app.add_handler(CommandHandler("beg", beg_command))
    app.add_handler(CommandHandler("crime", crime_command))
    app.add_handler(CommandHandler("rob", rob_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("withdraw", withdraw_command))
    app.add_handler(CommandHandler("transfer", transfer_command))
    app.add_handler(CommandHandler("give", give_command))
    app.add_handler(CommandHandler("shop", shop_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("inventory", inventory_command))
    app.add_handler(CommandHandler("inv", inventory_command))
    app.add_handler(CommandHandler("gamble", gamble_command))
    app.add_handler(CommandHandler("slots", slots_command))
    app.add_handler(CommandHandler("richest", richest_command))
    app.add_handler(CallbackQueryHandler(economy_callback, pattern=r"^eco:"))
