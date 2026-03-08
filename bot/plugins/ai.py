"""bot/plugins/ai.py – AI features (OpenAI if key set, else rule-based fallback)."""
from __future__ import annotations
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from bot.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_MAX_TOKENS

_client = None
if OPENAI_API_KEY:
    try:
        from openai import AsyncOpenAI
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        _client = None


async def _ai_complete(system: str, user: str) -> str:
    if _client:
        resp = await _client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=OPENAI_MAX_TOKENS,
        )
        return resp.choices[0].message.content.strip()
    return _rule_fallback(system, user)


def _rule_fallback(system: str, user: str) -> str:
    low = user.lower()
    if "sentiment" in system:
        pos = sum(w in low for w in ["good","great","love","happy","excellent","nice","awesome"])
        neg = sum(w in low for w in ["bad","hate","terrible","awful","sad","horrible","poor"])
        return "positive 😊" if pos > neg else ("negative 😞" if neg > pos else "neutral 😐")
    if "rephrase" in system:
        return f"[Rephrased]: {user}"
    if "grammar" in system:
        return f"✅ Looks fine (rule-based): {user}"
    if "style" in system:
        return f"[Styled]: {user}"
    if "summar" in system:
        words = user.split()
        return " ".join(words[:30]) + ("..." if len(words) > 30 else "")
    if "remix" in system:
        return f"[Remixed]: {user}"
    return f"🤖 (Rule-based) Echo: {user}"


def _get_text(update: Update, args: list[str], start: int = 0) -> str:
    if update.message.reply_to_message and update.message.reply_to_message.text:
        return update.message.reply_to_message.text
    return " ".join(args[start:])


async def ai_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args or [])
    if not text:
        await update.message.reply_text("Usage: /ai <text>")
        return
    reply = await _ai_complete("You are a helpful assistant.", text)
    await update.message.reply_text(reply)


async def analyze_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = _get_text(update, ctx.args or [])
    if not text:
        await update.message.reply_text("Usage: /analyze <text> or reply to a message")
        return
    result = await _ai_complete("Perform sentiment analysis. Reply with one word: positive, negative, or neutral, then a brief reason.", text)
    await update.message.reply_text(f"📊 Sentiment: {result}")


async def rephrase_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /rephrase <formal|casual|funny> <text>")
        return
    tone, text = args[0], " ".join(args[1:])
    result = await _ai_complete(f"Rephrase the following text in a {tone} tone.", text)
    await update.message.reply_text(result)


async def grammar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = _get_text(update, ctx.args or [])
    if not text:
        await update.message.reply_text("Usage: /grammar <text> or reply to a message")
        return
    result = await _ai_complete("Check the grammar of this text and provide corrected version with brief explanation of changes.", text)
    await update.message.reply_text(f"📝 Grammar check:\n{result}")


async def style_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /style <formal|casual|emoji|pirate> <text>")
        return
    style, text = args[0], " ".join(args[1:])
    result = await _ai_complete(f"Rewrite the following text in a {style} style.", text)
    await update.message.reply_text(result)


async def summarize_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = _get_text(update, ctx.args or [])
    if not text:
        await update.message.reply_text("Usage: /summarize <text> or reply to a message")
        return
    result = await _ai_complete("Summarize the following text concisely.", text)
    await update.message.reply_text(f"📋 Summary:\n{result}")


async def remix_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /remix <funny|serious> <text>")
        return
    mode, text = args[0], " ".join(args[1:])
    result = await _ai_complete(f"Remix the following text in a {mode} way.", text)
    await update.message.reply_text(result)


def setup(app: Application) -> None:
    app.add_handler(CommandHandler("ai", ai_cmd))
    app.add_handler(CommandHandler("analyze", analyze_cmd))
    app.add_handler(CommandHandler("rephrase", rephrase_cmd))
    app.add_handler(CommandHandler("grammar", grammar_cmd))
    app.add_handler(CommandHandler("style", style_cmd))
    app.add_handler(CommandHandler("summarize", summarize_cmd))
    app.add_handler(CommandHandler("remix", remix_cmd))
