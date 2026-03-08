"""
bot/plugins/fun.py
──────────────────
Fun commands: truth/dare, ship, 8ball, dice, jokes, roasts, text transforms,
countdown, roulette, and more.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import unicodedata
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.helpers.formatters import bold, code, escape_html, italic, spoiler, user_mention

logger = logging.getLogger(__name__)

# ── Data ──────────────────────────────────────────────────────────────────────

TRUTH_QUESTIONS = [
    "What's the most embarrassing thing you've done in public?",
    "Have you ever lied to get out of trouble? What was the lie?",
    "What's your biggest fear?",
    "Have you ever cheated on a test?",
    "What's the worst gift you've ever received?",
    "Have you ever talked behind a friend's back?",
    "What's the most childish thing you still do?",
    "What's a secret you've never told anyone?",
    "Have you ever pretended to be sick to avoid something?",
    "What's the biggest mistake you've ever made?",
    "What's something you're deeply embarrassed about?",
    "Have you ever stolen something?",
    "What's your most irrational fear?",
    "Who was your first crush?",
    "What's the most ridiculous thing you believed as a child?",
    "Have you ever ghosted someone?",
    "What's your most embarrassing nickname?",
    "What's the worst thing you've ever eaten?",
    "Have you ever lied about your age?",
    "What's something you've done that you hope your parents never find out about?",
]

DARE_CHALLENGES = [
    "Send a voice message saying 'I am a potato' 3 times.",
    "Change your bio to something embarrassing for 10 minutes.",
    "Type with your elbows for the next 3 messages.",
    "Send a selfie with a silly face.",
    "Write a 5-line poem about the person to your left.",
    "Do 10 push-ups and post proof.",
    "Send a message in a fake foreign accent (describe it).",
    "Compliment every member in the group right now.",
    "Sing the first verse of your national anthem (voice message).",
    "Post your most recent search history item.",
    "Do your best impression of a famous person in voice.",
    "Say something nice about your biggest rival.",
    "Tell a joke — if nobody laughs, do another dare.",
    "Share the last photo in your camera roll.",
    "Text your mom 'I love you and I owe you a hug'.",
    "Speak only in questions for the next 5 minutes.",
    "Write a haiku about a random group member.",
    "Try to lick your elbow and describe the attempt.",
    "Make a dramatic reading of the last message in this chat.",
    "Set a ridiculous reminder on your phone and show it.",
]

EIGHT_BALL_RESPONSES = [
    "🟢 It is certain.",
    "🟢 It is decidedly so.",
    "🟢 Without a doubt.",
    "🟢 Yes, definitely.",
    "🟢 You may rely on it.",
    "🟢 As I see it, yes.",
    "🟢 Most likely.",
    "🟢 Outlook good.",
    "🟢 Yes.",
    "🟢 Signs point to yes.",
    "🟡 Reply hazy, try again.",
    "🟡 Ask again later.",
    "🟡 Better not tell you now.",
    "🟡 Cannot predict now.",
    "🟡 Concentrate and ask again.",
    "🔴 Don't count on it.",
    "🔴 My reply is no.",
    "🔴 My sources say no.",
    "🔴 Outlook not so good.",
    "🔴 Very doubtful.",
]

JOKES = [
    ("Why don't scientists trust atoms?", "Because they make up everything! 😄"),
    ("What do you call a fake noodle?", "An impasta! 🍝"),
    ("Why did the scarecrow win an award?", "Because he was outstanding in his field! 🌾"),
    ("What do you call cheese that isn't yours?", "Nacho cheese! 🧀"),
    ("Why can't you give Elsa a balloon?", "Because she'll let it go! 🎈"),
    ("What do you call a sleeping dinosaur?", "A dino-snore! 🦕"),
    ("Why did the bicycle fall over?", "Because it was two-tired! 🚲"),
    ("What do you call a fish without eyes?", "A fsh! 🐟"),
    ("Why don't eggs tell jokes?", "They'd crack each other up! 🥚"),
    ("What do you call a boomerang that won't come back?", "A stick! 🪃"),
    ("What do you get when you cross a snowman and a vampire?", "Frostbite! ❄️🧛"),
    ("Why did the math book look so sad?", "Because it had too many problems! 📚"),
    ("What do you call a factory that makes okay products?", "A satisfactory! 🏭"),
    ("Why do cows wear bells?", "Because their horns don't work! 🐄"),
    ("What did the ocean say to the beach?", "Nothing, it just waved! 🌊"),
]

ROASTS = [
    "You're like a cloud — when you disappear, it's a beautiful day! ☀️",
    "I'd agree with you but then we'd both be wrong. 🤔",
    "You have your entire life to be an idiot. Why not take today off? 😴",
    "If laughter is the best medicine, your face must be curing diseases! 💊",
    "I've met some pricks in my time, but you're a whole cactus. 🌵",
    "You bring everyone so much joy... when you leave the room. 🚪",
    "I'd explain it to you but I left my crayons at home. 🖍️",
    "Your secrets are always safe with me. I never pay attention anyway. 🙉",
    "I'd give you a nasty look but you've already got one. 😐",
    "Some people are like slinkies — not really good for much but bring a smile when pushed down stairs. 😈",
    "You have the right to remain silent. Please use it. 🤫",
    "My phone battery lasts longer than most of your relationships. 🔋",
    "If you were any more inbred you'd be a sandwich. 🥪",
    "Even Siri tells you to get a life. 📱",
    "Your WiFi password is probably '1234' and your life choices show it. 📡",
]

COMPLIMENTS = [
    "You light up every room you walk into! ✨",
    "Your smile could outshine the sun! ☀️",
    "You have an incredible ability to make everyone around you feel special. 💫",
    "The world is genuinely a better place with you in it! 🌍",
    "Your creativity is absolutely inspiring! 🎨",
    "You're stronger than you know. Keep going! 💪",
    "You have a heart of gold! 💛",
    "Your kindness never goes unnoticed. 🌺",
    "You're one of the most genuine people I've ever met! 💎",
    "Your positive energy is absolutely contagious! ⚡",
    "You make the impossible look easy! 🌟",
    "Spending time with you is always the highlight of the day! 🌈",
    "You're not just talented — you're exceptional! 🏆",
    "Your intelligence is truly impressive! 🧠",
    "You're the definition of awesome! 🎉",
]

FACTS = [
    "Honey never spoils — archaeologists have found 3,000-year-old honey in Egyptian tombs. 🍯",
    "A group of flamingos is called a 'flamboyance'. 🦩",
    "Octopuses have three hearts and blue blood. 🐙",
    "The shortest war in history lasted 38–45 minutes (Anglo-Zanzibar War, 1896). ⚔️",
    "Bananas are berries, but strawberries aren't. 🍌🍓",
    "A day on Venus is longer than a year on Venus. 🌍",
    "The human brain generates about 70,000 thoughts per day. 🧠",
    "Cleopatra lived closer in time to the Moon landing than to the construction of the Great Pyramid. 🏛️",
    "There are more possible iterations of a game of chess than atoms in the observable universe. ♟️",
    "Sharks are older than trees — they've existed for over 400 million years. 🦈",
    "The total weight of all ants on Earth roughly equals the weight of all humans. 🐜",
    "Wombat poop is cube-shaped. 🦘",
    "A cloud can weigh over a million pounds. ☁️",
    "The average person walks about 100,000 miles in their lifetime. 👣",
    "Butterflies taste with their feet. 🦋",
]

QUOTES = [
    ("The only way to do great work is to love what you do.", "Steve Jobs"),
    ("In the middle of difficulty lies opportunity.", "Albert Einstein"),
    ("It does not matter how slowly you go as long as you do not stop.", "Confucius"),
    ("Life is what happens when you're busy making other plans.", "John Lennon"),
    ("The future belongs to those who believe in the beauty of their dreams.", "Eleanor Roosevelt"),
    ("It is during our darkest moments that we must focus to see the light.", "Aristotle"),
    ("Spread love everywhere you go. Let no one ever come to you without leaving happier.", "Mother Teresa"),
    ("When you reach the end of your rope, tie a knot in it and hang on.", "Franklin D. Roosevelt"),
    ("Don't judge each day by the harvest you reap but by the seeds that you plant.", "Robert Louis Stevenson"),
    ("The best time to plant a tree was 20 years ago. The second best time is now.", "Chinese Proverb"),
]

ADVICE = [
    "Drink more water. Your future self will thank you. 💧",
    "Sleep is not a luxury — it's a necessity. Prioritize it. 😴",
    "Don't compare your chapter 1 to someone else's chapter 20. 📖",
    "Take the trip. Buy the coffee. Life is short. ✈️",
    "The person who says it cannot be done should not interrupt the person doing it. 🚀",
    "Reply to that message you've been putting off. Now. 📱",
    "It's okay to say no. Your mental health is important. 🧘",
    "Spend time with people who make you feel good about yourself. 🤝",
    "Learn one new thing every day, even if it's tiny. 📚",
    "Be the energy you want to attract. ✨",
    "You don't have to get it right the first time. Just start. 🌱",
    "Failure is just a stepping stone to success. Keep going. 💪",
    "Forgive yourself. You're doing better than you think. 💛",
    "Put your phone down and look around. The world is beautiful. 🌍",
]

FORTUNES = [
    "🥠 You will find great success in an unexpected place.",
    "🥠 A smile is your umbrella in the storm of life.",
    "🥠 Good things come to those who hustle.",
    "🥠 Your creativity will lead you to great heights today.",
    "🥠 A random act of kindness will have a big impact.",
    "🥠 The answer you seek is already within you.",
    "🥠 Adventure awaits — say yes more often.",
    "🥠 Your hard work will be rewarded soon.",
    "🥠 Today is an excellent day for new beginnings.",
    "🥠 Trust your instincts — they are usually right.",
    "🥠 Someone is thinking about you right now.",
    "🥠 A pleasant surprise is heading your way.",
    "🥠 Help someone today and the universe will help you tomorrow.",
    "🥠 You are more powerful than you realize.",
]

RIDDLES = [
    ("I speak without a mouth and hear without ears. I have no body, but I come alive with wind. What am I?", "An echo! 🌬️"),
    ("The more you take, the more you leave behind. What am I?", "Footsteps! 👣"),
    ("I have cities, but no houses live there. I have mountains, but no trees grow there. I have water, but no fish swim there. What am I?", "A map! 🗺️"),
    ("What has hands but can't clap?", "A clock! ⏰"),
    ("I can fly without wings. I can cry without eyes. Wherever I go, darkness follows me. What am I?", "A cloud! ☁️"),
]

WORK_JOBS = ["delivery driver", "barista", "coder", "chef", "teacher", "mechanic"]


def _uwuify(text: str) -> str:
    subs = [
        (r"[rl]", "w"),
        (r"[RL]", "W"),
        (r"n([aeiou])", r"ny\1"),
        (r"N([aeiou])", r"Ny\1"),
        (r"N([AEIOU])", r"NY\1"),
        (r"ove", "uv"),
    ]
    for pattern, repl in subs:
        text = re.sub(pattern, repl, text)
    suffixes = ["uwu", "OwO", ">w<", "^w^", "~", "!!"]
    text += f" {random.choice(suffixes)}"
    return text


def _mock(text: str) -> str:
    return "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))


def _clap(text: str) -> str:
    return " 👏 ".join(text.split())


def _aesthetic(text: str) -> str:
    result = []
    for ch in text:
        cp = ord(ch)
        if 0x21 <= cp <= 0x7E:
            result.append(chr(cp + 0xFF00 - 0x21))
        else:
            result.append(ch)
    return "".join(result)


def _zalgo(text: str) -> str:
    zalgo_chars = [
        "\u0300", "\u0301", "\u0302", "\u0303", "\u0308", "\u030A",
        "\u0315", "\u031C", "\u0321", "\u0322", "\u0327", "\u0328",
    ]
    result = []
    for ch in text:
        result.append(ch)
        for _ in range(random.randint(2, 5)):
            result.append(random.choice(zalgo_chars))
    return "".join(result)


def _regional_indicator(text: str) -> str:
    """Convert text to regional indicator symbols (big text)."""
    result = []
    for ch in text.lower():
        cp = ord(ch)
        if ord('a') <= cp <= ord('z'):
            result.append(chr(0x1F1E6 + cp - ord('a')))
        elif ch == ' ':
            result.append('  ')
        else:
            result.append(ch)
    return ' '.join(result)


async def _reply(update: Update, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


def _get_text_arg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """Get text from args or from a replied-to message."""
    if context.args:
        return " ".join(context.args)
    msg = update.effective_message
    if msg and msg.reply_to_message:
        return msg.reply_to_message.text or msg.reply_to_message.caption
    return None


# ── /truth ────────────────────────────────────────────────────────────────────

async def truth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/truth — Random truth question."""
    q = random.choice(TRUTH_QUESTIONS)
    await _reply(update, f"🔮 {bold('Truth!')}\n\n{escape_html(q)}")


