"""bot/plugins/information.py – Info/search using free APIs."""
from __future__ import annotations
import datetime
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


async def _get(url: str, params: dict | None = None) -> dict | list | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception:
        return None


async def weather_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    city = " ".join(ctx.args or [])
    if not city:
        await update.message.reply_text("Usage: /weather <city>")
        return
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(f"https://wttr.in/{city}?format=3", timeout=aiohttp.ClientTimeout(total=10)) as r:
                text = await r.text()
            await update.message.reply_text(f"🌤 {text.strip()}")
        except Exception:
            await update.message.reply_text("❌ Could not fetch weather.")


async def wiki_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(ctx.args or [])
    if not query:
        await update.message.reply_text("Usage: /wiki <query>")
        return
    data = await _get("https://en.wikipedia.org/api/rest_v1/page/summary/" + query.replace(" ", "_"))
    if not data or "extract" not in data:
        await update.message.reply_text("❌ No results found.")
        return
    title = data.get("title", query)
    extract = data["extract"][:500]
    url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
    await update.message.reply_text(f"📖 *{title}*\n\n{extract}\n\n{url}", parse_mode="Markdown")


async def crypto_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    symbol = (ctx.args[0] if ctx.args else "").lower()
    if not symbol:
        await update.message.reply_text("Usage: /crypto <symbol> (e.g. bitcoin)")
        return
    data = await _get(f"https://api.coingecko.com/api/v3/simple/price", params={"ids": symbol, "vs_currencies": "usd", "include_24hr_change": "true"})
    if not data or symbol not in data:
        await update.message.reply_text("❌ Symbol not found.")
        return
    info = data[symbol]
    price = info.get("usd", "N/A")
    change = info.get("usd_24h_change", 0)
    arrow = "📈" if change >= 0 else "📉"
    await update.message.reply_text(f"💰 {symbol.upper()}: ${price:,.4f} {arrow} {change:.2f}% (24h)")


async def define_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    word = ctx.args[0] if ctx.args else ""
    if not word:
        await update.message.reply_text("Usage: /define <word>")
        return
    data = await _get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}")
    if not data or not isinstance(data, list):
        await update.message.reply_text("❌ Word not found.")
        return
    meanings = data[0].get("meanings", [])
    if not meanings:
        await update.message.reply_text("❌ No definition found.")
        return
    pos = meanings[0].get("partOfSpeech", "")
    defs = meanings[0].get("definitions", [{}])
    definition = defs[0].get("definition", "N/A")
    await update.message.reply_text(f"📚 *{word}* ({pos})\n{definition}", parse_mode="Markdown")


async def time_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tz_name = " ".join(ctx.args or [])
    if not tz_name:
        await update.message.reply_text("Usage: /time <timezone> (e.g. US/Eastern)")
        return
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
        now = datetime.datetime.now(tz)
        await update.message.reply_text(f"🕐 Time in {tz_name}: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    except Exception:
        await update.message.reply_text("❌ Invalid timezone. Use IANA format e.g. America/New_York")


async def anime_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    title = " ".join(ctx.args or [])
    if not title:
        await update.message.reply_text("Usage: /anime <title>")
        return
    data = await _get("https://api.jikan.moe/v4/anime", params={"q": title, "limit": 1})
    if not data or not data.get("data"):
        await update.message.reply_text("❌ Anime not found.")
        return
    a = data["data"][0]
    name = a.get("title", "N/A")
    score = a.get("score", "N/A")
    episodes = a.get("episodes", "N/A")
    synopsis = (a.get("synopsis") or "")[:300]
    url = a.get("url", "")
    await update.message.reply_text(
        f"🎌 *{name}*\n⭐ Score: {score} | 📺 Episodes: {episodes}\n\n{synopsis}\n{url}",
        parse_mode="Markdown",
    )


async def lyrics_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(ctx.args or [])
    if not query:
        await update.message.reply_text("Usage: /lyrics <artist - song>")
        return
    parts = query.split("-", 1)
    if len(parts) < 2:
        await update.message.reply_text("Format: /lyrics <artist> - <song>")
        return
    artist, title = parts[0].strip(), parts[1].strip()
    data = await _get(f"https://api.lyrics.ovh/v1/{artist}/{title}")
    if not data or "lyrics" not in data:
        await update.message.reply_text("❌ Lyrics not found.")
        return
    lyrics = data["lyrics"][:2000]
    await update.message.reply_text(f"🎵 *{title}* by {artist}\n\n{lyrics}", parse_mode="Markdown")


def setup(app: Application) -> None:
    app.add_handler(CommandHandler("weather", weather_cmd))
    app.add_handler(CommandHandler("wiki", wiki_cmd))
    app.add_handler(CommandHandler("crypto", crypto_cmd))
    app.add_handler(CommandHandler("define", define_cmd))
    app.add_handler(CommandHandler("time", time_cmd))
    app.add_handler(CommandHandler("anime", anime_cmd))
    app.add_handler(CommandHandler("lyrics", lyrics_cmd))
