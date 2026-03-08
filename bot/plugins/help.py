"""
bot/plugins/help.py
───────────────────
Multi-level help system with category browsing, pagination and keyword search.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.helpers.formatters import bold, code, escape_html, italic, paginate_items

logger = logging.getLogger(__name__)

# ── Help data ─────────────────────────────────────────────────────────────────

# Each entry: {"name", "syntax", "description", "examples", "aliases", "permission"}
HELP_DATA: Dict[str, Dict] = {
    "Admin": {
        "emoji": "(*)",
        "description": "Group administration and management commands.",
        "commands": [
            {
                "name": "promote",
                "syntax": "/promote &lt;user&gt; [title]",
                "description": "Promote a user to admin with an optional custom title.",
                "examples": ["/promote @user", "/promote @user Moderator"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "demote",
                "syntax": "/demote &lt;user&gt;",
                "description": "Remove a user's admin rights.",
                "examples": ["/demote @user"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "title",
                "syntax": "/title &lt;user&gt; &lt;title&gt;",
                "description": "Set a custom admin title for a user.",
                "examples": ["/title @user Chief"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "pin",
                "syntax": "/pin [loud]",
                "description": "Pin the replied message. Use 'loud' to notify members.",
                "examples": ["/pin", "/pin loud"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "unpin",
                "syntax": "/unpin",
                "description": "Unpin the replied message.",
                "examples": ["/unpin"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "unpinall",
                "syntax": "/unpinall",
                "description": "Unpin all pinned messages (requires confirmation).",
                "examples": ["/unpinall"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "invite",
                "syntax": "/invite",
                "description": "Get the group's invite link.",
                "examples": ["/invite"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "admins",
                "syntax": "/admins",
                "description": "List all admins in the group.",
                "examples": ["/admins"],
                "aliases": ["adminlist"],
                "permission": "Member",
            },
            {
                "name": "id",
                "syntax": "/id [user]",
                "description": "Get the Telegram ID of a user, chat, or message.",
                "examples": ["/id", "/id @user"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "info",
                "syntax": "/info [user]",
                "description": "Show detailed information about a user.",
                "examples": ["/info", "/info @user"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "chatinfo",
                "syntax": "/chatinfo",
                "description": "Show information about the current chat.",
                "examples": ["/chatinfo"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "members",
                "syntax": "/members",
                "description": "Show the number of members in this group.",
                "examples": ["/members"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "bots",
                "syntax": "/bots",
                "description": "List all bots in the group.",
                "examples": ["/bots"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "stats",
                "syntax": "/stats",
                "description": "Show activity statistics for this chat.",
                "examples": ["/stats"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "Moderation": {
        "emoji": "[=]",
        "description": "Commands to moderate and manage group members.",
        "commands": [
            {
                "name": "ban",
                "syntax": "/ban &lt;user&gt; [reason]",
                "description": "Ban a user from the group.",
                "examples": ["/ban @user", "/ban @user spamming"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "unban",
                "syntax": "/unban &lt;user&gt;",
                "description": "Unban a previously banned user.",
                "examples": ["/unban @user"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "tban",
                "syntax": "/tban &lt;user&gt; &lt;time&gt; [reason]",
                "description": "Temporarily ban a user. Time format: 1m, 2h, 3d, 1w.",
                "examples": ["/tban @user 1h spam", "/tban @user 30m"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "kick",
                "syntax": "/kick &lt;user&gt; [reason]",
                "description": "Kick a user from the group (they can rejoin).",
                "examples": ["/kick @user", "/kick @user Behaviour"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "mute",
                "syntax": "/mute &lt;user&gt; [reason]",
                "description": "Permanently mute a user in the group.",
                "examples": ["/mute @user", "/mute @user spamming"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "unmute",
                "syntax": "/unmute &lt;user&gt;",
                "description": "Unmute a muted user.",
                "examples": ["/unmute @user"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "tmute",
                "syntax": "/tmute &lt;user&gt; &lt;time&gt; [reason]",
                "description": "Temporarily mute a user.",
                "examples": ["/tmute @user 2h", "/tmute @user 30m spamming"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "warn",
                "syntax": "/warn &lt;user&gt; [reason]",
                "description": "Warn a user. Auto-action triggered at warn limit.",
                "examples": ["/warn @user", "/warn @user flood"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "warns",
                "syntax": "/warns &lt;user&gt;",
                "description": "Check the warnings of a user.",
                "examples": ["/warns @user"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "purge",
                "syntax": "/purge [n]",
                "description": "Delete messages from the replied message to now, or last N messages.",
                "examples": ["/purge", "/purge 20"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "del",
                "syntax": "/del",
                "description": "Delete the replied message.",
                "examples": ["/del"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "kickme",
                "syntax": "/kickme",
                "description": "Kick yourself from the group.",
                "examples": ["/kickme"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "zombies",
                "syntax": "/zombies",
                "description": "List deleted accounts in the group.",
                "examples": ["/zombies"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "kickzombies",
                "syntax": "/kickzombies",
                "description": "Remove all deleted accounts from the group.",
                "examples": ["/kickzombies"],
                "aliases": [],
                "permission": "Admin",
            },
        ],
    },
    "Welcome": {
        "emoji": "👋",
        "description": "Welcome, goodbye, rules and captcha settings.",
        "commands": [
            {
                "name": "welcome",
                "syntax": "/welcome",
                "description": "Show the current welcome message.",
                "examples": ["/welcome"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "setwelcome",
                "syntax": "/setwelcome &lt;text&gt;",
                "description": "Set welcome message. Variables: {mention}, {first}, {last}, {username}, {count}, {chat}.",
                "examples": ["/setwelcome Welcome {mention} to {chat}!"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "captcha",
                "syntax": "/captcha &lt;on|off&gt;",
                "description": "Enable or disable captcha for new members.",
                "examples": ["/captcha on", "/captcha off"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "rules",
                "syntax": "/rules",
                "description": "Show the group rules.",
                "examples": ["/rules"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "setrules",
                "syntax": "/setrules &lt;text&gt;",
                "description": "Set the group rules.",
                "examples": ["/setrules Rule 1: Be kind."],
                "aliases": [],
                "permission": "Admin",
            },
        ],
    },
    "Economy": {
        "emoji": "($)",
        "description": "Virtual economy — coins, bank, shop and rewards.",
        "commands": [
            {
                "name": "balance",
                "syntax": "/balance",
                "description": "Check your coin balance and bank.",
                "examples": ["/balance"],
                "aliases": ["bal", "wallet"],
                "permission": "Member",
            },
            {
                "name": "daily",
                "syntax": "/daily",
                "description": "Claim your daily coin reward.",
                "examples": ["/daily"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "weekly",
                "syntax": "/weekly",
                "description": "Claim your weekly coin reward.",
                "examples": ["/weekly"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "work",
                "syntax": "/work",
                "description": "Earn coins by working (1 hour cooldown).",
                "examples": ["/work"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "transfer",
                "syntax": "/transfer &lt;user&gt; &lt;amount&gt;",
                "description": "Transfer coins to another user.",
                "examples": ["/transfer @user 500"],
                "aliases": ["give", "pay"],
                "permission": "Member",
            },
        ],
    },
    "Fun": {
        "emoji": "(*o*)",
        "description": "Fun commands and random generators.",
        "commands": [
            {
                "name": "roll",
                "syntax": "/roll [dice]",
                "description": "Roll a dice. Default: d6. E.g. 2d20.",
                "examples": ["/roll", "/roll 2d6"],
                "aliases": ["dice"],
                "permission": "Member",
            },
            {
                "name": "flip",
                "syntax": "/flip",
                "description": "Flip a coin.",
                "examples": ["/flip"],
                "aliases": ["coinflip"],
                "permission": "Member",
            },
            {
                "name": "choose",
                "syntax": "/choose &lt;option1&gt; | &lt;option2&gt; ...",
                "description": "Randomly choose one of the given options.",
                "examples": ["/choose pizza | pasta | sushi"],
                "aliases": ["pick"],
                "permission": "Member",
            },
            {
                "name": "quote",
                "syntax": "/quote",
                "description": "Get a random inspirational quote.",
                "examples": ["/quote"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "joke",
                "syntax": "/joke",
                "description": "Get a random joke.",
                "examples": ["/joke"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "Games": {
        "emoji": "(^_^)",
        "description": "In-group games to play with other members.",
        "commands": [
            {
                "name": "trivia",
                "syntax": "/trivia",
                "description": "Start a trivia question. First correct answer wins points.",
                "examples": ["/trivia"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "hangman",
                "syntax": "/hangman",
                "description": "Start a game of Hangman.",
                "examples": ["/hangman"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "wordchain",
                "syntax": "/wordchain",
                "description": "Start a word chain game.",
                "examples": ["/wordchain"],
                "aliases": ["wc"],
                "permission": "Member",
            },
            {
                "name": "ttt",
                "syntax": "/ttt &lt;user&gt;",
                "description": "Challenge someone to Tic-Tac-Toe.",
                "examples": ["/ttt @user"],
                "aliases": ["tictactoe"],
                "permission": "Member",
            },
            {
                "name": "rps",
                "syntax": "/rps &lt;user&gt;",
                "description": "Challenge someone to Rock-Paper-Scissors.",
                "examples": ["/rps @user"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "Social": {
        "emoji": "(>_<)",
        "description": "Social interaction commands — rep, profile, etc.",
        "commands": [
            {
                "name": "profile",
                "syntax": "/profile [user]",
                "description": "View your or someone else's profile card.",
                "examples": ["/profile", "/profile @user"],
                "aliases": ["me"],
                "permission": "Member",
            },
            {
                "name": "rep",
                "syntax": "/rep &lt;user&gt;",
                "description": "Give reputation to a user (24h cooldown).",
                "examples": ["/rep @user"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "leaderboard",
                "syntax": "/leaderboard",
                "description": "Show the top members by activity.",
                "examples": ["/leaderboard"],
                "aliases": ["top", "lb"],
                "permission": "Member",
            },
            {
                "name": "afk",
                "syntax": "/afk [reason]",
                "description": "Mark yourself as AFK. Others will be notified when they tag you.",
                "examples": ["/afk", "/afk sleeping"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "AI": {
        "emoji": "(o_o)",
        "description": "AI-powered features using OpenAI.",
        "commands": [
            {
                "name": "ask",
                "syntax": "/ask &lt;question&gt;",
                "description": "Ask the AI a question.",
                "examples": ["/ask What is the speed of light?"],
                "aliases": ["ai", "gpt"],
                "permission": "Member",
            },
            {
                "name": "imagine",
                "syntax": "/imagine &lt;prompt&gt;",
                "description": "Generate an image from a text description using DALL-E.",
                "examples": ["/imagine a sunset over mountains"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "translate",
                "syntax": "/translate &lt;lang&gt; &lt;text&gt;",
                "description": "Translate text to another language.",
                "examples": ["/translate es Hello World"],
                "aliases": ["tr"],
                "permission": "Member",
            },
            {
                "name": "summarize",
                "syntax": "/summarize [reply to message]",
                "description": "Summarize the replied message or long text.",
                "examples": ["/summarize"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "Media": {
        "emoji": "[~]",
        "description": "Media processing and generation commands.",
        "commands": [
            {
                "name": "toimg",
                "syntax": "/toimg [reply to sticker]",
                "description": "Convert a sticker to a PNG image.",
                "examples": ["/toimg"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "tosticker",
                "syntax": "/tosticker [reply to image]",
                "description": "Convert an image to a sticker.",
                "examples": ["/tosticker"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "qr",
                "syntax": "/qr &lt;text&gt;",
                "description": "Generate a QR code from text or URL.",
                "examples": ["/qr https://telegram.org"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "ocr",
                "syntax": "/ocr [reply to image]",
                "description": "Extract text from an image using OCR.",
                "examples": ["/ocr"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "Info": {
        "emoji": "(i)",
        "description": "Information and lookup commands.",
        "commands": [
            {
                "name": "id",
                "syntax": "/id [user]",
                "description": "Get the Telegram ID of yourself, a user, or the chat.",
                "examples": ["/id", "/id @user"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "info",
                "syntax": "/info [user]",
                "description": "Get detailed information about a user.",
                "examples": ["/info", "/info @user", "/info 123456789"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "chatinfo",
                "syntax": "/chatinfo",
                "description": "Get information about the current group.",
                "examples": ["/chatinfo"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "ping",
                "syntax": "/ping",
                "description": "Check the bot's response time in milliseconds.",
                "examples": ["/ping"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "alive",
                "syntax": "/alive",
                "description": "Check if the bot is online and view uptime.",
                "examples": ["/alive"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "Utilities": {
        "emoji": "[+]",
        "description": "Useful utility commands.",
        "commands": [
            {
                "name": "calc",
                "syntax": "/calc &lt;expression&gt;",
                "description": "Calculate a mathematical expression.",
                "examples": ["/calc 2 + 2 * 10"],
                "aliases": ["math"],
                "permission": "Member",
            },
            {
                "name": "time",
                "syntax": "/time [timezone]",
                "description": "Get the current time in a timezone.",
                "examples": ["/time", "/time UTC", "/time US/Eastern"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "weather",
                "syntax": "/weather &lt;city&gt;",
                "description": "Get the current weather for a city.",
                "examples": ["/weather London"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "shorten",
                "syntax": "/shorten &lt;url&gt;",
                "description": "Shorten a URL.",
                "examples": ["/shorten https://example.com/very/long/path"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "remind",
                "syntax": "/remind &lt;time&gt; &lt;message&gt;",
                "description": "Set a personal reminder.",
                "examples": ["/remind 30m Check the oven", "/remind 1h Meeting"],
                "aliases": ["reminder"],
                "permission": "Member",
            },
        ],
    },
    "Stickers": {
        "emoji": "(*)",
        "description": "Sticker pack creation and management.",
        "commands": [
            {
                "name": "stickerpack",
                "syntax": "/stickerpack",
                "description": "Create or manage your personal sticker pack.",
                "examples": ["/stickerpack"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "addsticker",
                "syntax": "/addsticker [reply to image/sticker]",
                "description": "Add a sticker to your personal pack.",
                "examples": ["/addsticker"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "removesticker",
                "syntax": "/removesticker [reply to sticker]",
                "description": "Remove a sticker from your pack.",
                "examples": ["/removesticker"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "kang",
                "syntax": "/kang [reply to sticker]",
                "description": "Add any sticker to your personal pack.",
                "examples": ["/kang"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "Notes": {
        "emoji": "[n]",
        "description": "Save and retrieve notes by keyword.",
        "commands": [
            {
                "name": "save",
                "syntax": "/save &lt;name&gt; [reply or text]",
                "description": "Save a note. Reply to a message to save its content.",
                "examples": ["/save rules Follow the rules!", "/save link (reply to message)"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "get",
                "syntax": "/get &lt;name&gt; or #name",
                "description": "Retrieve a saved note.",
                "examples": ["/get rules", "#rules"],
                "aliases": [],
                "permission": "Member",
            },
            {
                "name": "notes",
                "syntax": "/notes",
                "description": "List all saved notes in this group.",
                "examples": ["/notes"],
                "aliases": ["saved"],
                "permission": "Member",
            },
            {
                "name": "clear",
                "syntax": "/clear &lt;name&gt;",
                "description": "Delete a saved note.",
                "examples": ["/clear rules"],
                "aliases": ["delnote"],
                "permission": "Admin",
            },
            {
                "name": "privatenotes",
                "syntax": "/privatenotes &lt;on|off&gt;",
                "description": "Send notes in PM instead of the group.",
                "examples": ["/privatenotes on"],
                "aliases": [],
                "permission": "Admin",
            },
        ],
    },
    "Federation": {
        "emoji": "(~)",
        "description": "Federation — share ban lists across multiple groups.",
        "commands": [
            {
                "name": "newfed",
                "syntax": "/newfed &lt;name&gt;",
                "description": "Create a new federation.",
                "examples": ["/newfed My Network"],
                "aliases": [],
                "permission": "Owner",
            },
            {
                "name": "joinfed",
                "syntax": "/joinfed &lt;fed_id&gt;",
                "description": "Join a federation.",
                "examples": ["/joinfed abc123"],
                "aliases": [],
                "permission": "Admin",
            },
            {
                "name": "fban",
                "syntax": "/fban &lt;user&gt; [reason]",
                "description": "Ban a user from all groups in your federation.",
                "examples": ["/fban @user spamming"],
                "aliases": [],
                "permission": "Fed Admin",
            },
            {
                "name": "funban",
                "syntax": "/funban &lt;user&gt;",
                "description": "Remove a user's federation ban.",
                "examples": ["/funban @user"],
                "aliases": [],
                "permission": "Fed Admin",
            },
            {
                "name": "fedinfo",
                "syntax": "/fedinfo [fed_id]",
                "description": "Show information about your or a specified federation.",
                "examples": ["/fedinfo"],
                "aliases": [],
                "permission": "Member",
            },
        ],
    },
    "Owner": {
        "emoji": "[o]",
        "description": "Bot owner and sudo-only commands.",
        "commands": [
            {
                "name": "broadcast",
                "syntax": "/broadcast &lt;message&gt;",
                "description": "Send a message to all groups the bot is in.",
                "examples": ["/broadcast Bot maintenance at midnight."],
                "aliases": [],
                "permission": "Owner",
            },
            {
                "name": "gban",
                "syntax": "/gban &lt;user&gt; [reason]",
                "description": "Globally ban a user from all groups.",
                "examples": ["/gban @user spam"],
                "aliases": [],
                "permission": "Sudo",
            },
            {
                "name": "ungban",
                "syntax": "/ungban &lt;user&gt;",
                "description": "Remove a global ban.",
                "examples": ["/ungban @user"],
                "aliases": [],
                "permission": "Sudo",
            },
            {
                "name": "maintenance",
                "syntax": "/maintenance &lt;on|off&gt;",
                "description": "Toggle maintenance mode.",
                "examples": ["/maintenance on"],
                "aliases": [],
                "permission": "Owner",
            },
            {
                "name": "stats",
                "syntax": "/stats",
                "description": "Global bot statistics (groups, users, messages).",
                "examples": ["/stats"],
                "aliases": [],
                "permission": "Sudo",
            },
        ],
    },
}

ITEMS_PER_PAGE = 10

# ── User-gating helper ────────────────────────────────────────────────────────

def _owner_check(query_uid: int, callback_data: str) -> bool:
    """Return True if the last segment of callback_data matches query_uid."""
    try:
        return int(callback_data.rsplit(":", 1)[-1]) == query_uid
    except (ValueError, IndexError):
        return True  # legacy data without uid — allow


# ── Keyboard builders ─────────────────────────────────────────────────────────

def _category_keyboard(uid: int = 0) -> InlineKeyboardMarkup:
    """Build the main help menu with all category buttons."""
    u = str(uid)
    categories = list(HELP_DATA.items())
    rows: List[List[InlineKeyboardButton]] = []

    row: List[InlineKeyboardButton] = []
    for name, data in categories:
        btn = InlineKeyboardButton(
            f"{data['emoji']} {name}",
            callback_data=f"help:cat:{name}:0:{u}",
        )
        row.append(btn)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("(?) Search Commands", callback_data=f"help:search:{u}")])
    rows.append([InlineKeyboardButton("(x) Close", callback_data=f"help:close:{u}")])
    return InlineKeyboardMarkup(rows)


def _commands_keyboard(category: str, page: int, total_pages: int, uid: int = 0) -> InlineKeyboardMarkup:
    """Build navigation keyboard for a category's command list."""
    u = str(uid)
    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◄", callback_data=f"help:cat:{category}:{page - 1}:{u}"))
    nav.append(InlineKeyboardButton(f"[ {page + 1}/{total_pages} ]", callback_data=f"help:noop:{u}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("►", callback_data=f"help:cat:{category}:{page + 1}:{u}"))

    return InlineKeyboardMarkup(
        [
            nav,
            [
                InlineKeyboardButton("◄ Categories", callback_data=f"help:menu:{u}"),
                InlineKeyboardButton("(?) Search", callback_data=f"help:search:{u}"),
            ],
        ]
    )


def _command_detail_keyboard(category: str, uid: int = 0) -> InlineKeyboardMarkup:
    u = str(uid)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"◄ Back to {category}",
                    callback_data=f"help:cat:{category}:0:{u}",
                )
            ]
        ]
    )