# ── /dare ─────────────────────────────────────────────────────────────────────

async def dare_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dare — Random dare challenge."""
    d = random.choice(DARE_CHALLENGES)
    await _reply(update, f"😈 {bold('Dare!')}\n\n{escape_html(d)}")


# ── /ship ─────────────────────────────────────────────────────────────────────

async def ship_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ship @user1 [@user2] — Compatibility percentage."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    name1 = escape_html(user.first_name)
    name2: str

    if msg.reply_to_message and msg.reply_to_message.from_user:
        u2 = msg.reply_to_message.from_user
        name2 = escape_html(u2.first_name)
    elif context.args:
        arg = context.args[0].lstrip("@")
        try:
            tg = await context.bot.get_chat(f"@{arg}")
            name2 = escape_html(tg.first_name or arg)
        except TelegramError:
            name2 = escape_html(arg)
    else:
        name2 = "Mystery Person"

    # Deterministic but "random" based on name hash
    score = (hash(name1.lower() + name2.lower()) % 100 + 100) % 100

    hearts = "❤️" * (score // 10) + "🖤" * (10 - score // 10)
    if score >= 80:
        verdict = "💑 Perfect match! You're meant to be!"
    elif score >= 60:
        verdict = "💕 Great compatibility! Give it a shot!"
    elif score >= 40:
        verdict = "💛 It could work with some effort!"
    elif score >= 20:
        verdict = "🤔 A bit of a stretch…"
    else:
        verdict = "💔 Maybe just stay friends."

    await _reply(update,
        f"💘 {bold('Ship Meter')}\n\n"
        f"👤 {bold(name1)} + {bold(name2)}\n\n"
        f"{hearts}\n"
        f"💯 Compatibility: {bold(str(score))}%\n\n"
        f"{verdict}"
    )


# ── /8ball ────────────────────────────────────────────────────────────────────

async def eightball_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/8ball <question> — Magic 8-ball."""
    if not context.args:
        await _reply(update, f"🎱 Ask me a question!\nUsage: {code('/8ball &lt;question&gt;')}")
        return
    question = escape_html(" ".join(context.args))
    answer = random.choice(EIGHT_BALL_RESPONSES)
    await _reply(update, f"🎱 {bold('Magic 8-Ball')}\n\n❓ {italic(question)}\n\n{answer}")


