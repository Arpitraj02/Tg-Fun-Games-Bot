"""
bot/plugins/utilities.py
─────────────────────────
Utility commands: calculator, unit/currency converter, QR code, hashing,
password generator, UUID, color info, timestamps, encoding tools,
text analysis, URL shortener, TTS, translation, and more.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import logging
import math
import operator
import os
import random
import re
import string
import struct
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Optional, Union

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.helpers.formatters import bold, code, escape_html, italic

logger = logging.getLogger(__name__)

GENERATED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "generated"
)
os.makedirs(GENERATED_DIR, exist_ok=True)


async def _reply(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


def _get_text_arg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    if context.args:
        return " ".join(context.args)
    msg = update.effective_message
    if msg and msg.reply_to_message:
        return msg.reply_to_message.text or msg.reply_to_message.caption
    return None


# ── /calc ──────────────────────────────────────────────────────────────────────

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCS = {
    "abs": abs, "round": round, "sqrt": math.sqrt,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "floor": math.floor, "ceil": math.ceil,
    "pi": math.pi, "e": math.e,
}


def _safe_eval(node: ast.AST) -> Union[int, float]:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value}")
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported operation: {op_type}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if op_type == ast.Pow and abs(right) > 100:
            raise ValueError("Exponent too large.")
        return _SAFE_OPS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported operation: {op_type}")
        return _SAFE_OPS[op_type](_safe_eval(node.operand))
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
            raise ValueError(f"Unknown function: {getattr(node.func, 'id', '?')}")
        args = [_safe_eval(a) for a in node.args]
        return _SAFE_FUNCS[node.func.id](*args)
    elif isinstance(node, ast.Name):
        if node.id in _SAFE_FUNCS:
            v = _SAFE_FUNCS[node.id]
            if isinstance(v, (int, float)):
                return v
        raise ValueError(f"Unknown name: {node.id}")
    raise ValueError(f"Unsupported expression type: {type(node)}")


async def calc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/calc <expression> — Safe math calculator."""
    expr = _get_text_arg(update, context)
    if not expr:
        await _reply(update, f"❌ Usage: {code('/calc &lt;expression&gt;')}\nExample: {code('/calc 2+2*sin(pi/4)')}")
        return

    safe_expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/")
    try:
        tree = ast.parse(safe_expr, mode="eval")
        result = _safe_eval(tree.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        await _reply(update,
            f"🧮 {bold('Calculator')}\n\n"
            f"📝 {code(escape_html(expr))}\n"
            f"✅ = {bold(code(str(result)))}"
        )
    except ZeroDivisionError:
        await _reply(update, "❌ Division by zero!")
    except Exception as e:
        await _reply(update, f"❌ Error: {escape_html(str(e))}\nPlease check your expression.")


# ── /convert ──────────────────────────────────────────────────────────────────

_UNIT_CATEGORIES = {
    "length": {
        "m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001,
        "mi": 1609.344, "yd": 0.9144, "ft": 0.3048, "in": 0.0254,
        "nm": 1852.0,
    },
    "weight": {
        "kg": 1.0, "g": 0.001, "mg": 1e-6, "lb": 0.453592,
        "oz": 0.0283495, "t": 1000.0,
    },
    "data": {
        "b": 1.0, "kb": 1024.0, "mb": 1048576.0, "gb": 1073741824.0,
        "tb": 1099511627776.0, "pb": 1.126e15,
    },
    "area": {
        "m2": 1.0, "km2": 1e6, "cm2": 0.0001, "ft2": 0.092903,
        "in2": 0.000645, "acre": 4046.86, "ha": 10000.0,
    },
    "speed": {
        "ms": 1.0, "kmh": 0.277778, "mph": 0.44704, "knot": 0.514444,
    },
}


def _convert_temp(value: float, from_unit: str, to_unit: str) -> Optional[float]:
    """Temperature conversion (celsius, fahrenheit, kelvin)."""
    celsius: float
    if from_unit == "c":
        celsius = value
    elif from_unit == "f":
        celsius = (value - 32) * 5 / 9
    elif from_unit == "k":
        celsius = value - 273.15
    else:
        return None

    if to_unit == "c":
        return celsius
    elif to_unit == "f":
        return celsius * 9 / 5 + 32
    elif to_unit == "k":
        return celsius + 273.15
    return None


async def convert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/convert <value> <from_unit> <to_unit> — Unit converter."""
    args = context.args or []
    if len(args) < 3:
        await _reply(update,
            f"❌ Usage: {code('/convert &lt;value&gt; &lt;from&gt; &lt;to&gt;')}\n"
            f"Example: {code('/convert 100 km mi')}\n"
            f"Categories: length, weight, temperature (c/f/k), data, area, speed"
        )
        return

    try:
        value = float(args[0])
    except ValueError:
        await _reply(update, "❌ Invalid value. Please provide a number.")
        return

    from_unit = args[1].lower()
    to_unit = args[2].lower()

    # Temperature check
    temp_units = {"c", "f", "k", "celsius", "fahrenheit", "kelvin"}
    temp_aliases = {"celsius": "c", "fahrenheit": "f", "kelvin": "k"}
    f_norm = temp_aliases.get(from_unit, from_unit)
    t_norm = temp_aliases.get(to_unit, to_unit)

    if f_norm in {"c", "f", "k"} or t_norm in {"c", "f", "k"}:
        result = _convert_temp(value, f_norm, t_norm)
        if result is None:
            await _reply(update, "❌ Invalid temperature units. Use c, f, or k.")
            return
        await _reply(update,
            f"🌡️ {bold('Temperature Conversion')}\n\n"
            f"{code(str(value))} {from_unit.upper()} = {bold(code(f'{result:.4f}'.rstrip('0').rstrip('.')))} {to_unit.upper()}"
        )
        return

    # Other units
    found_cat = None
    from_factor = None
    to_factor = None
    for cat, units in _UNIT_CATEGORIES.items():
        if from_unit in units and to_unit in units:
            found_cat = cat
            from_factor = units[from_unit]
            to_factor = units[to_unit]
            break

    if found_cat is None:
        await _reply(update, f"❌ Could not find both units. Supported: {', '.join(_UNIT_CATEGORIES.keys())}")
        return

    result = value * from_factor / to_factor
    formatted = f"{result:.6g}"

    await _reply(update,
        f"📏 {bold('Unit Conversion')} ({found_cat})\n\n"
        f"{code(str(value))} {from_unit} = {bold(code(formatted))} {to_unit}"
    )


# ── /currency ─────────────────────────────────────────────────────────────────

# Mock exchange rates relative to USD
_MOCK_RATES: dict[str, float] = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 149.5,
    "CAD": 1.36, "AUD": 1.53, "CHF": 0.90, "CNY": 7.24,
    "INR": 83.1, "MXN": 17.2, "BRL": 4.97, "KRW": 1325.0,
    "SGD": 1.34, "HKD": 7.82, "NOK": 10.7, "SEK": 10.5,
    "DKK": 6.89, "NZD": 1.63, "ZAR": 18.6, "RUB": 90.0,
    "TRY": 30.5, "AED": 3.67, "SAR": 3.75, "THB": 35.1,
    "BTC": 0.000024, "ETH": 0.00036,
}


async def currency_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/currency <amount> <from> <to> — Currency conversion."""
    args = context.args or []
    if len(args) < 3:
        await _reply(update, f"❌ Usage: {code('/currency &lt;amount&gt; &lt;from&gt; &lt;to&gt;')}\nExample: {code('/currency 100 USD EUR')}")
        return

    try:
        amount = float(args[0])
    except ValueError:
        await _reply(update, "❌ Invalid amount.")
        return

    from_cur = args[1].upper()
    to_cur = args[2].upper()

    if from_cur not in _MOCK_RATES:
        await _reply(update, f"❌ Unknown currency: {code(from_cur)}")
        return
    if to_cur not in _MOCK_RATES:
        await _reply(update, f"❌ Unknown currency: {code(to_cur)}")
        return

    usd_amount = amount / _MOCK_RATES[from_cur]
    result = usd_amount * _MOCK_RATES[to_cur]

    await _reply(update,
        f"💱 {bold('Currency Conversion')}\n\n"
        f"{code(f'{amount:.2f}')} {from_cur} = {bold(code(f'{result:.4f}'))} {to_cur}\n"
        f"{italic('(Based on approximate exchange rates)')}"
    )


# ── /qr ───────────────────────────────────────────────────────────────────────

async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/qr <text|url> — Generate QR code image."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/qr &lt;text or URL&gt;')}")
        return

    try:
        import qrcode
        qr = qrcode.QRCode(version=None, box_size=10, border=4)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        await update.effective_message.reply_photo(
            photo=buf,
            caption=f"📷 QR code for: {code(escape_html(text[:100]))}",
            parse_mode=ParseMode.HTML,
        )
    except ImportError:
        await _reply(update, "❌ QR code library not installed. Ask an admin to run: pip install qrcode[pil]")
    except Exception as e:
        await _reply(update, f"❌ Error generating QR: {escape_html(str(e))}")


