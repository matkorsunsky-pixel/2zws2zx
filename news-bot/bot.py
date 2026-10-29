"""
Discord-бот: ежедневные сводки в разные каналы.

  • 📰 новости  — сводка из RSS-лент
  • 🎖️ СВО      — новости из тех же лент, отобранные по ключевым словам
                  (плюс отдельные ленты из SVO_FEEDS, если вы их укажете)
  • 📜 история  — «Этот день в истории России» (события с этой датой из Википедии)

Каналы и время настраиваются прямо в Discord командой /setup — отдельно на каждом сервере.
Запуск:  python bot.py   (в файле .env нужен только DISCORD_TOKEN)
"""

import asyncio
import calendar
import datetime as dt
import html
import json
import logging
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ─────────────────────────────── НАСТРОЙКИ ───────────────────────────────

for _stream in (sys.stdout, sys.stderr):   # кириллица в логах при любой кодировке консоли
    if _stream and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Папка программы: рядом лежат .env и data.json (и для bot.py, и для собранного bot.exe)
BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)

TOKEN = os.getenv("DISCORD_TOKEN", "")
TZ = ZoneInfo("Europe/Moscow")
DEFAULT_TIME = os.getenv("DEFAULT_TIME", "09:00")   # время публикации для новых серверов (МСК)

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

MAX_ITEMS_PER_DIGEST = 8        # сколько новостей в одной сводке (каждая — отдельным постом)
ARTICLE_TEXT_LIMIT = 2500       # сколько знаков текста статьи показывать в посте (Discord — до 4096)
MAX_AGE_HOURS = 24              # брать только публикации не старше N часов
MAX_HISTORY_EVENTS = 6          # сколько исторических событий показывать

# Слова, по которым событие из Википедии считается относящимся к истории России
# (ищутся как начала слов)
RUSSIA_KEYWORDS = [
    "росси", "русск", "русь", "руси", "ссср", "советск", "москв", "петербург", "петроград",
    "ленинград", "новгород", "киевск", "кремл", "рсфср", "пётр", "екатерин", "красной арми",
    "большевик", "сибир", "казан", "волгоград", "сталинград", "севастопол", "крым",
    "романов", "гагарин", "байконур", "кутузов", "суворов", "ломоносов", "пушкин",
    "великий княз", "великого княз", "грозн", "суздал", "тверск", "рязан", "смоленск", "полтав",
    "бородин", "куликов", "невск", "донск", "ермак", "минин", "пожарск", "годунов", "рюрик",
    "царицын", "ленин", "сталин", "хрущёв", "брежнев", "горбачёв", "ельцин", "колчак", "деникин",
    "врангел", "казак", "русск", "российск", "московск", "петровск", "владивосток", "курил",
]

DATA_FILE = BASE_DIR / "data.json"   # настройки серверов и уже отправленное
FEED_CACHE_SECONDS = 600                            # не скачивать ленты чаще раза в 10 минут

KINDS = {"news": "📰 Новости", "svo": "🎖️ Фронт (СВО)", "history": "📜 История России"}

COLORS = {"news": 0x2B6CB0, "svo": 0xC53030, "history": 0xB7791F}
MONTHS_GEN = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
USER_AGENT = "VestnikNewsBot/1.0 (Discord bot; https://github.com/matkorsunsky-pixel/2zws2zx)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("newsbot")


# ─────────────────────────── НАСТРОЙКИ СЕРВЕРОВ ───────────────────────────
# data.json: {"<id сервера>": {"channels": {"news": id, ...}, "time": "09:00",
#                              "last": "2026-10-28", "sent": [...]}}

def load_data() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


DATA = load_data()


def save_data() -> None:
    for cfg in DATA.values():
        cfg["sent"] = cfg.get("sent", [])[-3000:]
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(DATA, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA_FILE)


def guild_cfg(guild_id: int) -> dict:
    cfg = DATA.setdefault(str(guild_id), {})
    cfg.setdefault("channels", {})
    cfg.setdefault("time", DEFAULT_TIME)
    cfg.setdefault("sent", [])
    return cfg


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


_feed_cache: dict[str, tuple[float, list[dict]]] = {}


async def fetch_feed(session: aiohttp.ClientSession, source: str, url: str) -> list[dict]:
    cached = _feed_cache.get(url)
    if cached and asyncio.get_running_loop().time() - cached[0] < FEED_CACHE_SECONDS:
        return cached[1]
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
        full = e.get("yandex_full-text") or (e.get("content") or [{}])[0].get("value", "")
        items.append({
            "id": e.get("id") or link,
            "fulltext": html_to_text(full),
            "source": source,
            "title": title,
            "link": link,
            "summary": clean_html(e.get("summary", "")),
            "time": entry_time(e),
            "image": entry_image(e),
        })
    log.info("%s: получено %d записей", source, len(items))
    _feed_cache[url] = (asyncio.get_running_loop().time(), items)
    return items