# ── Text builders ─────────────────────────────────────────────────────────────

def _build_category_list_text(category: str, page: int) -> Tuple[str, int]:
    """Return (formatted text, total_pages) for a category page."""
    data = HELP_DATA[category]
    commands = data["commands"]
    page_cmds, total_pages = paginate_items(commands, page, ITEMS_PER_PAGE)

    lines = [
        f"{data['emoji']} {bold(category)} — {italic(data['description'])}",
        "",
        f"{bold('Commands')} (page {page + 1}/{total_pages}):",
        "",
    ]
    for cmd in page_cmds:
        aliases = f" | {', '.join(code('/' + a) for a in cmd['aliases'])}" if cmd["aliases"] else ""
        lines.append(f"◆ {code('/' + cmd['name'])}{aliases}")
        lines.append(f"  └ {cmd['description']}")
        lines.append("")

    lines.append(italic("Tap a command name or use /help &lt;command&gt; for details."))
    return "\n".join(lines), total_pages


def _build_command_detail_text(cmd: dict) -> str:
    """Return full detail text for a single command."""
    lines = [
        f"{bold(cmd['name'].upper())}  (i)",
        "",
        f"{bold('Description:')}\n{cmd['description']}",
        "",
        f"{bold('Syntax:')}\n{code(cmd['syntax'])}",
    ]

    if cmd.get("aliases"):
        lines += ["", f"{bold('Aliases:')} {', '.join(code('/' + a) for a in cmd['aliases'])}"]

    if cmd.get("examples"):
        lines += ["", f"{bold('Examples:')}"]
        for ex in cmd["examples"]:
            lines.append(f"  ◆ {code(ex)}")

    lines += ["", f"{bold('Permission:')} {cmd.get('permission', 'Member')}"]
    return "\n".join(lines)