# ── /roll ─────────────────────────────────────────────────────────────────────

async def roll_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/roll [XdY+Z] — Roll dice."""
    expr = context.args[0] if context.args else "1d6"
    pattern = re.match(r"(\d+)d(\d+)(?:\+(\d+))?$", expr.lower())
    if not pattern:
        await _reply(update, f"❌ Invalid format. Use {code('XdY')} or {code('XdY+Z')}\nExample: {code('2d6+5')}")
        return

    num = min(int(pattern.group(1)), 20)
    sides = min(int(pattern.group(2)), 1000)
    bonus = int(pattern.group(3) or 0)

    if sides < 2:
        await _reply(update, "❌ Dice must have at least 2 sides.")
        return

    rolls = [random.randint(1, sides) for _ in range(num)]
    total = sum(rolls) + bonus

    rolls_str = ", ".join(str(r) for r in rolls)
    text = f"🎲 {bold('Dice Roll')}\n\n"
    text += f"Rolling {bold(f'{num}d{sides}')}"
    if bonus:
        text += f"+{bonus}"
    text += f"\n\n🎯 Rolls: {code(rolls_str)}"
    if len(rolls) > 1:
        text += f"\n📊 Sum: {code(str(sum(rolls)))}"
    if bonus:
        text += f"\n➕ Bonus: {code(str(bonus))}"
    text += f"\n✨ {bold('Total:')} {code(str(total))}"

    await _reply(update, text)


# ── /flip ─────────────────────────────────────────────────────────────────────

async def flip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/flip — Coin flip."""
    result = random.choice([("🪙 Heads!", "heads"), ("🪙 Tails!", "tails")])
    await _reply(update, f"{bold('Coin Flip')}\n\n{result[0]}")