def word_regex(prefixes: list[str], words: tuple | list = ()) -> re.Pattern:
    """Ищет слова, начинающиеся с prefixes, и слова words целиком."""
    parts = [re.escape(p) + r"[а-яёa-z]*" for p in prefixes] + [re.escape(w) for w in words]
    return re.compile(r"(?<![а-яёa-z])(?:" + "|".join(parts) + r")(?![а-яёa-z])", re.I)


SVO_RE = word_regex(SVO_KEYWORDS, SVO_WORDS)
RUSSIA_RE = word_regex(RUSSIA_KEYWORDS)


def is_svo(item: dict) -> bool:
    return bool(SVO_RE.search(f"{item['title']} {item['summary']}"))


# ─────────────────────────── ПОЛНЫЙ ТЕКСТ СТАТЕЙ ───────────────────────────

# Строки, которые не нужны в посте (подписки, «читайте также», подписи к фото)
_JUNK_LINE_RE = re.compile(
    r"^(читайте( также)?|смотрите( также)?|подписывайтесь|подпишитесь|фото|видео|источник|реклама|"
    r"следите за|все новости|новости по теме|по теме|©|теги?:)", re.I)


def html_to_text(raw_html: str) -> str:
    """HTML из RSS (content:encoded / yandex:full-text) в абзацы."""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", raw_html or "")
    return "\n".join(clean_html(line) for line in text.split("\n"))


def tidy_paragraphs(text: str, title: str = "") -> list[str]:
    paragraphs = []
    for para in (re.sub(r"\s+", " ", p).strip() for p in (text or "").split("\n")):
        if len(para) < 25 or _JUNK_LINE_RE.match(para) or para.rstrip(".") == title.rstrip("."):
            continue
        if para not in paragraphs:
            paragraphs.append(para)
    return paragraphs


def _extract_main_text(page_html: str) -> str:
    import trafilatura   # загружается только когда нужно
    return trafilatura.extract(page_html, include_comments=False, include_tables=False,
                               include_images=False, favor_precision=True, deduplicate=True) or ""


async def fetch_full_text(session: aiohttp.ClientSession, item: dict) -> None:
    """Добавляет в новость полный текст: из RSS, если он там есть, иначе со страницы статьи."""
    paragraphs = tidy_paragraphs(item.get("fulltext", ""), item["title"])
    if sum(map(len, paragraphs)) < 400:
        try:
            async with session.get(item["link"], timeout=aiohttp.ClientTimeout(total=20)) as resp:
                resp.raise_for_status()
                page_html = await resp.text(errors="replace")
            from_page = tidy_paragraphs(await asyncio.to_thread(_extract_main_text, page_html), item["title"])
            if sum(map(len, from_page)) > sum(map(len, paragraphs)):
                paragraphs = from_page
        except Exception as exc:
            log.warning("Не удалось загрузить текст статьи %s: %r", item["link"], exc)
    if not paragraphs and item["summary"] and item["summary"] != item["title"]:
        paragraphs = [item["summary"]]
    item["paragraphs"] = paragraphs


# ──────────────────────────── ОТБОР ГЛАВНОГО ────────────────────────────