# ── /base64 ───────────────────────────────────────────────────────────────────

async def base64_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/base64 <encode|decode> <text> — Base64 operations."""
    args = context.args or []
    if len(args) < 2:
        await _reply(update, f"❌ Usage: {code('/base64 &lt;encode|decode&gt; &lt;text&gt;')}")
        return

    operation = args[0].lower()
    text = " ".join(args[1:])

    try:
        if operation == "encode":
            result = base64.b64encode(text.encode()).decode()
            await _reply(update, f"🔐 {bold('Base64 Encode')}\n\nInput: {code(escape_html(text[:50]))}\n\nResult:\n{code(result)}")
        elif operation == "decode":
            decoded = base64.b64decode(text.encode()).decode("utf-8", errors="replace")
            await _reply(update, f"🔓 {bold('Base64 Decode')}\n\nInput: {code(escape_html(text[:50]))}\n\nResult:\n{code(escape_html(decoded))}")
        else:
            await _reply(update, f"❌ Use {code('encode')} or {code('decode')}.")
    except Exception as e:
        await _reply(update, f"❌ Error: {escape_html(str(e))}")


# ── /hash ─────────────────────────────────────────────────────────────────────

async def hash_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hash <md5|sha1|sha256|sha512> <text> — Generate hash."""
    args = context.args or []
    if len(args) < 2:
        await _reply(update, f"❌ Usage: {code('/hash &lt;md5|sha1|sha256|sha512&gt; &lt;text&gt;')}")
        return

    algo = args[0].lower()
    text = " ".join(args[1:]).encode()
    algo_map = {
        "md5": hashlib.md5,
        "sha1": hashlib.sha1,
        "sha256": hashlib.sha256,
        "sha512": hashlib.sha512,
    }

    if algo not in algo_map:
        await _reply(update, f"❌ Supported algorithms: {', '.join(code(a) for a in algo_map)}")
        return

    result = algo_map[algo](text).hexdigest()
    await _reply(update,
        f"🔒 {bold(f'{algo.upper()} Hash')}\n\n"
        f"Input: {code(escape_html(args[1:1+1][0][:40]))}\n\n"
        f"Hash:\n{code(result)}"
    )


