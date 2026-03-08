"""
bot/plugins/games.py
─────────────────────
Interactive games: TicTacToe, Trivia, Hangman, Wordle, Blackjack,
Math Challenge, Riddles, RPS Tournament, Custom Quiz, and stats.
All games use inline keyboards and persist state via GameSession model.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.database.connection import get_session
from bot.database.models import GameSession, User
from bot.helpers.formatters import bold, code, escape_html, italic, progress_bar, user_mention

logger = logging.getLogger(__name__)

# ── TicTacToe ─────────────────────────────────────────────────────────────────

TTT_EMPTY = "⬜"
TTT_X = "❌"
TTT_O = "⭕"


def _ttt_board_markup(board: List[str], game_id: int) -> InlineKeyboardMarkup:
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            idx = r * 3 + c
            cell = board[idx]
            row.append(InlineKeyboardButton(cell, callback_data=f"ttt:{game_id}:{idx}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🚩 Resign", callback_data=f"ttt:{game_id}:resign")])
    return InlineKeyboardMarkup(rows)


def _ttt_check_winner(board: List[str]) -> Optional[str]:
    wins = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]
    for a, b, c in wins:
        if board[a] != TTT_EMPTY and board[a] == board[b] == board[c]:
            return board[a]
    return None


async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tictactoe [@user] — Start a TicTacToe game."""
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message
    if not chat or not user or not msg:
        return

    opponent_id: Optional[int] = None
    opponent_name = "Anyone"

    if msg.reply_to_message and msg.reply_to_message.from_user:
        opp = msg.reply_to_message.from_user
        if opp.is_bot:
            await msg.reply_text("❌ You can't challenge a bot!", parse_mode=ParseMode.HTML)
            return
        opponent_id = opp.id
        opponent_name = escape_html(opp.first_name)
    elif context.args:
        arg = context.args[0].lstrip("@")
        try:
            tg = await context.bot.get_chat(f"@{arg}")
            opponent_id = tg.id
            opponent_name = escape_html(tg.first_name or arg)
        except TelegramError:
            pass

    board = [TTT_EMPTY] * 9
    data: Dict[str, Any] = {
        "board": board,
        "x_player": user.id,
        "o_player": opponent_id,
        "current": user.id,
        "x_name": escape_html(user.first_name),
        "o_name": opponent_name,
    }

    async with get_session() as session:
        gs = GameSession(chat_id=chat.id, game_type="tictactoe", data=data, active=True)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)

    challenge_text = f"challenged {bold(opponent_name)}" if opponent_id else "is looking for an opponent"
    text = (
        f"🎮 {bold('TicTacToe!')}\n\n"
        f"{user_mention(user.id, user.first_name)} {challenge_text}\n"
        f"❌ = {bold(data['x_name'])}\n"
        f"⭕ = {bold(opponent_name)}\n\n"
        f"Turn: {bold(data['x_name'])} (❌)"
    )
    await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_ttt_board_markup(board, gs.id))