def _title_words(title: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[а-яёa-z0-9]+", title.lower()) if len(w) > 3}


def group_stories(items: list[dict]) -> list[list[dict]]:
    """Склеивает одну и ту же новость из разных источников в одну «историю»."""
    stories: list[tuple[set[str], list[dict]]] = []
    for it in items:
        words = _title_words(it["title"])
        for story_words, story in stories:
            common = len(words & story_words)
            if words and common >= 2 and common / len(words | story_words) >= 0.3:
                story.append(it)
                story_words |= words
                break
        else:
            stories.append((set(words), [it]))
    return [story for _, story in stories]


def pick_top(items: list[dict], limit: int) -> list[dict]:
    """Главное за сутки: о чём написали больше источников, затем — свежее."""
    newest = lambda i: i["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    stories = group_stories(sorted(items, key=newest, reverse=True))
    stories.sort(key=lambda s: (len({i["source"] for i in s}), max(map(newest, s))), reverse=True)
    top = []
    for story in stories[:limit]:
        main = dict(max(story, key=lambda i: (len(i.get("fulltext", "")), bool(i["image"]))))
        main["also"] = sorted({i["source"] for i in story} - {main["source"]})
        main["duplicates"] = story
        main["image"] = main["image"] or next((i["image"] for i in story if i["image"]), None)
        top.append(main)
    top.sort(key=newest, reverse=True)
    return top


async def collect_news(session: aiohttp.ClientSession, sent: set[str]) -> tuple[list[dict], list[dict]]:
    """Возвращает (новости, новости СВО) — главное за сутки, ещё не отправленное, с полным текстом."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_HOURS)

    general = await asyncio.gather(*(fetch_feed(session, s, u) for s, u in NEWS_FEEDS.items()))
    special = await asyncio.gather(*(fetch_feed(session, s, u) for s, u in SVO_FEEDS.items()))

    def fresh(items):
        for it in items:
            if it["id"] in sent or it["link"] in sent:
                continue
            if it["time"] and it["time"] < cutoff:
                continue
            yield it

    all_general = list(fresh(i for feed in general for i in feed))
    svo = list(fresh(i for feed in special for i in feed)) + [i for i in all_general if is_svo(i)]
    news = [i for i in all_general if not (SVO_EXCLUDE_FROM_NEWS and is_svo(i))]

    news, svo = pick_top(news, MAX_ITEMS_PER_DIGEST), pick_top(svo, MAX_ITEMS_PER_DIGEST)
    limit = asyncio.Semaphore(4)

    async def load(item):
        async with limit:
            await fetch_full_text(session, item)

    await asyncio.gather(*(load(i) for i in news + svo))
    return news, svo


def mark_sent(cfg: dict, items: list[dict]) -> None:
    for main in items:
        for it in main.get("duplicates", [main]):
            cfg["sent"].append(it["id"])
            if it["link"] != it["id"]:
                cfg["sent"].append(it["link"])
    save_data()


def today_ru() -> str:
    now = dt.datetime.now(TZ)
    return f"{now.day} {MONTHS_GEN[now.month]} {now.year}"


# ──────────────────────────────── ПОСТЫ ────────────────────────────────

DIGEST_TITLES = {"news": "📰 Сводка новостей", "svo": "🎖️ Сводка с фронта"}


def article_body(paragraphs: list[str], limit: int) -> tuple[str, bool]:
    """Текст поста по целым абзацам (последний — по целым предложениям). Возвращает (текст, обрезан ли)."""
    out, total = [], 0
    for para in paragraphs:
        if total + len(para) <= limit:
            out.append(para)
            total += len(para) + 2
            continue
        part = ""
        for sentence in re.split(r"(?<=[.!?…»])\s+", para):
            if total + len(part) + len(sentence) > limit:
                break
            part += sentence + " "
        if part.strip():
            out.append(part.strip())
        return "\n\n".join(out), True
    return "\n\n".join(out), False


def build_article_embed(item: dict, kind: str, n: int, total: int) -> discord.Embed:
    paragraphs = item.get("paragraphs") or []
    body, cut = article_body(paragraphs[1:], ARTICLE_TEXT_LIMIT - len(paragraphs[0]) if paragraphs else 0)
    parts = []
    if paragraphs:
        parts.append(f"**{paragraphs[0]}**")   # лид — жирным, как в газете
    if body:
        parts.append(body)
    if cut:
        parts.append(f"*…продолжение — [в источнике]({item['link']})*")
    embed = discord.Embed(
        title=shorten(item["title"], 256),
        url=item["link"],
        description=shorten("\n\n".join(parts) or "*Текст недоступен — откройте источник.*", 4096),
        color=COLORS[kind],
        timestamp=item["time"] or dt.datetime.now(dt.timezone.utc),
    )
    embed.set_author(name=f"{DIGEST_TITLES[kind]} · {n} из {total}")
    if item["image"]:
        embed.set_image(url=item["image"])
    footer = item["source"]
    if item.get("also"):
        footer += " · также сообщают: " + ", ".join(item["also"])
    embed.set_footer(text=shorten(footer, 2048))
    return embed


def build_digest_header(items: list[dict], kind: str, links: list[str] | None = None) -> discord.Embed:
    """Шапка сводки с оглавлением. links — ссылки на посты (появляются, когда посты отправлены)."""
    lines = []
    for n, it in enumerate(items, 1):
        name = shorten(it["title"], 110)
        lines.append(f"`{n:>2}.` [{name}]({links[n - 1]})" if links else f"`{n:>2}.` {name}")
    embed = discord.Embed(
        title=f"{DIGEST_TITLES[kind]} — {today_ru()}",
        description=(f"Главное за сутки — {plural(len(items), 'материал', 'материала', 'материалов')}. "
                     "Нажмите на заголовок, чтобы перейти к новости, или листайте ниже ⬇️\n\n" + "\n".join(lines)),
        color=COLORS[kind],
    )
    sources = sorted({s for it in items for s in [it["source"], *it.get("also", [])]})
    embed.set_footer(text="Источники: " + ", ".join(sources))
    return embed


async def send_digest(channel: discord.abc.Messageable, items: list[dict], kind: str) -> None:
    header = await channel.send(embed=build_digest_header(items, kind))
    links = []
    for n, item in enumerate(items, 1):
        message = await channel.send(embed=build_article_embed(item, kind, n, len(items)))
        links.append(message.jump_url)
    await header.edit(embed=build_digest_header(items, kind, links))


# ─────────────────────────── ЭТОТ ДЕНЬ В ИСТОРИИ ───────────────────────────

def is_russian_history(text: str) -> bool:
    return bool(RUSSIA_RE.search(text))


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(1, "год", "года", "лет") -> "1 год", plural(5, ...) -> "5 лет"."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


# Значимость события: чем выше балл, тем вероятнее оно станет главным событием дня.
# (начала слов, балл)
SIGNIFICANCE = [
    # переломные события
    ("революц", 8), ("битв", 8), ("сражени", 8), ("побед", 6), ("капитуляц", 7), ("основан", 6),
    ("коронац", 7), ("венча", 5), ("престол", 6), ("отречени", 7), ("присоедин", 6), ("воссоедин", 6),
    ("провозглаш", 6), ("манифест", 6), ("конституц", 6), ("отмен", 5), ("крепостн", 6),
    ("космос", 7), ("космическ", 6), ("спутник", 6), ("гагарин", 8), ("полёт", 4),
    ("войн", 5), ("восстани", 5), ("освобожд", 5), ("штурм", 5), ("взяти", 5), ("взят", 4),
    ("договор", 5), ("мирн", 4), ("перемири", 5), ("указ", 4), ("учрежд", 4), ("реформ", 4),
    ("независимост", 5), ("распад", 6), ("образован", 4), ("создан", 3), ("открыт", 3),
    ("крещени", 7), ("собор", 3), ("ополчени", 6), ("флот", 3), ("армия", 3), ("арми", 3),
    ("император", 3), ("импери", 3), ("царь", 4), ("цар", 3), ("княз", 3), ("ссср", 3),
    ("покорени", 4), ("экспедици", 4), ("первы", 2), ("впервые", 3), ("рекорд", 2),
]
# Мелочи, которые не должны становиться «главным событием»
TRIVIA = [("матч", -6), ("чемпионат", -5), ("футбол", -6), ("хоккей", -4), ("фильм", -5),
          ("альбом", -6), ("сингл", -6), ("премьер", -3), ("телесериал", -6), ("выпуск", -3),
          ("серия", -3), ("игр", -2), ("компани", -2), ("магазин", -4)]

_SIGNIFICANCE_RE = [(word_regex([w]), score) for w, score in SIGNIFICANCE + TRIVIA]
_SERVICE_TITLE_RE = re.compile(r"^(\d{1,4}( год)?( до н\. э\.)?|[IVXLC]+ век.*|\d{1,2} \w+)$")
HISTORY_COLOR = 0x8B1A1A   # тёмно-красный, «летописный»


def significance(event: dict) -> int:
    text = event["search"]
    russia = {m.lower() for m in RUSSIA_RE.findall(text)}
    score = min(len(russia), 3) * 4                           # упор на Россию
    for regex, points in _SIGNIFICANCE_RE:
        if regex.search(text):
            score += points
    score += min(len(event["text"]) // 60, 4)                 # подробно описанное событие
    title = main_title(event)
    if title:
        score += 3                                            # есть статья для рассказа
        if any(r.search(title) for r, p in _SIGNIFICANCE_RE if p > 0):
            score += 4                                        # статья о самом событии («… битва»)
    return score


def main_title(event: dict) -> str | None:
    """Статья о самом событии: сначала та, в названии которой есть «битва», «договор» и т.п."""
    titles = [t for t in event.get("titles", []) if not _SERVICE_TITLE_RE.match(t)]
    if not titles:
        return None
    return max(titles, key=lambda t: (sum(p for r, p in _SIGNIFICANCE_RE if r.search(t) and p > 0)
                                      + 2 * bool(RUSSIA_RE.search(t)), -titles.index(t)))


def strip_wiki(text: str) -> str:
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip(" ;:")


async def history_from_api(session: aiohttp.ClientSession, day: dt.date) -> list[dict]:
    """Список событий дня из REST API Википедии."""
    url = f"https://ru.wikipedia.org/api/rest_v1/feed/onthisday/events/{day.month:02d}/{day.day:02d}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        resp.raise_for_status()
        data = await resp.json()
    events = []
    for ev in data.get("events", []):
        titles = [p.get("normalizedtitle") or p.get("title", "").replace("_", " ") for p in ev.get("pages", [])]
        events.append({"year": ev.get("year"), "text": ev.get("text", ""), "titles": titles,
                       "search": f"{ev.get('text', '')} {' '.join(titles)}"})
    return events


async def history_from_page(session: aiohttp.ClientSession, day: dt.date) -> list[dict]:
    """Запасной вариант: раздел «События» статьи «29 октября» (вики-разметка, вместе со ссылками)."""
    params = {"action": "parse", "page": f"{day.day} {MONTHS_GEN[day.month]}", "prop": "wikitext",
              "format": "json", "formatversion": "2", "redirects": "1"}
    async with session.get("https://ru.wikipedia.org/w/api.php", params=params,
                           timeout=aiohttp.ClientTimeout(total=20)) as resp:
        resp.raise_for_status()
        data = await resp.json()
    wikitext = data.get("parse", {}).get("wikitext", "")
    section = re.search(r"==\s*События\s*==(.*?)(?:\n==[^=]|\Z)", wikitext, re.S)
    if not section:
        return []
    events, year = [], None
    for line in section.group(1).splitlines():
        if not line.startswith("*"):
            continue
        depth = len(line) - len(line.lstrip("*"))
        body = line.lstrip("*").strip()
        m = re.match(r"^\[\[(\d{1,4})(?: год)?(?:\|[^\]]*)?\]\]\s*(?:год)?\s*[—–:-]?\s*(.*)$", body)
        if m:
            year, body = int(m.group(1)), m.group(2)
        elif depth == 1:
            m = re.match(r"^(\d{1,4})\s*(?:год)?\s*[—–:-]\s*(.*)$", body)
            if not m:
                continue
            year, body = int(m.group(1)), m.group(2)
        text = strip_wiki(body)
        if not year or len(text) < 15:
            continue
        titles = [t.strip() for t in re.findall(r"\[\[([^|\]#]+)", body)]
        events.append({"year": year, "text": text[0].upper() + text[1:], "titles": titles,
                       "search": f"{text} {' '.join(titles)}"})
    return events


async def fetch_article(session: aiohttp.ClientSession, title: str) -> dict:
    """Начало статьи (до ~1200 знаков) и её главная картинка."""
    params = {"action": "query", "prop": "extracts|pageimages|info", "titles": title, "redirects": "1",
              "exintro": "1", "explaintext": "1", "piprop": "thumbnail", "pithumbsize": "1000",
              "inprop": "url", "format": "json", "formatversion": "2"}
    async with session.get("https://ru.wikipedia.org/w/api.php", params=params,
                           timeout=aiohttp.ClientTimeout(total=20)) as resp:
        resp.raise_for_status()
        page = (await resp.json())["query"]["pages"][0]
    return {"title": page.get("title", title), "extract": page.get("extract", ""),
            "url": page.get("fullurl") or f"https://ru.wikipedia.org/wiki/{title.replace(' ', '_')}",
            "image": page.get("thumbnail", {}).get("source")}


def tidy_extract(text: str, limit: int = 1200) -> str:
    """Читабельный текст: без ударений и пустых скобок, по целым предложениям, абзацами."""
    text = text.replace("́", "")
    text = re.sub(r"\(\s*[;,]?\s*\)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    paragraphs, total = [], 0
    for para in (p.strip() for p in text.split("\n") if p.strip()):
        if total + len(para) > limit:
            sentences = re.split(r"(?<=[.!?…])\s+", para)
            part = ""
            for s in sentences:
                if total + len(part) + len(s) > limit:
                    break
                part += s + " "
            if part.strip():
                paragraphs.append(part.strip())
            break
        paragraphs.append(para)
        total += len(para)
    if paragraphs:  # выделить термин в начале статьи: «Полтавская битва — …»
        first = paragraphs[0]
        m = re.match(r"^(.{3,90}?)\s+[—–-]\s+", first)
        if m:
            paragraphs[0] = f"**{m.group(1)}** — {first[m.end():]}"
    return "\n\n".join(paragraphs)


_history_cache: dict[dt.date, dict] = {}
history_error = ""   # почему не удалось получить события — показывается в ответе /post_now


async def collect_history(session: aiohttp.ClientSession) -> dict | None:
    """{"main": главное событие дня со статьёй, "others": ещё несколько событий}."""
    global history_error
    day = dt.datetime.now(TZ).date()
    if day in _history_cache:
        return _history_cache[day]

    events, errors = [], []
    for source in (history_from_api, history_from_page):
        try:
            found = await source(session, day)
        except Exception as exc:
            errors.append(f"{source.__name__}: {exc!r}")
            log.warning("Википедия (%s): %r", source.__name__, exc)
            continue
        log.info("Википедия (%s): %d событий", source.__name__, len(found))
        if len(found) > len(events):
            events = found
        if sum(is_russian_history(e["search"]) for e in found) >= 3:
            break
    if not events:
        history_error = "; ".join(errors) or "Википедия не вернула событий"
        return None
    history_error = ""

    # упор на Россию: берём только российские события; если их нет совсем — любые
    pool = [e for e in events if is_russian_history(e["search"])] or events
    ranked = sorted(pool, key=significance, reverse=True)
    log.info("Главные кандидаты: %s", "; ".join(f"{e['year']} ({significance(e)})" for e in ranked[:5]))

    main = dict(ranked[0])
    title = main_title(main)
    main["article"] = None
    if title:
        try:
            main["article"] = await fetch_article(session, title)
        except Exception as exc:
            log.warning("Не удалось загрузить статью «%s»: %r", title, exc)

    others = [e for e in ranked[1:] if significance(e) > 0][:MAX_HISTORY_EVENTS - 1]
    others.sort(key=lambda e: e["year"] or 0)
    result = {"main": main, "others": others, "day": day}
    _history_cache.clear()
    _history_cache[day] = result
    return result


def year_label(year: int) -> str:
    return f"{-year} год до н. э." if year < 0 else f"{year} год"


def build_history_embed(history: dict) -> discord.Embed:
    day, main, article = history["day"], history["main"], history["main"]["article"]
    year = main["year"] or 0
    date_text = f"{day.day} {MONTHS_GEN[day.month]}"
    headline = main["text"].rstrip(".")

    parts = []
    if article and article["extract"]:
        parts.append(tidy_extract(article["extract"]))
        parts.append(f"📖 [Читать статью «{article['title']}» полностью]({article['url']})")
    else:
        parts.append(f"*{main['text']}*")

    embed = discord.Embed(
        title=shorten(f"{year_label(year)} · {headline}", 256),
        url=article["url"] if article else f"https://ru.wikipedia.org/wiki/{day.day}_{MONTHS_GEN[day.month]}",
        description=shorten("\n\n".join(parts), 4000),
        color=HISTORY_COLOR,
    )
    embed.set_author(name=f"📜 Этот день в истории России · {date_text}")
    embed.add_field(name="🗓️ Дата", value=f"{date_text} {year} г." if year > 0 else date_text, inline=True)
    if 0 < year < day.year:
        embed.add_field(name="⏳ Прошло", value=plural(day.year - year, "год", "года", "лет"), inline=True)

    if history["others"]:
        lines = [f"**{e['year']}** — {shorten(e['text'], 170)}" for e in history["others"]]
        value = ""
        for line in lines:
            if len(value) + len(line) + 1 > 1024:
                break
            value += line + "\n"
        embed.add_field(name="🕯️ Также в этот день", value=value.strip(), inline=False)

    if article and article["image"]:
        embed.set_image(url=article["image"])
    embed.set_footer(text="Историческая летопись · по материалам Википедии")
    return embed


# ──────────────────────────────── БОТ ────────────────────────────────

BOT_PERMISSIONS = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)


class NewsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        self.tree.on_error = on_command_error
        await self.tree.sync()   # глобальных команд нет: убираем старые, команды живут на каждом сервере
        scheduler.start()

    async def register_commands(self, guild: discord.Guild) -> None:
        """Команды, записанные прямо на сервер, появляются в Discord сразу."""
        for cmd in (setup_group, post_now, history_cmd, invite_cmd):
            self.tree.add_command(cmd, guild=guild, override=True)
        try:
            synced = await self.tree.sync(guild=guild)
            log.info("Сервер «%s»: команды готовы (%s)", guild.name, ", ".join("/" + c.name for c in synced))
        except discord.Forbidden:
            log.error("Сервер «%s»: бот добавлен без права на команды. Добавьте его заново по ссылке-приглашению "
                      "ниже (в ней должно быть applications.commands)", guild.name)

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    def invite_url(self) -> str:
        return discord.utils.oauth_url(self.user.id, permissions=BOT_PERMISSIONS,
                                       scopes=("bot", "applications.commands"))

    async def post_guild(self, guild: discord.Guild, kinds=tuple(KINDS)) -> list[str]:
        """Публикует сводки на одном сервере. Возвращает отчёт для пользователя."""
        cfg = guild_cfg(guild.id)
        report = []
        targets = {k: guild.get_channel(cfg["channels"].get(k, 0)) for k in kinds}

        if targets.get("news") or targets.get("svo"):
            news, svo = await collect_news(self.http_session, set(cfg["sent"]))
            for kind, items in (("news", news), ("svo", svo)):
                channel = targets.get(kind)
                if not channel:
                    continue
                if not items:
                    report.append(f"{KINDS[kind]}: новых публикаций нет")
                    continue
                await send_digest(channel, items, kind)
                mark_sent(cfg, items)
                report.append(f"{KINDS[kind]}: {plural(len(items), 'публикация', 'публикации', 'публикаций')} → {channel.mention}")

        channel = targets.get("history")
        if channel:
            history = await collect_history(self.http_session)
            if history:
                await channel.send(embed=build_history_embed(history))
                report.append(f"{KINDS['history']}: {history['main']['year']} — "
                              f"{shorten(history['main']['text'], 80)} → {channel.mention}")
            else:
                report.append(f"{KINDS['history']}: не удалось получить события ({history_error})")

        for kind in kinds:
            if kind in cfg["channels"] and not targets.get(kind):
                report.append(f"{KINDS[kind]}: канал удалён или недоступен — задайте заново через /setup channel")
        log.info("Сервер %s: %s", guild.name, "; ".join(report) or "каналы не настроены")
        return report


bot = NewsBot()


def parse_time(text: str) -> tuple[int, int]:
    h, m = text.strip().replace(".", ":").split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError
    return h, m


@tasks.loop(minutes=1)
async def scheduler():
    """Раз в минуту проверяет, не пора ли публиковать. Если бот был выключен — догонит."""
    now = dt.datetime.now(TZ)
    today = now.date().isoformat()
    for guild in bot.guilds:
        cfg = DATA.get(str(guild.id))
        if not cfg or not cfg.get("channels") or cfg.get("last") == today:
            continue
        if (now.hour, now.minute) < parse_time(cfg.get("time", DEFAULT_TIME)):
            continue
        cfg["last"] = today
        save_data()
        try:
            await bot.post_guild(guild)
        except Exception:
            log.exception("Ошибка рассылки на сервере %s", guild.name)


@scheduler.before_loop
async def before_scheduler():
    await bot.wait_until_ready()


# ───────────────────────────── КОМАНДЫ ─────────────────────────────

KIND_CHOICES = [app_commands.Choice(name=name, value=kind) for kind, name in KINDS.items()]

setup_group = app_commands.Group(
    name="setup", description="Настройка ежедневных сводок",
    default_permissions=discord.Permissions(manage_guild=True), guild_only=True,
)


@setup_group.command(name="channel", description="В какой канал отправлять сводку")
@app_commands.describe(kind="Какую сводку", channel="Текстовый канал")
@app_commands.rename(kind="сводка", channel="канал")
@app_commands.choices(kind=KIND_CHOICES)
async def setup_channel(interaction: discord.Interaction, kind: app_commands.Choice[str],
                        channel: discord.TextChannel):
    perms = channel.permissions_for(interaction.guild.me)
    missing = [name for name, ok in (("Просматривать каналы", perms.view_channel),
                                     ("Отправлять сообщения", perms.send_messages),
                                     ("Встраивать ссылки", perms.embed_links)) if not ok]
    if missing:
        await interaction.response.send_message(
            f"❌ У бота нет прав в {channel.mention}: {', '.join(missing)}.\n"
            "Шестерёнка у канала («Изменить канал») → «Права доступа» → добавьте бота и разрешите эти права.", ephemeral=True)
        return

    cfg = guild_cfg(interaction.guild_id)
    if not cfg["channels"] and "last" not in cfg:
        # первая настройка после времени публикации — не слать сразу, начать с завтра
        now = dt.datetime.now(TZ)
        if (now.hour, now.minute) >= parse_time(cfg["time"]):
            cfg["last"] = now.date().isoformat()
    cfg["channels"][kind.value] = channel.id
    save_data()
    await interaction.response.send_message(
        f"✅ {kind.name} будет приходить в {channel.mention} каждый день в {cfg['time']} (МСК).\n"
        "Проверить сразу: `/post_now`", ephemeral=True)


@setup_group.command(name="time", description="Во сколько публиковать сводки (по Москве)")
@app_commands.describe(time="Время в формате ЧЧ:ММ, например 09:00")
@app_commands.rename(time="время")
async def setup_time(interaction: discord.Interaction, time: str):
    try:
        h, m = parse_time(time)
    except ValueError:
        await interaction.response.send_message("❌ Укажите время как ЧЧ:ММ, например `09:00`", ephemeral=True)
        return
    cfg = guild_cfg(interaction.guild_id)
    cfg["time"] = f"{h:02d}:{m:02d}"
    now = dt.datetime.now(TZ)
    if (now.hour, now.minute) >= (h, m):
        cfg["last"] = now.date().isoformat()   # сегодняшнее время уже прошло — со следующего дня
    else:
        cfg.pop("last", None)
    save_data()
    await interaction.response.send_message(f"✅ Сводки будут приходить каждый день в {cfg['time']} (МСК)",
                                            ephemeral=True)


@setup_group.command(name="off", description="Выключить одну из сводок")
@app_commands.describe(kind="Какую сводку выключить")
@app_commands.rename(kind="сводка")
@app_commands.choices(kind=KIND_CHOICES)
async def setup_off(interaction: discord.Interaction, kind: app_commands.Choice[str]):
    cfg = guild_cfg(interaction.guild_id)
    cfg["channels"].pop(kind.value, None)
    save_data()
    await interaction.response.send_message(f"🔕 {kind.name}: выключено", ephemeral=True)


@setup_group.command(name="status", description="Текущие настройки сводок на этом сервере")
async def setup_status(interaction: discord.Interaction):
    cfg = guild_cfg(interaction.guild_id)
    embed = discord.Embed(title="⚙️ Настройки сводок", color=COLORS["news"])
    for kind, name in KINDS.items():
        channel = interaction.guild.get_channel(cfg["channels"].get(kind, 0))
        embed.add_field(name=name, value=channel.mention if channel else "— не настроено", inline=False)
    embed.add_field(name="⏰ Время", value=f"{cfg['time']} (МСК)", inline=True)
    embed.add_field(name="Последняя рассылка", value=cfg.get("last", "ещё не было"), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="post_now", description="Сразу опубликовать сводки (для проверки)")
@app_commands.describe(kind="Какую сводку (по умолчанию — все)")
@app_commands.rename(kind="сводка")
@app_commands.choices(kind=KIND_CHOICES)
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def post_now(interaction: discord.Interaction, kind: app_commands.Choice[str] | None = None):
    cfg = guild_cfg(interaction.guild_id)
    if not cfg["channels"]:
        await interaction.response.send_message(
            "Сначала укажите каналы: `/setup channel`", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    report = await bot.post_guild(interaction.guild, (kind.value,) if kind else tuple(KINDS))
    await interaction.followup.send("\n".join(report) or "Для этой сводки канал не настроен", ephemeral=True)


@app_commands.command(name="history", description="Что было в этот день в истории России")
async def history_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    history = await collect_history(bot.http_session)
    if history:
        await interaction.followup.send(embed=build_history_embed(history))
    else:
        await interaction.followup.send("Не удалось получить события, попробуйте позже.")


@app_commands.command(name="invite", description="Ссылка, чтобы добавить бота на другой сервер")
async def invite_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"🔗 {bot.invite_url()}", ephemeral=True)


# ───────────────────────────── СОБЫТИЯ ─────────────────────────────

WELCOME = (
    "👋 Привет! Я буду каждый день присылать сводки новостей, СВО и «Этот день в истории России».\n\n"
    "**Настройка (нужно право «Управлять сервером»).** Наберите `/` и выберите команду из списка:\n"
    "1. `/setup channel` → выберите сводку и канал (повторите для каждой из трёх)\n"
    "2. `/setup time` → время публикации по Москве (по умолчанию {time})\n"
    "3. `/post_now` → проверить прямо сейчас\n\n"
    "`/setup status` — текущие настройки, `/setup off` — выключить сводку, `/history` — история дня."
)


@bot.event
async def on_ready():
    log.info("Бот %s запущен на %d серверах", bot.user, len(bot.guilds))
    for guild in bot.guilds:
        await bot.register_commands(guild)
    log.info("Ссылка-приглашение: %s", bot.invite_url())
    log.info("Готово. Наберите в Discord «/setup» и выберите команду из списка.")


@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command):
    log.info("Команда /%s от %s на сервере «%s» выполнена", command.qualified_name, interaction.user,
             interaction.guild.name if interaction.guild else "-")


async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.error("Ошибка команды: %s", error, exc_info=error)
    text = "❌ У вас нет прав для этой команды." if isinstance(error, app_commands.MissingPermissions) \
        else f"❌ Ошибка: {error}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.DiscordException:
        pass


@bot.event
async def on_guild_join(guild: discord.Guild):
    log.info("Добавлен на сервер %s", guild.name)
    await bot.register_commands(guild)
    channel = guild.system_channel
    if channel is None or not channel.permissions_for(guild.me).send_messages:
        channel = next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
    if channel:
        try:
            await channel.send(WELCOME.format(time=DEFAULT_TIME))
        except discord.DiscordException:
            pass


@bot.event
async def on_guild_remove(guild: discord.Guild):
    if DATA.pop(str(guild.id), None) is not None:
        save_data()


def ask_token() -> str:
    """Первый запуск: спросить токен и сохранить его в .env рядом с программой."""
    print("Токен бота не найден.\n"
          "Developer Portal → ваше приложение → «Бот» → «Сбросить токен» → «Копировать».")
    token = input("Вставьте токен (ПКМ или Ctrl+V) и нажмите Enter: ").strip().strip('"')
    if token:
        ENV_FILE.write_text(f"DISCORD_TOKEN={token}\nDEFAULT_TIME={DEFAULT_TIME}\n", encoding="utf-8")
        print(f"Токен сохранён в {ENV_FILE}")
    return token


def selftest() -> None:
    """NewsBot.exe --selftest: проверка, что в сборку попало всё нужное для полного текста статей."""
    sample = "<html><body><article>" + "<p>Проверочный абзац о том, что бот умеет читать статьи целиком.</p>" * 5 \
             + "</article></body></html>"
    text = _extract_main_text(sample)
    ZoneInfo("Europe/Moscow")
    print("SELFTEST OK" if "Проверочный абзац" in text else "SELFTEST FAIL", len(text))


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    token = TOKEN
    if not token or token.startswith("вставьте"):
        token = ask_token()
    if not token:
        raise SystemExit("Токен не указан")
    try:
        bot.run(token, log_handler=None)
    except aiohttp.ClientConnectorError:
        raise SystemExit("Нет связи с Discord. Проверьте интернет; в России Discord открывается только через VPN.")
    except discord.LoginFailure:
        ENV_FILE.unlink(missing_ok=True)
        raise SystemExit("Discord не принял токен. Файл .env удалён — запустите снова и вставьте новый токен.")


if __name__ == "__main__":
    try:
        main()
    except (SystemExit, KeyboardInterrupt, Exception) as exc:
        if isinstance(exc, KeyboardInterrupt):
            pass
        elif not isinstance(exc, SystemExit):
            log.exception("Бот остановлен из-за ошибки")
        elif exc.code not in (None, 0):
            print(exc.code)
        if getattr(sys, "frozen", False):   # окно .exe не закрывается само, чтобы было видно ошибку
            try:
                input("Нажмите Enter, чтобы закрыть окно…")
            except EOFError:
                pass