# ── /password ─────────────────────────────────────────────────────────────────

async def password_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/password [length] [include_special] — Generate secure password."""
    args = context.args or []
    try:
        length = min(max(int(args[0]), 8), 64) if args else 16
    except ValueError:
        length = 16

    include_special = len(args) > 1 and args[1].lower() in ("true", "yes", "1")

    chars = string.ascii_letters + string.digits
    if include_special:
        chars += "!@#$%^&*()_+-=[]{}|;:,.<>?"

    password = "".join(random.SystemRandom().choice(chars) for _ in range(length))

    # Strength indicator
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in string.punctuation for c in password)
    strength = sum([has_upper, has_lower, has_digit, has_special])
    strength_label = ["Very Weak 🔴", "Weak 🟠", "Medium 🟡", "Strong 🟢", "Very Strong 💪"][strength]

    await _reply(update,
        f"🔐 {bold('Password Generator')}\n\n"
        f"Password: {code(password)}\n"
        f"📏 Length: {length}\n"
        f"💪 Strength: {strength_label}"
    )


# ── /uuid ─────────────────────────────────────────────────────────────────────

async def uuid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/uuid — Generate UUID4."""
    uid = str(uuid.uuid4())
    await _reply(update, f"🆔 {bold('UUID4')}\n\n{code(uid)}")