# ── /rps ──────────────────────────────────────────────────────────────────────

async def rps_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rps <rock|paper|scissors> — Rock paper scissors vs bot."""
    if not context.args:
        await _reply(update, f"✊✋✌️ Usage: {code('/rps &lt;rock|paper|scissors&gt;')}")
        return

    player = context.args[0].lower().strip()
    valid = {"rock": "✊", "paper": "✋", "scissors": "✌️"}
    if player not in valid:
        await _reply(update, f"❌ Choose: {code('rock')}, {code('paper')}, or {code('scissors')}")
        return

    bot_choice = random.choice(list(valid.keys()))
    wins = {"rock": "scissors", "scissors": "paper", "paper": "rock"}

    p_emoji = valid[player]
    b_emoji = valid[bot_choice]

    if player == bot_choice:
        result = f"🤝 {bold('Draw!')} We both chose {bold(player)}."
    elif wins[player] == bot_choice:
        result = f"🎉 {bold('You Win!')} {p_emoji} beats {b_emoji}!"
    else:
        result = f"😈 {bold('Bot Wins!')} {b_emoji} beats {p_emoji}!"

    await _reply(update, f"✊✋✌️ {bold('Rock Paper Scissors')}\n\nYou: {p_emoji} {player}\nBot: {b_emoji} {bot_choice}\n\n{result}")


# ── /choose ───────────────────────────────────────────────────────────────────

async def choose_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/choose <opt1> | <opt2> ... — Choose between options."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/choose &lt;option1&gt; | &lt;option2&gt; | ...')}")
        return

    options = [o.strip() for o in text.split("|") if o.strip()]
    if len(options) < 2:
        await _reply(update, "❌ Please provide at least 2 options separated by |")
        return

    chosen = random.choice(options)
    await _reply(update,
        f"🎯 {bold('I Choose...')}\n\n"
        + "\n".join(f"• {escape_html(o)}" for o in options)
        + f"\n\n✅ {bold(escape_html(chosen))}"
    )


# ── /joke ─────────────────────────────────────────────────────────────────────

async def joke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/joke — Random joke."""
    setup, punchline = random.choice(JOKES)
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🥁 Punchline", callback_data=f"fun:punchline:{escape_html(punchline)[:50]}")]])
    await _reply(update, f"😂 {bold('Joke Time!')}\n\n{escape_html(setup)}", markup)