def _find_command(name: str) -> Optional[Tuple[str, dict]]:
    """Look up a command by name or alias. Return (category, cmd_dict) or None."""
    name = name.lstrip("/").lower()
    for category, data in HELP_DATA.items():
        for cmd in data["commands"]:
            if cmd["name"].lower() == name:
                return category, cmd
            if name in [a.lower() for a in cmd.get("aliases", [])]:
                return category, cmd
    return None


def _search_commands(query: str) -> List[Tuple[str, dict]]:
    """Search commands by keyword across all categories."""
    query = query.lower().strip()
    results: List[Tuple[str, dict]] = []
    seen: set = set()
    for category, data in HELP_DATA.items():
        for cmd in data["commands"]:
            key = cmd["name"]
            if key in seen:
                continue
            haystack = (
                cmd["name"]
                + " "
                + cmd["description"]
                + " "
                + " ".join(cmd.get("aliases", []))
            )
            if query in haystack.lower():
                results.append((category, cmd))
                seen.add(key)
    return results


# ── Command handlers ──────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /help              → category menu
    /help <category>   → commands in category
    /help <command>    → detailed command help
    """
    user = update.effective_user
    uid = user.id if user else 0
    query = " ".join(context.args).strip() if context.args else ""

    if not query:
        text = (
            f"{bold('Help Menu')}  (~_~)\n\n"
            "Browse commands by category or use /search_help to find a specific command.\n\n"
            "Available categories:"
        )
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_category_keyboard(uid),
        )
        return

    # Try exact category match (case-insensitive)
    cat_key = next((k for k in HELP_DATA if k.lower() == query.lower()), None)
    if cat_key:
        text, total = _build_category_list_text(cat_key, 0)
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_commands_keyboard(cat_key, 0, total, uid),
        )
        return

    # Try command / alias lookup
    result = _find_command(query)
    if result:
        category, cmd = result
        await update.effective_message.reply_text(
            _build_command_detail_text(cmd),
            parse_mode=ParseMode.HTML,
            reply_markup=_command_detail_keyboard(category, uid),
        )
        return

    # Fallback: search
    hits = _search_commands(query)
    if not hits:
        await update.effective_message.reply_text(
            f"(x)  No command or category found matching {code(escape_html(query))}.\n"
            "Use /help to browse all categories.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"{bold('Search Results')}  (?)  for {code(escape_html(query))}:", ""]
    for category, cmd in hits[:15]:
        lines.append(f"◆ {code('/' + cmd['name'])} ({italic(category)}) — {cmd['description']}")
    if len(hits) > 15:
        lines.append(f"\n{italic(f'... and {len(hits) - 15} more results.')}")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◄ Help Menu", callback_data=f"help:menu:{uid}")]]
        ),
    )


async def search_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/search_help <keyword> — Search commands by keyword."""
    user = update.effective_user
    uid = user.id if user else 0

    if not context.args:
        await update.effective_message.reply_text(
            f"{bold('Command Search')}  (?)\n\nUsage: {code('/search_help &lt;keyword&gt;')}\n"
            f"Example: {code('/search_help ban')}",
            parse_mode=ParseMode.HTML,
        )
        return

    query = " ".join(context.args)
    hits = _search_commands(query)

    if not hits:
        await update.effective_message.reply_text(
            f"(x)  No results for {code(escape_html(query))}.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"{bold('Search Results')}  (?)  for {code(escape_html(query))}:", ""]
    for category, cmd in hits[:20]:
        lines.append(f"◆ {code('/' + cmd['name'])} — {cmd['description']}")
        lines.append(f"  Category: {italic(category)}")
        lines.append("")

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("◄ Help Menu", callback_data=f"help:menu:{uid}")]]
        ),
    )


