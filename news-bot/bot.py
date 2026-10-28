"""
Discord-бот: ежедневные сводки в разные каналы.

  • NEWS_CHANNEL_ID    — сводка новостей из RSS-лент
  • SVO_CHANNEL_ID     — сводка по СВО (новости из тех же лент, отобранные по ключевым словам,
                         плюс отдельные ленты из SVO_FEEDS, если вы их укажете)
  • HISTORY_CHANNEL_ID — «Этот день в истории России» (события с этой датой из Википедии)

Запуск:  python bot.py
"""

import asyncio
import calendar
import datetime as dt
import html
import json
import logging
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ─────────────────────────────── НАСТРОЙКИ ───────────────────────────────

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
NEWS_CHANNEL_ID = int(os.getenv("NEWS_CHANNEL_ID", "0"))
SVO_CHANNEL_ID = int(os.getenv("SVO_CHANNEL_ID", "0"))
HISTORY_CHANNEL_ID = int(os.getenv("HISTORY_CHANNEL_ID", "0"))
TZ = ZoneInfo("Europe/Moscow")
POST_TIMES = [
    dt.time(int(h), int(m), tzinfo=TZ)
    for h, m in (t.strip().split(":") for t in os.getenv("POST_TIMES", "09:00").split(","))
]

# Общие новостные ленты: название -> адрес RSS
NEWS_FEEDS = {
    "ТАСС": "https://tass.ru/rss/v2.xml",
    "РИА Новости": "https://ria.ru/export/rss2/archive/index.xml",
    "Лента.ру": "https://lenta.ru/rss/news",
    "Интерфакс": "https://www.interfax.ru/rss.asp",
}

# Ленты, целиком посвящённые СВО (если есть такая лента — впишите её сюда).
# Кроме них, в сводку СВО попадают новости из NEWS_FEEDS с ключевыми словами ниже.
SVO_FEEDS: dict[str, str] = {}

# Начала слов: «украин» найдёт «Украина», «украинских» и т.д.
SVO_KEYWORDS = [
    "спецоперац", "специальной военной операц", "минобороны", "донбасс", "запорожск",
    "херсонск", "линии соприкосновения", "освобожд", "беспилотник", "украин", "киевск режим",
]
# Короткие слова, которые ищутся только целиком (чтобы «СВО» не находилось в «свобода»)
SVO_WORDS = ["сво", "всу", "днр", "лнр", "бпла", "вс рф", "мо рф"]

# Если True — новости про СВО не дублируются в общей сводке
SVO_EXCLUDE_FROM_NEWS = True

MAX_ITEMS_PER_DIGEST = 10       # сколько новостей в одной сводке (Discord разрешает до 25 полей)
MAX_AGE_HOURS = 24              # брать только публикации не старше N часов
MAX_HISTORY_EVENTS = 6          # сколько исторических событий показывать

# Слова, по которым событие из Википедии считается относящимся к истории России
# (ищутся как начала слов)
RUSSIA_KEYWORDS = [
    "росси", "русск", "русь", "руси", "ссср", "советск", "москв", "петербург", "петроград",
    "ленинград", "новгород", "киевск", "кремл", "рсфср", "пётр", "екатерин", "красной арми",
    "большевик", "сибир", "казан", "волгоград", "сталинград", "севастопол", "крым",
    "романов", "гагарин", "байконур", "кутузов", "суворов", "ломоносов", "пушкин",
]

SENT_FILE = Path(__file__).with_name("sent.json")   # здесь бот помнит, что уже отправил

COLORS = {"news": 0x2B6CB0, "svo": 0xC53030, "history": 0xB7791F}
MONTHS_GEN = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
USER_AGENT = "Mozilla/5.0 (compatible; DiscordNewsBot/1.0)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("newsbot")


# ─────────────────────────── ПАМЯТЬ ОТПРАВЛЕННОГО ───────────────────────────