async def joke_punchline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    parts = (query.data or "").split(":", 2)
    punchline = parts[2] if len(parts) > 2 else "..."
    try:
        original = query.message.text or ""
        await query.edit_message_text(
            original + f"\n\n🥁 {bold(escape_html(punchline))}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


# ── /roast ────────────────────────────────────────────────────────────────────

async def roast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/roast [@user] — Roast someone."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    target_name: str
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        target_name = escape_html(u.first_name)
    elif context.args:
        target_name = escape_html(context.args[0].lstrip("@"))
    else:
        target_name = escape_html(user.first_name)

    roast = random.choice(ROASTS)
    await _reply(update, f"🔥 {bold(target_name)}, {roast}")


# ── /compliment ───────────────────────────────────────────────────────────────

async def compliment_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/compliment [@user] — Compliment someone."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    target_name: str
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        target_name = escape_html(u.first_name)
    elif context.args:
        target_name = escape_html(context.args[0].lstrip("@"))
    else:
        target_name = escape_html(user.first_name)

    comp = random.choice(COMPLIMENTS)
    await _reply(update, f"💝 {bold(target_name)}, {comp}")


# ── /fact ─────────────────────────────────────────────────────────────────────

async def fact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/fact — Random interesting fact."""
    await _reply(update, f"🧠 {bold('Random Fact')}\n\n{random.choice(FACTS)}")


# ── /quote ────────────────────────────────────────────────────────────────────

async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quote — Inspirational quote."""
    text, author = random.choice(QUOTES)
    await _reply(update, f"💭 {italic(f'"{escape_html(text)}"')}\n\n— {bold(escape_html(author))}")


# ── /advice ───────────────────────────────────────────────────────────────────

async def advice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/advice — Random life advice."""
    await _reply(update, f"💡 {bold('Life Advice')}\n\n{random.choice(ADVICE)}")


# ── /fortune ──────────────────────────────────────────────────────────────────

async def fortune_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/fortune — Fortune cookie."""
    await _reply(update, f"🥠 {bold('Fortune Cookie')}\n\n{random.choice(FORTUNES)}")


# ── /mock ─────────────────────────────────────────────────────────────────────

async def mock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mock <text or reply> — SpongeBob mock text."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/mock &lt;text&gt;')} or reply to a message")
        return
    await _reply(update, escape_html(_mock(text)))