# ── Callback handler ──────────────────────────────────────────────────────────

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all help:* callback queries."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    # ── Owner check ──────────────────────────────────────────────────────────
    if not _owner_check(user.id, query.data or ""):
        await query.answer("(x_x)  This menu is not for you!", show_alert=True)
        return

    await query.answer()

    data = query.data  # e.g. "help:menu:uid", "help:cat:Admin:0:uid"
    uid = user.id

    # Split enough to get action; uid is always the last segment
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "menu":
        text = (
            f"{bold('Help Menu')}  (~_~)\n\n"
            "Browse commands by category or use /search_help to find a specific command.\n\n"
            "Available categories:"
        )
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=_category_keyboard(uid),
            )
        except TelegramError:
            pass
        return

    if action == "cat":
        # format: help:cat:CATEGORY:PAGE:uid
        category = parts[2] if len(parts) > 2 else ""
        try:
            page = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            page = 0

        if category not in HELP_DATA:
            await query.answer("(x)  Unknown category.", show_alert=True)
            return

        text, total = _build_category_list_text(category, page)
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=_commands_keyboard(category, page, total, uid),
            )
        except TelegramError:
            pass
        return

    if action == "cmd":
        cmd_name = parts[2] if len(parts) > 2 else ""
        result = _find_command(cmd_name)
        if not result:
            await query.answer("(x)  Command not found.", show_alert=True)
            return
        category, cmd = result
        try:
            await query.edit_message_text(
                _build_command_detail_text(cmd),
                parse_mode=ParseMode.HTML,
                reply_markup=_command_detail_keyboard(category, uid),
            )
        except TelegramError:
            pass
        return

    if action == "search":
        try:
            await query.edit_message_text(
                f"{bold('Search Help')}  (?)\n\nUse the command:\n{code('/search_help &lt;keyword&gt;')}\n\n"
                f"Example: {code('/search_help ban')}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◄ Back", callback_data=f"help:menu:{uid}")]]
                ),
            )
        except TelegramError:
            pass
        return

    if action == "close":
        try:
            await query.delete_message()
        except TelegramError:
            pass
        return

    # "noop" — ignore
    if action == "noop":
        return

    await query.answer("(x)  Unknown action.", show_alert=True)


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search_help", search_help_command))
    app.add_handler(CallbackQueryHandler(help_callback, pattern=r"^help:"))