# ── /color ────────────────────────────────────────────────────────────────────

_COLOR_NAMES: dict[str, str] = {
    "red": "#FF0000", "green": "#00FF00", "blue": "#0000FF",
    "white": "#FFFFFF", "black": "#000000", "yellow": "#FFFF00",
    "cyan": "#00FFFF", "magenta": "#FF00FF", "orange": "#FFA500",
    "purple": "#800080", "pink": "#FFC0CB", "brown": "#A52A2A",
    "gray": "#808080", "grey": "#808080",
}


async def color_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/color <hex|rgb|name> — Color info."""
    args = context.args or []
    if not args:
        await _reply(update, f"❌ Usage: {code('/color &lt;#HEX&gt;')} or {code('/color &lt;name&gt;')} or {code('/color &lt;R&gt; &lt;G&gt; &lt;B&gt;')}")
        return

    hex_color: Optional[str] = None
    r = g = b = 0

    try:
        if len(args) == 3:
            r, g, b = int(args[0]), int(args[1]), int(args[2])
            hex_color = f"#{r:02X}{g:02X}{b:02X}"
        elif args[0].startswith("#"):
            hex_color = args[0].upper()
            h = hex_color.lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        elif args[0].lower() in _COLOR_NAMES:
            hex_color = _COLOR_NAMES[args[0].lower()]
            h = hex_color.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        else:
            await _reply(update, f"❌ Unknown color format or name.")
            return
    except (ValueError, KeyError):
        await _reply(update, "❌ Invalid color format.")
        return

    # HSL
    r_n, g_n, b_n = r / 255, g / 255, b / 255
    cmax = max(r_n, g_n, b_n)
    cmin = min(r_n, g_n, b_n)
    delta = cmax - cmin
    l = (cmax + cmin) / 2
    s = 0 if delta == 0 else delta / (1 - abs(2 * l - 1))
    if delta == 0:
        h_val = 0.0
    elif cmax == r_n:
        h_val = 60 * (((g_n - b_n) / delta) % 6)
    elif cmax == g_n:
        h_val = 60 * ((b_n - r_n) / delta + 2)
    else:
        h_val = 60 * ((r_n - g_n) / delta + 4)

    swatch = "🟥" if r > 200 else ("🟦" if b > 200 else ("🟩" if g > 200 else "⬜"))

    await _reply(update,
        f"🎨 {bold('Color Info')}\n\n"
        f"{swatch} Swatch (approximation)\n\n"
        f"🔑 {bold('HEX:')} {code(hex_color)}\n"
        f"🎨 {bold('RGB:')} {code(f'rgb({r}, {g}, {b})')}\n"
        f"🌈 {bold('HSL:')} {code(f'hsl({h_val:.0f}, {s*100:.1f}%, {l*100:.1f}%)')}\n"
        f"🌑 {bold('Brightness:')} {'Light' if l > 0.5 else 'Dark'}"
    )


# ── /timestamp ────────────────────────────────────────────────────────────────

async def timestamp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/timestamp [date] — Unix timestamp."""
    now = datetime.now(timezone.utc)
    ts = int(now.timestamp())

    text = (
        f"⏰ {bold('Timestamp')}\n\n"
        f"🕐 {bold('Current UTC:')} {code(now.strftime('%Y-%m-%d %H:%M:%S'))}\n"
        f"🔢 {bold('Unix Timestamp:')} {code(str(ts))}\n"
        f"📅 {bold('ISO 8601:')} {code(now.isoformat())}"
    )
    await _reply(update, text)