def load_sent() -> list[str]:
    try:
        return json.loads(SENT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_sent(ids: list[str]) -> None:
    SENT_FILE.write_text(json.dumps(ids[-3000:], ensure_ascii=False), encoding="utf-8")


# ──────────────────────────────── RSS ────────────────────────────────

def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def entry_time(entry) -> dt.datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return dt.datetime.fromtimestamp(calendar.timegm(parsed), dt.timezone.utc)


def entry_image(entry) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []):
            if media.get("url"):
                return media["url"]
    for link in entry.get("enclosures", []) + entry.get("links", []):
        if link.get("type", "").startswith("image/") and link.get("href"):
            return link["href"]
    return None


async def fetch_feed(session: aiohttp.ClientSession, source: str, url: str) -> list[dict]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            resp.raise_for_status()
            raw = await resp.read()
    except Exception as exc:
        log.warning("Не удалось загрузить %s (%s): %s", source, url, exc)
        return []

    feed = feedparser.parse(raw)
    items = []
    for e in feed.entries:
        link = e.get("link", "")
        title = clean_html(e.get("title", ""))
        if not link or not title:
            continue
        items.append({
            "id": e.get("id") or link,
            "source": source,
            "title": title,
            "link": link,
            "summary": clean_html(e.get("summary", "")),
            "time": entry_time(e),
            "image": entry_image(e),
        })
    log.info("%s: получено %d записей", source, len(items))
    return items


def word_regex(prefixes: list[str], words: tuple | list = ()) -> re.Pattern:
    """Ищет слова, начинающиеся с prefixes, и слова words целиком."""
    parts = [re.escape(p) + r"[а-яёa-z]*" for p in prefixes] + [re.escape(w) for w in words]
    return re.compile(r"(?<![а-яёa-z])(?:" + "|".join(parts) + r")(?![а-яёa-z])", re.I)


SVO_RE = word_regex(SVO_KEYWORDS, SVO_WORDS)
RUSSIA_RE = word_regex(RUSSIA_KEYWORDS)


def is_svo(item: dict) -> bool:
    return bool(SVO_RE.search(f"{item['title']} {item['summary']}"))