async def ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle TicTacToe moves."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return

    game_id_str = parts[1]
    move = parts[2]

    if not game_id_str.isdigit():
        return

    game_id = int(game_id_str)

    async with get_session() as session:
        stmt = select(GameSession).where(GameSession.id == game_id, GameSession.active == True)
        gs = (await session.execute(stmt)).scalar_one_or_none()
        if not gs:
            await query.answer("❌ Game not found or already over.", show_alert=True)
            return

        data = dict(gs.data)
        board: List[str] = data["board"]
        x_player: int = data["x_player"]
        o_player: Optional[int] = data["o_player"]
        current: int = data["current"]

        # Allow anyone to join as O if no opponent set
        if o_player is None and user.id != x_player:
            data["o_player"] = user.id
            data["o_name"] = escape_html(user.first_name)
            o_player = user.id

        if user.id != current:
            await query.answer("⏳ It's not your turn!", show_alert=True)
            return
        if o_player and user.id not in (x_player, o_player):
            await query.answer("❌ You're not in this game!", show_alert=True)
            return

        if move == "resign":
            gs.active = False
            winner_name = data["o_name"] if user.id == x_player else data["x_name"]
            await session.commit()
            try:
                await query.edit_message_text(
                    f"🏳️ {bold(escape_html(user.first_name))} resigned!\n🏆 {bold(winner_name)} wins!",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
            return

        idx = int(move) if move.isdigit() else -1
        if idx < 0 or idx > 8 or board[idx] != TTT_EMPTY:
            await query.answer("❌ Invalid move!", show_alert=True)
            return

        symbol = TTT_X if user.id == x_player else TTT_O
        board[idx] = symbol

        winner = _ttt_check_winner(board)
        if winner:
            gs.active = False
            win_name = data["x_name"] if winner == TTT_X else data["o_name"]
            gs.data = {**data, "board": board}
            await session.commit()
            try:
                await query.edit_message_text(
                    f"🎉 {bold(win_name)} wins!\n\n" + _ttt_board_str(board),
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
            return

        if TTT_EMPTY not in board:
            gs.active = False
            gs.data = {**data, "board": board}
            await session.commit()
            try:
                await query.edit_message_text(
                    f"🤝 {bold('Draw!')} No more moves.\n\n" + _ttt_board_str(board),
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
            return

        next_player = o_player if user.id == x_player else x_player
        next_name = data["o_name"] if user.id == x_player else data["x_name"]
        next_symbol = TTT_O if user.id == x_player else TTT_X
        data.update({"board": board, "current": next_player})
        gs.data = data
        await session.commit()

    try:
        await query.edit_message_reply_markup(reply_markup=_ttt_board_markup(board, game_id))
        await query.edit_message_text(
            f"🎮 {bold('TicTacToe')}\n\n"
            f"❌ {bold(data['x_name'])} vs ⭕ {bold(data['o_name'])}\n\n"
            f"Turn: {bold(next_name)} ({next_symbol})",
            parse_mode=ParseMode.HTML,
            reply_markup=_ttt_board_markup(board, game_id),
        )
    except TelegramError:
        pass


def _ttt_board_str(board: List[str]) -> str:
    rows = []
    for r in range(3):
        rows.append(" ".join(board[r * 3:(r + 1) * 3]))
    return "\n".join(rows)


# ── Trivia ────────────────────────────────────────────────────────────────────

TRIVIA_QUESTIONS = [
    {
        "q": "What is the capital of France?",
        "options": ["Berlin", "Madrid", "Paris", "Rome"],
        "answer": 2,
        "category": "Geography",
    },
    {
        "q": "Which planet is known as the Red Planet?",
        "options": ["Venus", "Mars", "Jupiter", "Saturn"],
        "answer": 1,
        "category": "Science",
    },
    {
        "q": "Who painted the Mona Lisa?",
        "options": ["Van Gogh", "Picasso", "Da Vinci", "Rembrandt"],
        "answer": 2,
        "category": "Art",
    },
    {
        "q": "What is the largest ocean on Earth?",
        "options": ["Atlantic", "Indian", "Arctic", "Pacific"],
        "answer": 3,
        "category": "Geography",
    },
    {
        "q": "In which year did World War II end?",
        "options": ["1943", "1944", "1945", "1946"],
        "answer": 2,
        "category": "History",
    },
    {
        "q": "What is the chemical symbol for gold?",
        "options": ["Go", "Gd", "Au", "Ag"],
        "answer": 2,
        "category": "Science",
    },
    {
        "q": "Who wrote 'Romeo and Juliet'?",
        "options": ["Charles Dickens", "William Shakespeare", "Jane Austen", "Mark Twain"],
        "answer": 1,
        "category": "Literature",
    },
    {
        "q": "What is the fastest land animal?",
        "options": ["Lion", "Horse", "Cheetah", "Greyhound"],
        "answer": 2,
        "category": "Animals",
    },
    {
        "q": "How many sides does a hexagon have?",
        "options": ["5", "6", "7", "8"],
        "answer": 1,
        "category": "Math",
    },
    {
        "q": "What language has the most native speakers?",
        "options": ["English", "Spanish", "Mandarin Chinese", "Hindi"],
        "answer": 2,
        "category": "Language",
    },
    {
        "q": "Which element has atomic number 1?",
        "options": ["Helium", "Hydrogen", "Carbon", "Oxygen"],
        "answer": 1,
        "category": "Science",
    },
    {
        "q": "What is the smallest country in the world?",
        "options": ["Monaco", "San Marino", "Vatican City", "Liechtenstein"],
        "answer": 2,
        "category": "Geography",
    },
]

TRIVIA_POINTS = 10
OPTION_EMOJIS = ["🅐", "🅑", "🅒", "🅓"]


async def trivia_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/trivia [category] — Start a trivia question."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    cat_filter = context.args[0].lower() if context.args else None
    pool = [q for q in TRIVIA_QUESTIONS if not cat_filter or q["category"].lower() == cat_filter]
    if not pool:
        pool = TRIVIA_QUESTIONS

    q = random.choice(pool)
    options_shuffled = list(enumerate(q["options"]))
    random.shuffle(options_shuffled)
    correct_new_idx = next(i for i, (orig, _) in enumerate(options_shuffled) if orig == q["answer"])

    data: Dict[str, Any] = {
        "question": q["q"],
        "options": [opt for _, opt in options_shuffled],
        "answer": correct_new_idx,
        "category": q["category"],
        "asked_by": user.id,
        "answered": {},
        "deadline": (datetime.now(timezone.utc).timestamp() + 30),
    }

    async with get_session() as session:
        gs = GameSession(chat_id=chat.id, game_type="trivia", data=data, active=True)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)

    options_text = "\n".join(
        f"{OPTION_EMOJIS[i]} {escape_html(opt)}"
        for i, opt in enumerate(data["options"])
    )
    text = (
        f"🧠 {bold('Trivia!')} [{escape_html(data['category'])}]\n\n"
        f"{bold(escape_html(q['q']))}\n\n"
        f"{options_text}\n\n"
        f"{italic('⏱️ 30 seconds to answer!')}"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{OPTION_EMOJIS[i]} {escape_html(opt)[:30]}", callback_data=f"trivia:{gs.id}:{i}")]
        for i, opt in enumerate(data["options"])
    ])

    sent = await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)

    # Auto-close after 30 seconds
    await asyncio.sleep(30)
    async with get_session() as session:
        stmt = select(GameSession).where(GameSession.id == gs.id)
        active_gs = (await session.execute(stmt)).scalar_one_or_none()
        if active_gs and active_gs.active:
            active_gs.active = False
            await session.commit()
            correct_opt = escape_html(data["options"][data["answer"]])
            try:
                await sent.edit_text(
                    text + f"\n\n⏰ {bold('Time\'s up!')} Answer: {bold(correct_opt)}",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass


async def trivia_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle trivia answers."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    game_id = int(parts[1]) if parts[1].isdigit() else 0
    choice = int(parts[2]) if parts[2].isdigit() else -1

    async with get_session() as session:
        stmt = select(GameSession).where(GameSession.id == game_id, GameSession.active == True)
        gs = (await session.execute(stmt)).scalar_one_or_none()
        if not gs:
            await query.answer("⏰ This trivia has ended!", show_alert=True)
            return

        data = dict(gs.data)
        answered: dict = data.get("answered", {})

        if str(user.id) in answered:
            await query.answer("✋ You already answered this question!", show_alert=True)
            return

        correct = data.get("answer", -1)
        answered[str(user.id)] = choice
        data["answered"] = answered
        gs.data = data

        if choice == correct:
            # Award points
            db_stmt = select(User).where(User.user_id == user.id)
            db_user = (await session.execute(db_stmt)).scalar_one_or_none()
            if db_user:
                db_user.xp = (db_user.xp or 0) + TRIVIA_POINTS
            await session.commit()
            await query.answer(f"✅ Correct! +{TRIVIA_POINTS} XP", show_alert=True)
        else:
            await session.commit()
            correct_opt = escape_html(data["options"][correct])
            await query.answer(f"❌ Wrong! Correct answer: {correct_opt}", show_alert=True)


# ── Hangman ───────────────────────────────────────────────────────────────────

HANGMAN_WORDS = [
    "python", "telegram", "keyboard", "adventure", "butterfly",
    "chocolate", "elephant", "paradise", "symphony", "universe",
    "quantum", "midnight", "mountain", "treasure", "horizon",
    "lightning", "umbrella", "pancake", "calendar", "whisper",
]

HANGMAN_ART = [
    "```\n  ┌─┐\n  │  \n     \n     \n     \n─────\n```",
    "```\n  ┌─┐\n  │  \n  O  \n     \n     \n─────\n```",
    "```\n  ┌─┐\n  │  \n  O  \n  │  \n     \n─────\n```",
    "```\n  ┌─┐\n  │  \n  O  \n /│  \n     \n─────\n```",
    "```\n  ┌─┐\n  │  \n  O  \n /│\\ \n     \n─────\n```",
    "```\n  ┌─┐\n  │  \n  O  \n /│\\ \n /   \n─────\n```",
    "```\n  ┌─┐\n  │  \n  O  \n /│\\ \n / \\ \n─────\n```",
]

ALPHABET = list("abcdefghijklmnopqrstuvwxyz")


def _hangman_display(word: str, guessed: List[str]) -> str:
    return " ".join(c.upper() if c in guessed else "＿" for c in word)


def _hangman_keyboard(game_id: int, guessed: List[str]) -> InlineKeyboardMarkup:
    rows = []
    current_row: List[InlineKeyboardButton] = []
    for i, letter in enumerate(ALPHABET):
        if letter in guessed:
            btn = InlineKeyboardButton("·", callback_data="hangman:noop")
        else:
            btn = InlineKeyboardButton(letter.upper(), callback_data=f"hangman:{game_id}:{letter}")
        current_row.append(btn)
        if len(current_row) == 7 or i == len(ALPHABET) - 1:
            rows.append(current_row)
            current_row = []
    return InlineKeyboardMarkup(rows)


async def hangman_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hangman — Start a Hangman word game."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    word = random.choice(HANGMAN_WORDS)
    data: Dict[str, Any] = {
        "word": word,
        "guessed": [],
        "wrong": 0,
        "max_wrong": 6,
        "started_by": user.id,
    }

    async with get_session() as session:
        gs = GameSession(chat_id=chat.id, game_type="hangman", data=data, active=True)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)

    display = _hangman_display(word, [])
    text = (
        f"😵 {bold('Hangman!')}\n\n"
        f"{HANGMAN_ART[0]}\n\n"
        f"Word: {code(display)}\n"
        f"❌ Wrong guesses: 0/6\n\n"
        f"Guess a letter!"
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=_hangman_keyboard(gs.id, [])
    )