# ── /morse ────────────────────────────────────────────────────────────────────

_MORSE: dict[str, str] = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..", "0": "-----", "1": ".----", "2": "..---", "3": "...--",
    "4": "....-", "5": ".....", "6": "-....", "7": "--...", "8": "---..",
    "9": "----.", " ": "/",
}
_MORSE_REV = {v: k for k, v in _MORSE.items()}


async def morse_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/morse <encode|decode> <text> — Morse code."""
    args = context.args or []
    if len(args) < 2:
        await _reply(update, f"❌ Usage: {code('/morse &lt;encode|decode&gt; &lt;text&gt;')}")
        return

    op = args[0].lower()
    text = " ".join(args[1:])

    try:
        if op == "encode":
            result = " ".join(_MORSE.get(c.upper(), "?") for c in text)
            await _reply(update, f"📡 {bold('Morse Encode')}\n\nInput: {code(escape_html(text))}\n\nResult:\n{code(result)}")
        elif op == "decode":
            words = text.split(" / ")
            decoded = "".join(_MORSE_REV.get(m, "?") for word in words for m in (word.split() + ["/"])).rstrip("/")
            await _reply(update, f"📡 {bold('Morse Decode')}\n\nInput: {code(escape_html(text[:50]))}\n\nResult:\n{code(decoded)}")
        else:
            await _reply(update, f"❌ Use {code('encode')} or {code('decode')}.")
    except Exception as e:
        await _reply(update, f"❌ Error: {escape_html(str(e))}")


# ── /binary ───────────────────────────────────────────────────────────────────

async def binary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/binary <encode|decode> <text> — Binary conversion."""
    args = context.args or []
    if len(args) < 2:
        await _reply(update, f"❌ Usage: {code('/binary &lt;encode|decode&gt; &lt;text&gt;')}")
        return

    op = args[0].lower()
    text = " ".join(args[1:])

    try:
        if op == "encode":
            result = " ".join(format(ord(c), "08b") for c in text)
            await _reply(update, f"💻 {bold('Binary Encode')}\n\nInput: {code(escape_html(text[:30]))}\n\nResult:\n{code(result[:500])}")
        elif op == "decode":
            groups = text.split()
            decoded = "".join(chr(int(b, 2)) for b in groups if len(b) == 8 and all(c in "01" for c in b))
            await _reply(update, f"💻 {bold('Binary Decode')}\n\nResult: {code(escape_html(decoded))}")
        else:
            await _reply(update, f"❌ Use {code('encode')} or {code('decode')}.")
    except Exception as e:
        await _reply(update, f"❌ Error: {escape_html(str(e))}")


# ── /hex ──────────────────────────────────────────────────────────────────────