async def collect_news(session: aiohttp.ClientSession) -> tuple[list[dict], list[dict]]:
    """Возвращает (новости, новости СВО) — только свежие и ещё не отправленные."""
    sent = set(load_sent())
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_HOURS)

    general = await asyncio.gather(*(fetch_feed(session, s, u) for s, u in NEWS_FEEDS.items()))
    special = await asyncio.gather(*(fetch_feed(session, s, u) for s, u in SVO_FEEDS.items()))

    def fresh(items):
        seen_titles = set()
        for it in items:
            key = it["title"].lower()
            if it["id"] in sent or it["link"] in sent or key in seen_titles:
                continue
            if it["time"] and it["time"] < cutoff:
                continue
            seen_titles.add(key)
            yield it

    all_general = list(fresh(i for feed in general for i in feed))
    svo = list(fresh(i for feed in special for i in feed)) + [i for i in all_general if is_svo(i)]
    news = [i for i in all_general if not (SVO_EXCLUDE_FROM_NEWS and is_svo(i))]

    newest_first = lambda i: i["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    news.sort(key=newest_first, reverse=True)
    svo.sort(key=newest_first, reverse=True)
    return news[:MAX_ITEMS_PER_DIGEST], svo[:MAX_ITEMS_PER_DIGEST]


def mark_sent(items: list[dict]) -> None:
    sent = load_sent()
    for it in items:
        sent.append(it["id"])
        if it["link"] != it["id"]:
            sent.append(it["link"])
    save_sent(sent)


def today_ru() -> str:
    now = dt.datetime.now(TZ)
    return f"{now.day} {MONTHS_GEN[now.month]} {now.year}"


def build_digest_embed(items: list[dict], kind: str) -> discord.Embed:
    title = {"news": "📰 Сводка новостей", "svo": "🎖️ Сводка СВО"}[kind]
    embed = discord.Embed(
        title=f"{title} — {today_ru()}",
        description=f"Главное за последние {MAX_AGE_HOURS} ч. · публикаций: {len(items)}",
        color=COLORS[kind],
        timestamp=dt.datetime.now(dt.timezone.utc),
    )
    for n, it in enumerate(items, 1):
        when = f" · <t:{int(it['time'].timestamp())}:t>" if it["time"] else ""
        summary = shorten(it["summary"], 180) if it["summary"] and it["summary"] != it["title"] else ""
        value = (f"{summary}\n" if summary else "") + f"[Читать →]({it['link']}) · *{it['source']}*{when}"
        embed.add_field(name=shorten(f"{n}. {it['title']}", 256), value=shorten(value, 1024), inline=False)

    image = next((it["image"] for it in items if it["image"]), None)
    if image:
        embed.set_image(url=image)
    embed.set_footer(text="Источники: " + ", ".join(sorted({it["source"] for it in items})))
    return embed


# ─────────────────────────── ЭТОТ ДЕНЬ В ИСТОРИИ ───────────────────────────

def is_russian_history(text: str) -> bool:
    return bool(RUSSIA_RE.search(text))


def years_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "год"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "года"
    return "лет"


async def history_from_api(session: aiohttp.ClientSession, day: dt.date) -> list[dict]:
    """Готовый список событий дня из REST API Википедии."""
    url = f"https://ru.wikipedia.org/api/rest_v1/feed/onthisday/events/{day.month:02d}/{day.day:02d}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        resp.raise_for_status()
        data = await resp.json()
    events = []
    for ev in data.get("events", []):
        pages = ev.get("pages", [])
        titles = " ".join(p.get("normalizedtitle", p.get("title", "")) for p in pages)
        link = None
        if pages:
            link = pages[0].get("content_urls", {}).get("desktop", {}).get("page")
        thumb = next((p["thumbnail"]["source"] for p in pages if p.get("thumbnail")), None)
        events.append({"year": ev.get("year"), "text": ev.get("text", ""),
                       "search": f"{ev.get('text', '')} {titles}", "link": link, "image": thumb})
    return events


async def history_from_page(session: aiohttp.ClientSession, day: dt.date) -> list[dict]:
    """Запасной вариант: раздел «События» статьи «28 октября»."""
    page = f"{day.day} {MONTHS_GEN[day.month]}"
    params = {"action": "query", "prop": "extracts", "explaintext": "1", "titles": page,
              "format": "json", "formatversion": "2"}
    async with session.get("https://ru.wikipedia.org/w/api.php", params=params,
                           timeout=aiohttp.ClientTimeout(total=20)) as resp:
        resp.raise_for_status()
        data = await resp.json()
    text = data["query"]["pages"][0].get("extract", "")
    section = re.search(r"==\s*События\s*==(.*?)(?:\n==\s*[^=]|\Z)", text, re.S)
    if not section:
        return []
    events, year = [], None
    for line in section.group(1).splitlines():
        line = line.strip()
        m = re.match(r"^(\d{1,4})(?:\s*год)?\s*[—–-]\s*(.+)$", line)
        if m:
            year, line = int(m.group(1)), m.group(2)
        elif re.fullmatch(r"\d{1,4}( год)?", line):
            year = int(line.split()[0])
            continue
        if year and len(line) > 15 and not line.startswith("="):
            events.append({"year": year, "text": line, "search": line, "link": None, "image": None})
    return events


async def collect_history(session: aiohttp.ClientSession) -> list[dict]:
    day = dt.datetime.now(TZ).date()
    try:
        events = await history_from_api(session, day)
    except Exception as exc:
        log.warning("REST API Википедии недоступен (%s), читаю статью дня", exc)
        events = []
    if not events:
        try:
            events = await history_from_page(session, day)
        except Exception as exc:
            log.warning("Не удалось получить события дня: %s", exc)
            return []

    russian = [e for e in events if is_russian_history(e["search"])]
    russian.sort(key=lambda e: e["year"] or 0)
    if len(russian) > MAX_HISTORY_EVENTS:  # равномерно по эпохам
        step = len(russian) / MAX_HISTORY_EVENTS
        russian = [russian[int(i * step)] for i in range(MAX_HISTORY_EVENTS)]
    return russian


def build_history_embed(events: list[dict]) -> discord.Embed:
    now = dt.datetime.now(TZ)
    page = f"{now.day}_{MONTHS_GEN[now.month]}"
    embed = discord.Embed(
        title=f"📜 Этот день в истории России — {now.day} {MONTHS_GEN[now.month]}",
        url=f"https://ru.wikipedia.org/wiki/{page}",
        description="Что произошло в этот день в разные годы:",
        color=COLORS["history"],
    )
    for ev in events:
        years_ago = now.year - ev["year"] if ev["year"] and ev["year"] > 0 else None
        name = f"🗓️ {ev['year']} год" + (f" ({years_ago} {years_word(years_ago)} назад)" if years_ago else "")
        value = shorten(ev["text"], 900) + (f"\n[Подробнее →]({ev['link']})" if ev["link"] else "")
        embed.add_field(name=name, value=value, inline=False)
    image = next((e["image"] for e in events if e["image"]), None)
    if image:
        embed.set_thumbnail(url=image)
    embed.set_footer(text="Источник: Википедия")
    return embed


# ──────────────────────────────── БОТ ────────────────────────────────

class NewsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        self.tree.add_command(post_now)
        await self.tree.sync()
        daily_post.start()

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def channel(self, channel_id: int) -> discord.abc.Messageable | None:
        if not channel_id:
            return None
        try:
            return self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        except discord.DiscordException as exc:
            log.error("Канал %s недоступен: %s", channel_id, exc)
            return None

    async def post_all(self):
        session = self.http_session
        news, svo = await collect_news(session)

        for kind, items, channel_id in (("news", news, NEWS_CHANNEL_ID), ("svo", svo, SVO_CHANNEL_ID)):
            channel = await self.channel(channel_id)
            if not channel:
                continue
            if not items:
                log.info("Нет новых публикаций для «%s»", kind)
                continue
            await channel.send(embed=build_digest_embed(items, kind))
            mark_sent(items)
            log.info("Отправлено «%s»: %d публикаций", kind, len(items))

        channel = await self.channel(HISTORY_CHANNEL_ID)
        if channel:
            events = await collect_history(session)
            if events:
                await channel.send(embed=build_history_embed(events))
                log.info("Отправлено «история»: %d событий", len(events))


bot = NewsBot()


@tasks.loop(time=POST_TIMES)
async def daily_post():
    try:
        await bot.post_all()
    except Exception:
        log.exception("Ошибка при ежедневной рассылке")


@daily_post.before_loop
async def before_daily_post():
    await bot.wait_until_ready()


@app_commands.command(name="post_now", description="Сразу опубликовать все сводки (для проверки)")
@app_commands.default_permissions(administrator=True)
async def post_now(interaction: discord.Interaction):
    await interaction.response.send_message("⏳ Собираю сводки…", ephemeral=True)
    await bot.post_all()
    await interaction.edit_original_response(content="✅ Готово")


@bot.event
async def on_ready():
    log.info("Бот %s запущен. Публикация каждый день в %s (МСК)", bot.user,
             ", ".join(t.strftime("%H:%M") for t in POST_TIMES))


if __name__ == "__main__":
    if not TOKEN or TOKEN.startswith("вставьте"):
        raise SystemExit("Укажите DISCORD_TOKEN в файле .env")
    bot.run(TOKEN, log_handler=None)