async def hangman_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Hangman letter guesses."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3 or parts[1] == "noop":
        return

    game_id = int(parts[1]) if parts[1].isdigit() else 0
    letter = parts[2].lower()

    async with get_session() as session:
        stmt = select(GameSession).where(GameSession.id == game_id, GameSession.active == True)
        gs = (await session.execute(stmt)).scalar_one_or_none()
        if not gs:
            await query.answer("❌ Game not found.", show_alert=True)
            return

        data = dict(gs.data)
        word: str = data["word"]
        guessed: List[str] = list(data["guessed"])
        wrong: int = data["wrong"]
        max_wrong: int = data["max_wrong"]

        if letter in guessed:
            await query.answer("⚠️ Already guessed!", show_alert=True)
            return

        guessed.append(letter)
        if letter not in word:
            wrong += 1

        data["guessed"] = guessed
        data["wrong"] = wrong
        gs.data = data

        display = _hangman_display(word, guessed)
        art = HANGMAN_ART[min(wrong, len(HANGMAN_ART) - 1)]

        if all(c in guessed for c in word):
            gs.active = False
            await session.commit()
            try:
                await query.edit_message_text(
                    f"🎉 {bold('You Won!')}\n\nWord: {bold(word.upper())}\n❌ Wrong: {wrong}/{max_wrong}",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
            return

        if wrong >= max_wrong:
            gs.active = False
            await session.commit()
            try:
                await query.edit_message_text(
                    f"💀 {bold('Game Over!')}\n\n{art}\n\nWord was: {bold(word.upper())}",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
            return

        await session.commit()

    wrong_letters = [l.upper() for l in guessed if l not in word]
    text = (
        f"😵 {bold('Hangman!')}\n\n"
        f"{art}\n\n"
        f"Word: {code(display)}\n"
        f"❌ Wrong: {wrong}/{max_wrong}"
        + (f" [{', '.join(wrong_letters)}]" if wrong_letters else "")
        + "\n\nGuess a letter!"
    )
    try:
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=_hangman_keyboard(game_id, guessed)
        )
    except TelegramError:
        pass


# ── Wordle ────────────────────────────────────────────────────────────────────

WORDLE_WORDS = [
    "crane", "slate", "stove", "brave", "grind", "flame", "plumb", "shout",
    "tiger", "ghost", "blind", "frost", "crisp", "glare", "mango", "query",
    "swift", "patch", "blend", "clash", "frame", "grasp", "hoist", "kneel",
    "light", "match", "nerve", "orbit", "prize", "quest", "raise", "stale",
    "tower", "vault", "whirl", "yacht", "zonal", "blaze", "choir", "dwarf",
]

WORDLE_MAX_TRIES = 6


def _wordle_evaluate(guess: str, word: str) -> str:
    result = []
    word_list = list(word)
    guess_list = list(guess)
    marks = ["⬛"] * 5

    # First pass: correct positions
    for i in range(5):
        if guess_list[i] == word_list[i]:
            marks[i] = "🟩"
            word_list[i] = None
            guess_list[i] = None

    # Second pass: wrong positions
    for i in range(5):
        if guess_list[i] and guess_list[i] in word_list:
            marks[i] = "🟨"
            word_list[word_list.index(guess_list[i])] = None

    return "".join(marks)


async def wordle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wordle — Daily Wordle-style game."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    word = random.choice(WORDLE_WORDS)
    data: Dict[str, Any] = {
        "word": word,
        "tries": [],
        "max_tries": WORDLE_MAX_TRIES,
        "player": user.id,
    }

    async with get_session() as session:
        gs = GameSession(chat_id=chat.id, game_type="wordle", data=data, active=True)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)

    text = (
        f"🟩 {bold('Wordle!')}\n\n"
        f"Guess the {bold('5-letter')} word in {bold('6')} tries!\n"
        f"🟩 = Right letter, right spot\n"
        f"🟨 = Right letter, wrong spot\n"
        f"⬛ = Letter not in word\n\n"
        f"Reply to this message with your guess!\n"
        f"Game ID: {code(str(gs.id))}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    # Listen for replies using context.user_data
    context.chat_data.setdefault("wordle_games", {})[gs.id] = data


async def wordle_guess_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Wordle guesses (via message handler)."""
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    text = (msg.text or "").strip().lower()
    if len(text) != 5 or not text.isalpha():
        return

    # Find active wordle games for this user
    async with get_session() as session:
        stmt = (
            select(GameSession)
            .where(
                GameSession.chat_id == chat.id,
                GameSession.game_type == "wordle",
                GameSession.active == True,
            )
            .order_by(GameSession.id.desc())
            .limit(1)
        )
        gs = (await session.execute(stmt)).scalar_one_or_none()
        if not gs:
            return
        if gs.data.get("player") != user.id:
            return

        data = dict(gs.data)
        word: str = data["word"]
        tries: List[dict] = data["tries"]
        max_tries: int = data["max_tries"]

        result_row = _wordle_evaluate(text, word)
        tries.append({"guess": text, "result": result_row})
        data["tries"] = tries
        gs.data = data

        board = "\n".join(
            f"{t['result']} {code(t['guess'].upper())}" for t in tries
        )
        remaining = max_tries - len(tries)

        if text == word:
            gs.active = False
            await session.commit()
            await msg.reply_text(
                f"🎉 {bold('You Won!')}\n\n{board}\n\n"
                f"Word: {bold(word.upper())} ✅\nTries: {len(tries)}/{max_tries}",
                parse_mode=ParseMode.HTML,
            )
            return

        if len(tries) >= max_tries:
            gs.active = False
            await session.commit()
            await msg.reply_text(
                f"💀 {bold('Game Over!')}\n\n{board}\n\n"
                f"Word was: {bold(word.upper())}",
                parse_mode=ParseMode.HTML,
            )
            return

        await session.commit()
        await msg.reply_text(
            f"🟩 {bold('Wordle')}\n\n{board}\n\n"
            f"Tries left: {bold(str(remaining))}",
            parse_mode=ParseMode.HTML,
        )


# ── Blackjack ─────────────────────────────────────────────────────────────────

SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
RANK_VALUES = {"A": 11, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
               "8": 8, "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10}


def _new_deck() -> List[str]:
    deck = [f"{r}{s}" for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def _card_value(card: str) -> int:
    rank = card[:-2] if card[-2:] in SUITS else card[:-1]
    return RANK_VALUES.get(rank, 10)


def _hand_value(hand: List[str]) -> int:
    total = sum(_card_value(c) for c in hand)
    aces = sum(1 for c in hand if c.startswith("A"))
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _bj_keyboard(game_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👊 Hit", callback_data=f"bj:{game_id}:hit"),
            InlineKeyboardButton("✋ Stand", callback_data=f"bj:{game_id}:stand"),
            InlineKeyboardButton("💰 Double", callback_data=f"bj:{game_id}:double"),
        ]
    ])


def _bj_text(player: List[str], dealer_visible: List[str], game_id: int, bet: int) -> str:
    pv = _hand_value(player)
    dv = _hand_value(dealer_visible)
    return (
        f"🃏 {bold('Blackjack!')}\n\n"
        f"🤖 {bold('Dealer:')} {' '.join(dealer_visible)} = {code(str(dv))}\n"
        f"👤 {bold('You:')} {' '.join(player)} = {code(str(pv))}\n"
        f"💰 Bet: {code(str(bet))} coins"
    )


async def blackjack_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/blackjack — Play Blackjack."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    bet = 50
    if context.args:
        try:
            bet = max(1, int(context.args[0]))
        except ValueError:
            pass

    deck = _new_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    data: Dict[str, Any] = {
        "deck": deck,
        "player": player,
        "dealer": dealer,
        "bet": bet,
        "player_id": user.id,
        "doubled": False,
    }

    async with get_session() as session:
        gs = GameSession(chat_id=chat.id, game_type="blackjack", data=data, active=True)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)

    dealer_visible = [dealer[0], "🂠"]
    text = _bj_text(player, dealer_visible, gs.id, bet)

    pv = _hand_value(player)
    if pv == 21:
        await update.effective_message.reply_text(
            text + f"\n\n🎉 {bold('Blackjack! You win!')}",
            parse_mode=ParseMode.HTML,
        )
        async with get_session() as session:
            gs2 = (await session.execute(select(GameSession).where(GameSession.id == gs.id))).scalar_one()
            gs2.active = False
            await session.commit()
        return

    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=_bj_keyboard(gs.id)
    )


async def blackjack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Blackjack actions."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    game_id = int(parts[1]) if parts[1].isdigit() else 0
    action = parts[2]

    async with get_session() as session:
        stmt = select(GameSession).where(GameSession.id == game_id, GameSession.active == True)
        gs = (await session.execute(stmt)).scalar_one_or_none()
        if not gs:
            await query.answer("❌ Game not found.", show_alert=True)
            return
        if gs.data.get("player_id") != user.id:
            await query.answer("❌ Not your game!", show_alert=True)
            return

        data = dict(gs.data)
        deck: List[str] = list(data["deck"])
        player: List[str] = list(data["player"])
        dealer: List[str] = list(data["dealer"])
        bet: int = data["bet"]

        if action == "hit" or action == "double":
            if action == "double":
                bet *= 2
                data["bet"] = bet
                data["doubled"] = True
            player.append(deck.pop())
            data["player"] = player
            data["deck"] = deck
            gs.data = data
            pv = _hand_value(player)

            if pv > 21 or action == "double":
                # Bust or double-down forces stand
                result, outcome = await _bj_resolve(data, dealer, deck, bet, bust=(pv > 21))
                gs.active = False
                gs.data = {**data, "player": player, "dealer": dealer, "deck": deck}
                await session.commit()
                try:
                    await query.edit_message_text(result, parse_mode=ParseMode.HTML)
                except TelegramError:
                    pass
                return

            await session.commit()
            dealer_visible = [dealer[0], "🂠"]
            text = _bj_text(player, dealer_visible, game_id, bet)
            try:
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=_bj_keyboard(game_id))
            except TelegramError:
                pass

        elif action == "stand":
            # Dealer draws
            while _hand_value(dealer) < 17:
                dealer.append(deck.pop())
            result, _ = await _bj_resolve(data, dealer, deck, bet, bust=False)
            gs.active = False
            gs.data = {**data, "dealer": dealer}
            await session.commit()
            try:
                await query.edit_message_text(result, parse_mode=ParseMode.HTML)
            except TelegramError:
                pass


async def _bj_resolve(data: dict, dealer: List[str], deck: List[str], bet: int, bust: bool) -> tuple:
    player: List[str] = data["player"]
    pv = _hand_value(player)
    dv = _hand_value(dealer)

    player_str = " ".join(player)
    dealer_str = " ".join(dealer)

    result_text = (
        f"🃏 {bold('Blackjack — Result!')}\n\n"
        f"🤖 {bold('Dealer:')} {dealer_str} = {code(str(dv))}\n"
        f"👤 {bold('You:')} {player_str} = {code(str(pv))}\n"
        f"💰 Bet: {code(str(bet))} coins\n\n"
    )

    if bust or (pv > 21):
        result_text += f"💥 {bold('Bust! You lose!')} -{bet} coins"
        return result_text, "lose"
    elif dv > 21 or pv > dv:
        result_text += f"🎉 {bold('You Win!')} +{bet} coins"
        return result_text, "win"
    elif pv == dv:
        result_text += f"🤝 {bold('Push! (Draw)')} Bet returned."
        return result_text, "draw"
    else:
        result_text += f"😞 {bold('Dealer Wins!')} -{bet} coins"
        return result_text, "lose"


# ── Math Challenge ────────────────────────────────────────────────────────────

async def mathchallenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mathchallenge — Quick math challenge."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    a = random.randint(1, 50)
    b = random.randint(1, 50)
    op = random.choice(["+", "-", "*"])
    if op == "+":
        answer = a + b
    elif op == "-":
        answer = a - b
    else:
        answer = a * b

    data = {"question": f"{a} {op} {b}", "answer": answer, "player": user.id, "deadline": datetime.now(timezone.utc).timestamp() + 20}

    async with get_session() as session:
        gs = GameSession(chat_id=chat.id, game_type="math", data=data, active=True)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)

    sent = await update.effective_message.reply_text(
        f"🧮 {bold('Math Challenge!')}\n\n"
        f"What is {bold(f'{a} {op} {b}')}?\n\n"
        f"{italic('Reply with the answer within 20 seconds!')}",
        parse_mode=ParseMode.HTML,
    )
    context.chat_data.setdefault("math_games", {})[gs.id] = {"msg_id": sent.message_id}

    await asyncio.sleep(20)
    async with get_session() as session:
        stmt = select(GameSession).where(GameSession.id == gs.id, GameSession.active == True)
        active_gs = (await session.execute(stmt)).scalar_one_or_none()
        if active_gs:
            active_gs.active = False
            await session.commit()
            try:
                await sent.edit_text(
                    f"⏰ {bold('Time\'s up!')}\nAnswer was: {bold(str(answer))}",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass


# ── Riddle ────────────────────────────────────────────────────────────────────

RIDDLES = [
    ("I speak without a mouth and hear without ears. I have no body, but I come alive with wind. What am I?", "An echo"),
    ("The more you take, the more you leave behind. What am I?", "Footsteps"),
    ("I have cities but no houses. Mountains but no trees. Water but no fish. What am I?", "A map"),
    ("What has hands but can't clap?", "A clock"),
    ("I get wetter the more I dry. What am I?", "A towel"),
    ("I have a head and a tail but no body. What am I?", "A coin"),
    ("What has keys but no locks, space but no room, and you can enter but can't go inside?", "A keyboard"),
    ("The more you have of me, the less you see. What am I?", "Darkness"),
]


async def riddle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/riddle — Random riddle with reveal button."""
    user = update.effective_user
    uid = user.id if user else 0
    question, answer = random.choice(RIDDLES)
    # Truncate answer to keep callback_data within 64 bytes
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("(?) Reveal Answer", callback_data=f"riddle:reveal:{escape_html(answer)[:38]}:{uid}")
    ]])
    await update.effective_message.reply_text(
        f"{bold('Riddle!')}  (^_^)\n\n{escape_html(question)}",
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def riddle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    # ── Owner check ──────────────────────────────────────────────────────────
    try:
        owner_id = int((query.data or "").rsplit(":", 1)[-1])
        if owner_id != 0 and user.id != owner_id:
            await query.answer("(x_x)  This is not your riddle!", show_alert=True)
            return
    except (ValueError, IndexError):
        pass

    await query.answer()
    # riddle:reveal:ANSWER:uid — strip uid from the end
    raw = query.data or ""
    parts = raw.split(":", 2)
    if len(parts) < 3:
        return
    payload = parts[2]
    answer = payload.rsplit(":", 1)[0] if ":" in payload else payload
    try:
        original = query.message.text or ""
        await query.edit_message_text(
            original + f"\n\n(!)  {bold('Answer:')} {escape_html(answer)}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


# ── RPS Tournament ────────────────────────────────────────────────────────────

async def rps_challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rps_challenge @user — Challenge user to RPS."""
    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    if not user or not msg or not chat:
        return

    target_id: Optional[int] = None
    target_name = "Anyone"

    if msg.reply_to_message and msg.reply_to_message.from_user:
        t = msg.reply_to_message.from_user
        target_id = t.id
        target_name = escape_html(t.first_name)
    elif context.args:
        arg = context.args[0].lstrip("@")
        try:
            tg = await context.bot.get_chat(f"@{arg}")
            target_id = tg.id
            target_name = escape_html(tg.first_name or arg)
        except TelegramError:
            pass

    data = {
        "challenger": user.id,
        "challenger_name": escape_html(user.first_name),
        "opponent": target_id,
        "opponent_name": target_name,
        "challenger_choice": None,
        "opponent_choice": None,
    }

    async with get_session() as session:
        gs = GameSession(chat_id=chat.id, game_type="rps_challenge", data=data, active=True)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✊ Rock", callback_data=f"rpsc:{gs.id}:rock"),
        InlineKeyboardButton("✋ Paper", callback_data=f"rpsc:{gs.id}:paper"),
        InlineKeyboardButton("✌️ Scissors", callback_data=f"rpsc:{gs.id}:scissors"),
    ]])

    await msg.reply_text(
        f"✊✋✌️ {bold('RPS Challenge!')}\n\n"
        f"{user_mention(user.id, user.first_name)} challenges {bold(target_name)}!\n\n"
        f"Both players pick your move!",
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def rpsc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle RPS challenge moves."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    game_id = int(parts[1]) if parts[1].isdigit() else 0
    choice = parts[2]

    wins_against = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
    emojis = {"rock": "✊", "paper": "✋", "scissors": "✌️"}

    async with get_session() as session:
        stmt = select(GameSession).where(GameSession.id == game_id, GameSession.active == True)
        gs = (await session.execute(stmt)).scalar_one_or_none()
        if not gs:
            await query.answer("❌ Game ended.", show_alert=True)
            return

        data = dict(gs.data)
        challenger: int = data["challenger"]
        opponent: Optional[int] = data["opponent"]

        if opponent and user.id not in (challenger, opponent):
            await query.answer("❌ You're not in this game!", show_alert=True)
            return

        if user.id == challenger:
            if data["challenger_choice"]:
                await query.answer("✋ Already picked!", show_alert=True)
                return
            data["challenger_choice"] = choice
        else:
            if data["opponent_choice"]:
                await query.answer("✋ Already picked!", show_alert=True)
                return
            data["opponent_choice"] = choice
            if not opponent:
                data["opponent"] = user.id
                data["opponent_name"] = escape_html(user.first_name)

        gs.data = data

        c_choice = data.get("challenger_choice")
        o_choice = data.get("opponent_choice")

        if c_choice and o_choice:
            gs.active = False
            await session.commit()
            c_name = data["challenger_name"]
            o_name = data["opponent_name"]
            result: str
            if c_choice == o_choice:
                result = f"🤝 {bold('Draw!')} Both chose {emojis[c_choice]}!"
            elif wins_against[c_choice] == o_choice:
                result = f"🎉 {bold(c_name)} wins! {emojis[c_choice]} beats {emojis[o_choice]}!"
            else:
                result = f"🎉 {bold(o_name)} wins! {emojis[o_choice]} beats {emojis[c_choice]}!"

            try:
                await query.edit_message_text(
                    f"✊✋✌️ {bold('RPS Result!')}\n\n"
                    f"{bold(c_name)}: {emojis[c_choice]}\n"
                    f"{bold(o_name)}: {emojis[o_choice]}\n\n"
                    f"{result}",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
        else:
            await session.commit()
            picked = [data["challenger_name"]] if c_choice else []
            if o_choice:
                picked.append(data["opponent_name"])
            await query.answer(f"✅ Picked! Waiting for the other player...", show_alert=True)


# ── Quiz creator ──────────────────────────────────────────────────────────────

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quiz <question> | <A> | <B> | <C> | <D> | <correct(0-3)> — Create quiz."""
    text = " ".join(context.args) if context.args else ""
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 6:
        await update.effective_message.reply_text(
            f"❌ Usage: {code('/quiz Question | A | B | C | D | correct_index(0-3)')}",
            parse_mode=ParseMode.HTML,
        )
        return
    question = parts[0]
    options = parts[1:5]
    try:
        correct = int(parts[5])
        if correct not in range(4):
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("❌ Correct answer must be 0, 1, 2, or 3.")
        return

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    data: Dict[str, Any] = {
        "question": question,
        "options": options,
        "answer": correct,
        "created_by": user.id,
        "answered": {},
    }

    async with get_session() as session:
        gs = GameSession(chat_id=chat.id, game_type="quiz", data=data, active=True)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{OPTION_EMOJIS[i]} {escape_html(opt)[:30]}", callback_data=f"quiz:{gs.id}:{i}")]
        for i, opt in enumerate(options)
    ])
    options_text = "\n".join(f"{OPTION_EMOJIS[i]} {escape_html(opt)}" for i, opt in enumerate(options))
    await update.effective_message.reply_text(
        f"📋 {bold('Custom Quiz!')}\n\n{bold(escape_html(question))}\n\n{options_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quiz answers."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    game_id = int(parts[1]) if parts[1].isdigit() else 0
    choice = int(parts[2]) if parts[2].isdigit() else -1

    async with get_session() as session:
        stmt = select(GameSession).where(GameSession.id == game_id, GameSession.active == True)
        gs = (await session.execute(stmt)).scalar_one_or_none()
        if not gs:
            await query.answer("❌ Quiz ended.", show_alert=True)
            return

        data = dict(gs.data)
        answered: dict = data.get("answered", {})
        if str(user.id) in answered:
            await query.answer("✋ Already answered!", show_alert=True)
            return

        correct = data.get("answer", -1)
        answered[str(user.id)] = choice
        data["answered"] = answered
        gs.data = data
        await session.commit()

        if choice == correct:
            await query.answer("✅ Correct!", show_alert=True)
        else:
            correct_opt = escape_html(data["options"][correct])
            await query.answer(f"❌ Wrong! Correct: {correct_opt}", show_alert=True)


# ── /triviaboard ──────────────────────────────────────────────────────────────

async def triviaboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/triviaboard — Show trivia scores (via XP)."""
    chat = update.effective_chat
    if not chat:
        return

    async with get_session() as session:
        stmt = select(User).order_by(User.xp.desc()).limit(10)
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        await update.effective_message.reply_text(
            f"🏆 {bold('Trivia Leaderboard')}\n\n{italic('No data yet.')}",
            parse_mode=ParseMode.HTML,
        )
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"🏆 {bold('Trivia Leaderboard')}\n"]
    for i, u in enumerate(rows):
        name = escape_html(u.first_name or str(u.user_id))
        lines.append(f"{medals[i]} {bold(name)}: {code(str(u.xp or 0))} XP")

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML
    )


# ── /game_stats ───────────────────────────────────────────────────────────────

async def game_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/game_stats — Personal game statistics."""
    user = update.effective_user
    if not user:
        return

    async with get_session() as session:
        stmt = select(User).where(User.user_id == user.id)
        db_user = (await session.execute(stmt)).scalar_one_or_none()

    if not db_user:
        await update.effective_message.reply_text(
            f"📊 {bold('Game Stats')}\n\n{italic('No data found.')}",
            parse_mode=ParseMode.HTML,
        )
        return

    xp = db_user.xp or 0
    level = max(1, xp // 100)

    text = (
        f"📊 {bold(f'{escape_html(user.first_name)}\'s Game Stats')}\n\n"
        f"⭐ {bold('XP:')} {code(str(xp))}\n"
        f"🏆 {bold('Level:')} {code(str(level))}\n"
        f"👑 {bold('Reputation:')} {code(str(db_user.reputation or 0))}\n"
        f"\n{progress_bar(xp % 100, 100, length=10)} {xp % 100}/100 XP to next level"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("tictactoe", tictactoe_command))
    app.add_handler(CommandHandler("ttt", tictactoe_command))
    app.add_handler(CommandHandler("trivia", trivia_command))
    app.add_handler(CommandHandler("hangman", hangman_command))
    app.add_handler(CommandHandler("wordle", wordle_command))
    app.add_handler(CommandHandler("mathchallenge", mathchallenge_command))
    app.add_handler(CommandHandler("riddle", riddle_command))
    app.add_handler(CommandHandler("blackjack", blackjack_command))
    app.add_handler(CommandHandler("bj", blackjack_command))
    app.add_handler(CommandHandler("rps_challenge", rps_challenge_command))
    app.add_handler(CommandHandler("quiz", quiz_command))
    app.add_handler(CommandHandler("triviaboard", triviaboard_command))
    app.add_handler(CommandHandler("game_stats", game_stats_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(ttt_callback, pattern=r"^ttt:"))
    app.add_handler(CallbackQueryHandler(trivia_callback, pattern=r"^trivia:"))
    app.add_handler(CallbackQueryHandler(hangman_callback, pattern=r"^hangman:"))
    app.add_handler(CallbackQueryHandler(riddle_callback, pattern=r"^riddle:"))
    app.add_handler(CallbackQueryHandler(blackjack_callback, pattern=r"^bj:"))
    app.add_handler(CallbackQueryHandler(rpsc_callback, pattern=r"^rpsc:"))
    app.add_handler(CallbackQueryHandler(quiz_callback, pattern=r"^quiz:"))