# ── /clap ─────────────────────────────────────────────────────────────────────

async def clap_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clap <text or reply> — Add 👏 between words."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/clap &lt;text&gt;')} or reply to a message")
        return
    await _reply(update, _clap(text))


# ── /aesthetic ────────────────────────────────────────────────────────────────

async def aesthetic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/aesthetic <text> — Fullwidth aesthetic text."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/aesthetic &lt;text&gt;')}")
        return
    await _reply(update, _aesthetic(text))


# ── /reverse ──────────────────────────────────────────────────────────────────

async def reverse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reverse <text or reply> — Reverse text."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/reverse &lt;text&gt;')} or reply to a message")
        return
    await _reply(update, escape_html(text[::-1]))


# ── /uwu ──────────────────────────────────────────────────────────────────────

async def uwu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/uwu <text or reply> — UwU-ify text."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/uwu &lt;text&gt;')} or reply to a message")
        return
    await _reply(update, escape_html(_uwuify(text)))


# ── /shrug, /tableflip, /unflip ──────────────────────────────────────────────

async def shrug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(r"¯\_(ツ)_/¯")


async def tableflip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("(╯°□°）╯︵ ┻━┻")


async def unflip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("┬─┬ノ(° -°ノ)")


# ── /zalgo ────────────────────────────────────────────────────────────────────

async def zalgo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/zalgo <text> — Zalgo glitch text."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/zalgo &lt;text&gt;')}")
        return
    await update.effective_message.reply_text(_zalgo(text[:50]))


# ── /big ──────────────────────────────────────────────────────────────────────

async def big_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/big <text> — Big text using regional indicators."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/big &lt;text&gt;')}")
        return
    await update.effective_message.reply_text(_regional_indicator(text[:20]))


# ── /spoiler ──────────────────────────────────────────────────────────────────

async def spoiler_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/spoiler <text> — Format as spoiler."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/spoiler &lt;text&gt;')}")
        return
    await update.effective_message.reply_text(
        f"<tg-spoiler>{escape_html(text)}</tg-spoiler>",
        parse_mode=ParseMode.HTML,
    )


# ── /countdown ────────────────────────────────────────────────────────────────