async def hex_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hex <encode|decode> <text> — Hex conversion."""
    args = context.args or []
    if len(args) < 2:
        await _reply(update, f"❌ Usage: {code('/hex &lt;encode|decode&gt; &lt;text&gt;')}")
        return

    op = args[0].lower()
    text = " ".join(args[1:])

    try:
        if op == "encode":
            result = text.encode().hex()
            await _reply(update, f"🔣 {bold('Hex Encode')}\n\nInput: {code(escape_html(text[:30]))}\n\nResult:\n{code(result[:500])}")
        elif op == "decode":
            clean = text.replace(" ", "")
            decoded = bytes.fromhex(clean).decode("utf-8", errors="replace")
            await _reply(update, f"🔣 {bold('Hex Decode')}\n\nResult: {code(escape_html(decoded))}")
        else:
            await _reply(update, f"❌ Use {code('encode')} or {code('decode')}.")
    except Exception as e:
        await _reply(update, f"❌ Error: {escape_html(str(e))}")


# ── /count ────────────────────────────────────────────────────────────────────

async def count_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/count <text> — Word/character/line count."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/count &lt;text&gt;')} or reply to a message.")
        return

    words = len(text.split())
    chars = len(text)
    chars_no_space = len(text.replace(" ", ""))
    lines = text.count("\n") + 1
    sentences = len(re.findall(r"[.!?]+", text)) or 1

    await _reply(update,
        f"📊 {bold('Text Stats')}\n\n"
        f"📝 {bold('Words:')} {code(str(words))}\n"
        f"🔤 {bold('Characters:')} {code(str(chars))}\n"
        f"🔡 {bold('Chars (no spaces):')} {code(str(chars_no_space))}\n"
        f"📄 {bold('Lines:')} {code(str(lines))}\n"
        f"💬 {bold('Sentences:')} {code(str(sentences))}"
    )


# ── /paste ────────────────────────────────────────────────────────────────────

_paste_store: dict[str, str] = {}


async def paste_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/paste <text> — Create mock paste."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/paste &lt;text&gt;')} or reply to a message.")
        return

    paste_id = uuid.uuid4().hex[:8]
    _paste_store[paste_id] = text
    await _reply(update,
        f"📋 {bold('Paste Created!')}\n\n"
        f"🔑 ID: {code(paste_id)}\n"
        f"🔗 (Mock URL) paste.gg/{paste_id}\n\n"
        f"{italic('This is a local paste and will be lost on restart.')}"
    )


# ── /shorten ──────────────────────────────────────────────────────────────────

async def shorten_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/shorten <url> — Shorten URL via is.gd API."""
    args = context.args or []
    if not args:
        await _reply(update, f"❌ Usage: {code('/shorten &lt;url&gt;')}")
        return

    url = args[0]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://is.gd/create.php",
                params={"format": "simple", "url": url},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    short = await resp.text()
                    await _reply(update,
                        f"🔗 {bold('URL Shortened!')}\n\n"
                        f"Original: {code(escape_html(url[:60]))}\n"
                        f"Short: {bold(short.strip())}"
                    )
                else:
                    await _reply(update, f"❌ Shortening failed (HTTP {resp.status}).")
    except ImportError:
        await _reply(update, "❌ aiohttp not available.")
    except Exception as e:
        await _reply(update, f"❌ Error: {escape_html(str(e))}")


# ── /tts ──────────────────────────────────────────────────────────────────────

async def tts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tts <text> — Text to speech (Google TTS)."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/tts &lt;text&gt;')} or reply to a message.")
        return
    if len(text) > 200:
        await _reply(update, "❌ Text too long. Max 200 characters for TTS.")
        return

    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang="en")
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        await update.effective_message.reply_voice(
            voice=buf,
            caption=f"🔊 {italic(escape_html(text[:50]))}",
            parse_mode=ParseMode.HTML,
        )
    except ImportError:
        await _reply(update, "❌ gTTS library not installed. Ask admin to run: pip install gTTS")
    except Exception as e:
        await _reply(update, f"❌ TTS failed: {escape_html(str(e))}")


# ── /translate ────────────────────────────────────────────────────────────────

async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/translate <lang> <text or reply> — Translate text."""
    args = context.args or []
    if not args:
        await _reply(update, f"❌ Usage: {code('/translate &lt;lang_code&gt; &lt;text&gt;')}\nExample: {code('/translate es Hello world')}")
        return

    target_lang = args[0].lower()
    if len(args) > 1:
        text = " ".join(args[1:])
    else:
        msg = update.effective_message
        reply = msg.reply_to_message if msg else None
        text = (reply.text or reply.caption or "") if reply else ""

    if not text:
        await _reply(update, "❌ No text to translate.")
        return

    try:
        from googletrans import Translator
        translator = Translator()
        result = translator.translate(text, dest=target_lang)
        await _reply(update,
            f"🌐 {bold('Translation')}\n\n"
            f"🔤 {bold('From:')} {code(result.src or 'auto')}\n"
            f"🔠 {bold('To:')} {code(target_lang)}\n\n"
            f"{italic(escape_html(text[:100]))}\n\n"
            f"➡️ {bold(escape_html(result.text))}"
        )
    except ImportError:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://libretranslate.com/translate",
                    json={"q": text, "source": "auto", "target": target_lang, "format": "text"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        translated = data.get("translatedText", text)
                        await _reply(update,
                            f"🌐 {bold('Translation')}\n\n"
                            f"🔠 {bold('To:')} {code(target_lang)}\n\n"
                            f"{italic(escape_html(text[:100]))}\n\n"
                            f"➡️ {bold(escape_html(translated))}"
                        )
                    else:
                        await _reply(update, f"❌ Translation service unavailable (HTTP {resp.status}).")
        except Exception as e:
            await _reply(update, f"❌ Translation failed: {escape_html(str(e))}\nInstall googletrans: pip install googletrans==4.0.0-rc1")


# ── /detectlang ───────────────────────────────────────────────────────────────

async def detectlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/detectlang <text> — Detect language."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/detectlang &lt;text&gt;')} or reply to a message.")
        return

    try:
        from langdetect import detect, detect_langs
        langs = detect_langs(text)
        primary = detect(text)
        results = "\n".join(f"• {code(str(lang.lang))}: {lang.prob * 100:.1f}%" for lang in langs[:3])
        await _reply(update,
            f"🔍 {bold('Language Detection')}\n\n"
            f"Input: {italic(escape_html(text[:60]))}\n\n"
            f"Detected:\n{results}\n\n"
            f"Primary: {bold(code(primary))}"
        )
    except ImportError:
        await _reply(update, "❌ langdetect not installed. Ask admin: pip install langdetect")
    except Exception as e:
        await _reply(update, f"❌ Detection failed: {escape_html(str(e))}")


# ── /reverse_text ─────────────────────────────────────────────────────────────

async def reverse_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reverse_text <text> — Reverse text."""
    text = _get_text_arg(update, context)
    if not text:
        await _reply(update, f"❌ Usage: {code('/reverse_text &lt;text&gt;')}")
        return
    await _reply(update, f"🔄 {bold('Reversed:')}\n\n{code(escape_html(text[::-1]))}")


# ── Registration ──────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("calc", calc_command))
    app.add_handler(CommandHandler("calculate", calc_command))
    app.add_handler(CommandHandler("convert", convert_command))
    app.add_handler(CommandHandler("currency", currency_command))
    app.add_handler(CommandHandler("qr", qr_command))
    app.add_handler(CommandHandler("qrcode", qr_command))
    app.add_handler(CommandHandler("base64", base64_command))
    app.add_handler(CommandHandler("hash", hash_command))
    app.add_handler(CommandHandler("password", password_command))
    app.add_handler(CommandHandler("genpass", password_command))
    app.add_handler(CommandHandler("uuid", uuid_command))
    app.add_handler(CommandHandler("color", color_command))
    app.add_handler(CommandHandler("colour", color_command))
    app.add_handler(CommandHandler("timestamp", timestamp_command))
    app.add_handler(CommandHandler("time", timestamp_command))
    app.add_handler(CommandHandler("morse", morse_command))
    app.add_handler(CommandHandler("binary", binary_command))
    app.add_handler(CommandHandler("hex", hex_command))
    app.add_handler(CommandHandler("count", count_command))
    app.add_handler(CommandHandler("wordcount", count_command))
    app.add_handler(CommandHandler("paste", paste_command))
    app.add_handler(CommandHandler("shorten", shorten_command))
    app.add_handler(CommandHandler("shorturl", shorten_command))
    app.add_handler(CommandHandler("tts", tts_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CommandHandler("tr", translate_command))
    app.add_handler(CommandHandler("detectlang", detectlang_command))
    app.add_handler(CommandHandler("reverse_text", reverse_text_command))