async def countdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/countdown <n> [text] — Count down from n with message edits."""
    args = context.args or []
    if not args:
        await _reply(update, f"❌ Usage: {code('/countdown &lt;n&gt; [text]')}")
        return
    try:
        n = int(args[0])
    except ValueError:
        await _reply(update, "❌ Please provide a valid number.")
        return
    n = min(max(n, 1), 30)  # clamp 1-30
    label = escape_html(" ".join(args[1:])) if len(args) > 1 else "Countdown"

    sent = await update.effective_message.reply_text(
        f"⏱️ {bold(label)}: {code(str(n))}",
        parse_mode=ParseMode.HTML,
    )

    for i in range(n - 1, -1, -1):
        await asyncio.sleep(1)
        try:
            if i == 0:
                await sent.edit_text(f"🎉 {bold(label)}: {code('BLAST OFF! 🚀')}", parse_mode=ParseMode.HTML)
            else:
                await sent.edit_text(f"⏱️ {bold(label)}: {code(str(i))}", parse_mode=ParseMode.HTML)
        except TelegramError:
            break


# ── /roulette ─────────────────────────────────────────────────────────────────

async def roulette_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/roulette — Russian roulette game."""
    user = update.effective_user
    if not user:
        return

    name = escape_html(user.first_name)
    # 1 in 6 chance
    shot = random.randint(1, 6) == 1

    if shot:
        await _reply(update,
            f"🔫 {bold('Russian Roulette')}\n\n"
            f"{bold(name)} pulled the trigger...\n\n"
            f"💥 {bold('BANG!')} — Better luck in the next life! 😵"
        )
    else:
        await _reply(update,
            f"🔫 {bold('Russian Roulette')}\n\n"
            f"{bold(name)} pulled the trigger...\n\n"
            f"😮‍💨 {bold('Click!')} — You survived! (This time...) 😅"
        )


# ── Callback dispatcher ───────────────────────────────────────────────────────

async def fun_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch fun:* callbacks."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    parts = (query.data or "").split(":", 2)
    action = parts[1] if len(parts) > 1 else ""

    if action == "punchline":
        punchline = parts[2] if len(parts) > 2 else "..."
        try:
            original = query.message.text or ""
            await query.edit_message_text(
                original + f"\n\n🥁 {bold(escape_html(punchline))}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("truth", truth_command))
    app.add_handler(CommandHandler("dare", dare_command))
    app.add_handler(CommandHandler("ship", ship_command))
    app.add_handler(CommandHandler("8ball", eightball_command))
    app.add_handler(CommandHandler("roll", roll_command))
    app.add_handler(CommandHandler("flip", flip_command))
    app.add_handler(CommandHandler("rps", rps_command))
    app.add_handler(CommandHandler("choose", choose_command))
    app.add_handler(CommandHandler("joke", joke_command))
    app.add_handler(CommandHandler("roast", roast_command))
    app.add_handler(CommandHandler("compliment", compliment_command))
    app.add_handler(CommandHandler("fact", fact_command))
    app.add_handler(CommandHandler("quote", quote_command))
    app.add_handler(CommandHandler("advice", advice_command))
    app.add_handler(CommandHandler("fortune", fortune_command))
    app.add_handler(CommandHandler("mock", mock_command))
    app.add_handler(CommandHandler("clap", clap_command))
    app.add_handler(CommandHandler("aesthetic", aesthetic_command))
    app.add_handler(CommandHandler("reverse", reverse_command))
    app.add_handler(CommandHandler("uwu", uwu_command))
    app.add_handler(CommandHandler("shrug", shrug_command))
    app.add_handler(CommandHandler("tableflip", tableflip_command))
    app.add_handler(CommandHandler("unflip", unflip_command))
    app.add_handler(CommandHandler("zalgo", zalgo_command))
    app.add_handler(CommandHandler("big", big_command))
    app.add_handler(CommandHandler("spoiler", spoiler_command))
    app.add_handler(CommandHandler("countdown", countdown_command))
    app.add_handler(CommandHandler("roulette", roulette_command))
    app.add_handler(CallbackQueryHandler(fun_callback, pattern=r"^fun:"))
