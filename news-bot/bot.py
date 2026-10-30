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
import io
import json
import logging
import os
import re
import struct
import zlib
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import commands
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
# Кто может пользоваться командами бота. Владелец приложения в Developer Portal — всегда;
# дополнительно можно перечислить ID через запятую в .env: OWNER_IDS=123,456
EXTRA_OWNER_IDS = {int(x) for x in re.findall(r"\d+", os.getenv("OWNER_IDS", ""))}
TZ = ZoneInfo("Europe/Moscow")

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

MAX_ITEMS_PER_DIGEST = 12       # сколько новостей в одной сводке (всё — одним сообщением)
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
# data.json: {"<id сервера>": {"channels": {"news": id, ...},
#                              "sent": [ссылки], "sent_titles": [[когда, заголовок], ...],
#                              "history_done": {"date": "2026-10-29", "events": [...]},
#                              "last_post": {"news": "29.10 09:00", ...}}}
SENT_TITLES_DAYS = 3   # сколько дней помнить заголовки, чтобы не повторять ту же новость из другого источника

def load_data() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


DATA = load_data()


def save_data() -> None:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=SENT_TITLES_DAYS)).isoformat()
    for cfg in DATA.values():
        cfg["sent"] = cfg.get("sent", [])[-3000:]
        cfg["sent_titles"] = [t for t in cfg.get("sent_titles", []) if t[0] >= cutoff]
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(DATA, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA_FILE)


def guild_cfg(guild_id: int) -> dict:
    cfg = DATA.setdefault(str(guild_id), {})
    cfg.setdefault("channels", {})
    cfg.pop("time", None)        # от прежней версии с расписанием
    cfg.pop("last", None)
    cfg.setdefault("sent", [])
    cfg.setdefault("sent_titles", [])
    cfg.setdefault("last_post", {})
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


def same_story(a: set[str], b: set[str]) -> bool:
    common = len(a & b)
    return bool(a) and common >= 2 and common / len(a | b) >= 0.3


def group_stories(items: list[dict]) -> list[list[dict]]:
    """Склеивает одну и ту же новость из разных источников в одну «историю»."""
    stories: list[tuple[set[str], list[dict]]] = []
    for it in items:
        words = _title_words(it["title"])
        for story_words, story in stories:
            if same_story(words, story_words):
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


async def collect_news(session: aiohttp.ClientSession, sent: set[str],
                       sent_titles: list[str] = ()) -> tuple[list[dict], list[dict]]:
    """Возвращает (новости, новости СВО) — главное за сутки с полным текстом.
    Пропускает всё, что уже было в прошлых сводках: те же ссылки и ту же новость из других источников."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_HOURS)
    told = [_title_words(t) for t in sent_titles]

    general = await asyncio.gather(*(fetch_feed(session, s, u) for s, u in NEWS_FEEDS.items()))
    special = await asyncio.gather(*(fetch_feed(session, s, u) for s, u in SVO_FEEDS.items()))

    def fresh(items):
        for it in items:
            if it["id"] in sent or it["link"] in sent:
                continue
            if it["time"] and it["time"] < cutoff:
                continue
            words = _title_words(it["title"])
            if any(same_story(words, t) for t in told):
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
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for main in items:
        for it in main.get("duplicates", [main]):
            cfg["sent"].append(it["id"])
            if it["link"] != it["id"]:
                cfg["sent"].append(it["link"])
            cfg["sent_titles"].append([now, it["title"]])
    save_data()


def today_ru() -> str:
    now = dt.datetime.now(TZ)
    return f"{now.day} {MONTHS_GEN[now.month]} {now.year}"


# ──────────────────────────────── ПОСТЫ ────────────────────────────────
# Сводка — одно сообщение: «📌 Главное» и пронумерованные разделы по категориям.
# Discord ограничивает размер сообщения в байтах, а русская буква — 2 байта. Поэтому длина пересказов
# подбирается под этот предел, а если Discord всё равно отказал (ошибка 413) — бот ужимает текст и пробует снова.

DIGEST_TITLES = {"news": "☀️ Главное за сутки", "svo": "⚔️ Сводка с фронта"}
EMBED_BYTES = 5800       # начальный предел текста рамки в байтах; при отказе Discord бот снижает его сам
MIN_EMBED_BYTES = 2000


def embed_size(embed: discord.Embed) -> int:
    """Размер текста рамки в байтах UTF-8 — так его меряет Discord."""
    parts = [embed.title, embed.description, embed.author.name, embed.footer.text]
    parts += [x for f in embed.fields for x in (f.name, f.value)]
    return sum(len(p.encode("utf-8")) for p in parts if p)

# Разделы сводки в порядке показа: (название, начала слов). Новость без совпадений — в последний раздел.
SECTIONS = {
    "news": [
        ("🏛️ Политика", ["путин", "кремл", "госдум", "правительств", "министр", "президент", "выбор",
                         "закон", "депутат", "совфед", "партия", "губернатор", "мэр", "песков"]),
        ("💰 Экономика", ["рубл", "курс", "нефт", "газпром", "банк", "цб", "инфляц", "бирж", "экономик",
                          "бюджет", "налог", "ценов", "подорож", "зарплат", "пенси", "ипотек", "кредит", "санкци"]),
        ("🌍 В мире", ["сша", "китай", "европ", "евросоюз", "нато", "трамп", "израил", "иран", "германи",
                       "франц", "британ", "оон", "турци", "индия", "япони", "вашингтон", "пекин"]),
        ("🚨 Происшествия", ["пожар", "авари", "дтп", "погиб", "задержан", "судебн", "приговор", "взрыв",
                             "крушени", "мошенн", "убийств", "уголовн"]),
        ("🚀 Наука и технологии", ["учён", "наук", "космос", "космическ", "роскосмос", "ракет", "спутник",
                                   "технолог", "интернет", "нейросет", "искусственн", "открыти"]),
        ("🎭 Культура", ["фильм", "театр", "музе", "выставк", "концерт", "писател", "режиссёр", "артист", "книг"]),
        ("⚽ Спорт", ["матч", "чемпион", "футбол", "хоккей", "олимпи", "спортсмен", "турнир", "сборн"]),
        ("🏙️ Общество", []),
    ],
    "svo": [
        ("🗺️ Обстановка на фронте", ["направлени", "освобожд", "наступлени", "атак", "штурм", "позици",
                                    "населённ", "продвину", "контрол", "отразил"]),
        ("🎯 Удары и ПВО", ["удар", "пво", "беспилотник", "бпла", "ракет", "сбит", "сбил", "перехват",
                           "поражен", "дрон"]),
        ("🏘️ Приграничье", ["белгород", "курск", "брянск", "приграничн", "эвакуац", "обстрел"]),
        ("🛡️ Тыл и оснащение", ["техник", "бронетехник", "танк", "уралвагон", "поставк", "оснащ", "вооружени", "производств", "контракт",
                               "мобилизац", "доброволь", "призыв", "военнослужащ", "ветеран"]),
        ("🌐 Переговоры и политика", ["переговор", "мирн", "перемири", "обмен", "пленн", "зеленск",
                                      "трамп", "нато", "запад", "санкци", "путин", "кремл"]),
        ("📋 Другое", []),
    ],
}
_SECTION_RES = {kind: [(name, word_regex(words) if words else None) for name, words in sections]
                for kind, sections in SECTIONS.items()}
_SPEECH_RE = re.compile(r"(заяв|сказа|отмети|подчеркну|сообщи|пояснил|добави|уверен|признал)", re.I)
_QUOTE_RE = re.compile(r"«([^«»]{50,280})»")


def section_of(item: dict, kind: str) -> str:
    text = f"{item['title']} {item['title']} {' '.join((item.get('paragraphs') or [])[:2])}"
    scores = [(len(regex.findall(text)), -i, name) for i, (name, regex) in enumerate(_SECTION_RES[kind]) if regex]
    best = max(scores)
    return best[2] if best[0] else _SECTION_RES[kind][-1][0]


def pull_quote(item: dict) -> tuple[str, str, str] | None:
    """Самая яркая прямая речь из статьи: (цитата, кто сказал, абзац с ней)."""
    best = None
    for para in item.get("paragraphs") or []:
        for m in _QUOTE_RE.finditer(para):
            score = len(m.group(1)) + (200 if _SPEECH_RE.search(para) else 0)
            if not best or score > best[0]:
                after = re.match(r"^[,.]?\s*[—–-]\s*([^.«]{5,160})", para[m.end():])
                speaker = after.group(1).strip() if after else ""
                speaker = re.sub(r"^\w+(ся)?\s+(?=\w)", "", speaker) if _SPEECH_RE.match(speaker) else speaker
                before = re.search(r"([А-ЯЁ][^.:«»]{2,80}):\s*$", para[:m.start()])
                speaker = speaker or (before.group(1).strip() if before else "")
                best = (score, m.group(1).strip(), speaker, para)
    return best[1:] if best else None


def split_long(paragraphs: list[str], size: int = 550) -> list[str]:
    """Длинные абзацы режутся пополам по предложению — так текст легче читать с экрана."""
    out = []
    for para in paragraphs:
        while len(para) > size:
            sentences = re.split(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«])", para)
            if len(sentences) < 2:
                break
            head = ""
            while sentences and len(head) + len(sentences[0]) < len(para) / 2 + 80:
                head += sentences.pop(0) + " "
            if not head or not sentences:
                break
            out.append(head.strip())
            para = " ".join(sentences)
        out.append(para)
    return out


def article_body(paragraphs: list[str], limit: int) -> tuple[str, bool]:
    """Текст по целым абзацам (последний — по целым предложениям). Возвращает (текст, обрезан ли)."""
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


def gist(item: dict, limit: int) -> str:
    """Суть новости в limit знаков: первые предложения статьи одним абзацем."""
    sentences = [s for p in item.get("paragraphs") or [] for s in re.split(r"(?<=[.!?…])\s+", p)]
    text = ""
    for s in sentences:
        if text and len(text) + len(s) + 1 > limit:
            break
        text += (" " if text else "") + s
    return shorten(text, limit) if text else ""


def reading_minutes(paragraphs: list[str]) -> int:
    return max(1, round(sum(len(p.split()) for p in paragraphs) / 180))


def sources_line(item: dict) -> str:
    return ", ".join([item["source"], *item.get("also", [])])


def build_digest_embed(items: list[dict], kind: str, limit: int = EMBED_BYTES) -> tuple[discord.Embed, list[dict]]:
    """Рамка сводки и список новостей, которые в неё поместились."""
    top, rest = items[0], items[1:]
    grouped: dict[str, list[dict]] = {}
    for it in rest:
        grouped.setdefault(section_of(it, kind), []).append(it)
    order = [name for name, _ in SECTIONS[kind] if name in grouped]

    hour = dt.datetime.now(TZ).hour
    hello = "Доброе утро" if 5 <= hour < 12 else "Добрый день" if hour < 18 else "Добрый вечер"
    intro = {"news": f"{hello}! Главное, что произошло за сутки, — в одном сообщении.",
             "svo": "Главные события в зоне СВО за сутки — в одном сообщении."}[kind]
    quote = next(((q, it) for it in items if (q := pull_quote(it)) and q[1]), None) \
        or next(((q, it) for it in items if (q := pull_quote(it))), None)

    def build(top_len: int, item_len: int) -> discord.Embed:
        embed = discord.Embed(
            title=f"{DIGEST_TITLES[kind]} · {today_ru()}",
            description=(f"{intro}\n\n**📌 Главное: [{top['title']}]({top['link']})**\n"
                         f"{gist(top, top_len)}\n*— {sources_line(top)}*"),
            color=COLORS[kind],
            timestamp=dt.datetime.now(dt.timezone.utc),
        )
        for n, name in enumerate(order, 1):
            entries = []
            for it in grouped[name]:
                text = gist(it, item_len)
                entries.append(f"▸ **[{shorten(it['title'], 150)}]({it['link']})**"
                               + (f"\n{text}" if text and text != it["title"] else "")
                               + f"\n*— {sources_line(it)}*")
            chunks = [""]                                     # блок Discord — до 1024 знаков,
            for entry in entries:                             # длинный раздел продолжается в следующем
                entry = shorten(entry, 1024)
                if chunks[-1] and len(chunks[-1]) + len(entry) + 2 > 1024:
                    chunks.append("")
                chunks[-1] += ("\n\n" if chunks[-1] else "") + entry
            for i, chunk in enumerate(chunks):
                embed.add_field(name=f"{n}. {name}" if i == 0 else "\u200b", value=chunk, inline=False)
        if quote:
            (text, speaker, _), it = quote
            embed.add_field(name="💬 Цитата дня",
                            value=f"> *«{shorten(text, 300)}»*\n> — {speaker or it['source']}", inline=False)
        if top["image"]:
            embed.set_thumbnail(url=top["image"])      # фото — в углу, внизу линия единой ширины
        minutes = max(1, round(len(embed) / 1100))
        embed.set_footer(text=f"{plural(len(items), 'новость', 'новости', 'новостей')} · ⏱ {minutes} мин чтения · "
                              "заголовки кликабельны")
        return embed

    top_len, item_len = 650, 420
    embed = build(top_len, item_len)
    while embed_size(embed) > limit and item_len > 60:     # не влезает в сообщение — короче пересказы
        item_len -= 30
        top_len = max(250, top_len - 30)
        embed = build(top_len, item_len)
    while embed_size(embed) > limit and len(rest) > 1:     # всё ещё велико — убрать наименее важные новости
        dropped = min(rest, key=lambda i: (len({i["source"], *i.get("also", [])}), i["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc)))
        rest.remove(dropped)
        grouped = {}
        for it in rest:
            grouped.setdefault(section_of(it, kind), []).append(it)
        order = [name for name, _ in SECTIONS[kind] if name in grouped]
        items = [top, *rest]
        embed = build(top_len, item_len)
    return embed, [top, *rest]


def divider_png(color: int, width: int = 1200, height: int = 6) -> bytes:
    """Тонкая линия цвета рамки на всю ширину: Discord растягивает рамку под картинку,
    поэтому с ней все сводки одинаковой (максимальной) ширины."""
    r, g, b = color >> 16 & 255, color >> 8 & 255, color & 255
    row = b"\x00" + bytes((r, g, b, 255)) * width
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height, 9)) + chunk(b"IEND", b""))


def framed(embed: discord.Embed) -> dict:
    """Аргументы для send(): рамка + линия внизу, задающая единую ширину."""
    embed.set_image(url="attachment://line.png")
    return {"embed": embed, "file": discord.File(io.BytesIO(divider_png(embed.color.value)), filename="line.png")}


_size_limit = EMBED_BYTES   # запоминается: если Discord однажды отказал, дальше бот сразу берёт меньший размер


async def send_fitting(send, build):
    """Отправляет рамку build(limit), при отказе Discord «слишком большое сообщение» — ужимает и повторяет."""
    global _size_limit
    limit = _size_limit
    while True:
        result = build(limit)
        embed = result[0] if isinstance(result, tuple) else result
        try:
            await send(**framed(embed))
            _size_limit = limit
            return result
        except discord.HTTPException as exc:
            if (exc.status != 413 and exc.code != 40005) or limit <= MIN_EMBED_BYTES:
                raise
            log.warning("Discord: сообщение слишком большое (%d байт текста) — ужимаю", embed_size(embed))
            limit = max(MIN_EMBED_BYTES, int(min(limit, embed_size(embed)) * 0.85))


async def send_digest(channel: discord.abc.Messageable, items: list[dict], kind: str) -> list[dict]:
    """Публикует сводку; возвращает новости, которые в неё вошли."""
    _, shown = await send_fitting(channel.send, lambda limit: build_digest_embed(items, kind, limit))
    return shown


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
    """Статья целиком (вступление и разделы вроде «Итоги») и её главная картинка."""
    params = {"action": "query", "prop": "extracts|pageimages|info", "titles": title, "redirects": "1",
              "explaintext": "1", "exsectionformat": "wiki", "piprop": "thumbnail", "pithumbsize": "1000",
              "inprop": "url", "format": "json", "formatversion": "2"}
    async with session.get("https://ru.wikipedia.org/w/api.php", params=params,
                           timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        page = (await resp.json())["query"]["pages"][0]
    text = page.get("extract", "")
    sections = {"": ""}
    current = ""
    for line in text.split("\n"):
        m = re.match(r"^(=+)\s*(.*?)\s*=+$", line.strip())
        if m:
            current = m.group(2) if len(m.group(1)) == 2 else current   # подразделы — в свой раздел
            sections.setdefault(current, "")
        else:
            sections[current] += line + "\n"
    return {"title": page.get("title", title), "intro": sections.pop(""), "sections": sections,
            "url": page.get("fullurl") or f"https://ru.wikipedia.org/wiki/{title.replace(' ', '_')}",
            "image": page.get("thumbnail", {}).get("source")}


# Скобки со справочными подробностями, которые мешают читать: (англ. …), (22 октября [3 ноября]) …
_REFERENCE_PARENS_RE = re.compile(
    r"\s*\((?:[^()]*?(?:англ|лат|нем|фр|франц|греч|польск|укр|тат|швед|тур|дореф|рус|ст\. ст|н\. ст|МФА|"
    r"произн|род\.|ум\.|\[)[^()]*)\)")


def readable(text: str) -> str:
    text = text.replace("́", "")
    for _ in range(2):
        text = _REFERENCE_PARENS_RE.sub("", text)
    text = re.sub(r"\(\s*[;,]?\s*\)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\s+([,.;:])", r"\1", text)


def take_text(text: str, limit: int) -> str:
    """Начало текста по целым предложениям, абзацами."""
    paragraphs = [p.strip() for p in readable(text).split("\n") if len(p.strip()) > 30]
    body, _ = article_body(split_long(paragraphs, 450), limit)
    return body


def find_section(article: dict, *names: str) -> str:
    for title, text in article["sections"].items():
        if any(title.lower().startswith(n) for n in names) and len(text.strip()) > 80:
            return text
    return ""


_history_cache: dict[dt.date, dict] = {}
history_error = ""   # почему не удалось получить события — показывается в ответе /post_now


async def collect_history(session: aiohttp.ClientSession) -> dict | None:
    """{"day": дата, "ranked": события дня о России, от самого значимого}."""
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
    result = {"day": day, "ranked": ranked}
    _history_cache.clear()
    _history_cache[day] = result
    return result


def event_key(event: dict) -> str:
    return f"{event['year']}|{event['text'][:60]}"


_article_cache: dict[str, dict | None] = {}


async def prepare_history(session: aiohttp.ClientSession, done: set[str] = frozenset()) -> dict | None:
    """Пост летописи: самое значимое событие дня, которого ещё не было (done — уже выложенные сегодня)."""
    history = await collect_history(session)
    if not history:
        return None
    fresh = [e for e in history["ranked"] if event_key(e) not in done]
    if not fresh:
        return {"day": history["day"], "exhausted": True}

    main = dict(fresh[0])
    title = main_title(main)
    if title and title not in _article_cache:
        try:
            _article_cache[title] = await fetch_article(session, title)
        except Exception as exc:
            log.warning("Не удалось загрузить статью «%s»: %r", title, exc)
            _article_cache[title] = None
    main["article"] = _article_cache.get(title) if title else None

    others = [e for e in fresh[1:] if significance(e) > 0][:5]
    others.sort(key=lambda e: e["year"] or 0)
    return {"main": main, "others": others, "day": history["day"]}


def year_label(year: int) -> str:
    return f"{-year} год до н. э." if year < 0 else f"{year} год"


def build_history_embed(history: dict, limit: int = EMBED_BYTES, story_len: int = 2200) -> discord.Embed:
    day, main, article = history["day"], history["main"], history["main"]["article"]
    year = main["year"] or 0
    date_text = f"{day.day} {MONTHS_GEN[day.month]}"
    event = main["text"].rstrip(".")

    # зачин: «Ровно 214 лет назад, 29 октября 1812 года, …»
    if 0 < year < day.year:
        ago = plural(day.year - year, "год", "года", "лет")
        hook = f"Ровно {ago} назад — {date_text} {year} года. {event}."
    else:
        hook = f"{date_text}: {event}."
    parts = [f"> *{hook}*"]

    if article:
        story = take_text(article["intro"], story_len)
        if story:
            # термин в начале статьи — жирным: «**Сражение при Вязьме** — …»
            story = re.sub(r"^(.{3,90}?)\s+[—–-]\s+", r"**\1** — ", story, count=1)
            parts.append(story)

    # заголовок — название события, если статья о нём самом («Сражение при Вязьме»), иначе само событие
    about_event = article and any(r.search(article["title"]) for r, p in _SIGNIFICANCE_RE if p > 0)
    embed = discord.Embed(
        title=shorten(f"{year_label(year)}. {article['title'] if about_event else event}", 256),
        url=article["url"] if article else f"https://ru.wikipedia.org/wiki/{day.day}_{MONTHS_GEN[day.month]}",
        description=shorten("\n\n".join(parts), 4000),
        color=HISTORY_COLOR,
    )
    embed.set_author(name=f"📜 Летопись России · {date_text}")

    if article:
        outcome = find_section(article, "итог", "значени", "последстви", "результат")
        if outcome:
            embed.add_field(name="⚖️ Итоги и значение", value=take_text(outcome, 950), inline=False)
        trivia = find_section(article, "интересн", "любопытн", "факт")
        if trivia:
            fact = take_text(trivia, 500).split("\n\n")[0]
            embed.add_field(name="💡 Любопытный факт", value=fact, inline=False)
        memory = find_section(article, "памят", "увековеч", "в культуре", "в искусстве")
        if memory and not trivia:
            embed.add_field(name="🏛️ Память", value=take_text(memory, 500).split("\n\n")[0], inline=False)

    if history["others"]:
        lines = [f"`{e['year']}` {shorten(e['text'], 180)}" for e in history["others"]]
        embed.add_field(name="🕯️ Также в этот день", value="\n".join(lines)[:1024], inline=False)

    if article and article["image"]:
        embed.set_thumbnail(url=article["image"])
    link = f"📖 Полная статья — Википедия" if article else "По материалам Википедии"
    embed.set_footer(text=f"{link} · ⏱ {max(1, round(len(embed) / 1100))} мин чтения")
    if embed_size(embed) > limit and story_len > 300:     # не влезает в сообщение Discord — рассказ короче
        return build_history_embed(history, limit, story_len - 200)
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
        info = await self.application_info()
        owners = {m.id for m in info.team.members} if info.team else {info.owner.id}
        self.owner_ids = owners | EXTRA_OWNER_IDS
        log.info("Командами пользуются только: %s", ", ".join(map(str, sorted(self.owner_ids))))
        await self.tree.sync()   # глобальных команд нет: убираем старые, команды живут на каждом сервере

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

        stamp = dt.datetime.now(TZ).strftime("%d.%m %H:%M")
        if targets.get("news") or targets.get("svo"):
            news, svo = await collect_news(self.http_session, set(cfg["sent"]), [t for _, t in cfg["sent_titles"]])
            for kind, items in (("news", news), ("svo", svo)):
                channel = targets.get(kind)
                if not channel:
                    continue
                if not items:
                    report.append(f"{KINDS[kind]}: новых новостей с прошлой сводки нет — всё уже было")
                    continue
                items = await send_digest(channel, items, kind)
                cfg["last_post"][kind] = stamp
                mark_sent(cfg, items)
                report.append(f"{KINDS[kind]}: {plural(len(items), 'публикация', 'публикации', 'публикаций')} → {channel.mention}")

        channel = targets.get("history")
        if channel:
            today = dt.datetime.now(TZ).date().isoformat()
            done = cfg.get("history_done", {})
            if done.get("date") != today:
                done = cfg["history_done"] = {"date": today, "events": []}
            history = await prepare_history(self.http_session, set(done["events"]))
            if history and history.get("exhausted"):
                report.append(f"{KINDS['history']}: все события этого дня уже выложены")
            elif history:
                await send_fitting(channel.send, lambda limit: build_history_embed(history, limit))
                done["events"].append(event_key(history["main"]))
                cfg["last_post"]["history"] = stamp
                save_data()
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


# ───────────────────────────── КОМАНДЫ ─────────────────────────────

KIND_CHOICES = [app_commands.Choice(name=name, value=kind) for kind, name in KINDS.items()]


class NotOwner(app_commands.CheckFailure):
    pass


def owner_only():
    """Команда работает только у владельца бота (и у тех, кто указан в OWNER_IDS)."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id not in getattr(interaction.client, "owner_ids", set()):
            raise NotOwner()
        return True
    return app_commands.check(predicate)


# administrator — чтобы команды не были видны обычным участникам; owner_only — чтобы работали только у вас
class SetupGroup(app_commands.Group):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in getattr(interaction.client, "owner_ids", set()):
            raise NotOwner()
        return True


setup_group = SetupGroup(
    name="setup", description="Настройка сводок",
    default_permissions=discord.Permissions(administrator=True), guild_only=True,
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
    cfg["channels"][kind.value] = channel.id
    save_data()
    await interaction.response.send_message(
        f"✅ {kind.name} будет публиковаться в {channel.mention}.\n"
        "Опубликовать: `/post_now` (бот сам ничего не отправляет — только по этой команде)", ephemeral=True)


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
        last = cfg["last_post"].get(kind)
        value = (channel.mention if channel else "— не настроено") + (f"\nпоследняя: {last} МСК" if last else "")
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text="Публикация — только вручную командой /post_now")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="post_now", description="Опубликовать сводки (только новое — без повторов)")
@app_commands.describe(kind="Какую сводку (по умолчанию — все)")
@app_commands.rename(kind="сводка")
@app_commands.choices(kind=KIND_CHOICES)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@owner_only()
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
@app_commands.default_permissions(administrator=True)
@owner_only()
async def history_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    history = await prepare_history(bot.http_session)
    if history and not history.get("exhausted"):
        await send_fitting(interaction.followup.send, lambda limit: build_history_embed(history, limit))
    else:
        await interaction.followup.send("Не удалось получить события, попробуйте позже.")


@app_commands.command(name="invite", description="Ссылка, чтобы добавить бота на другой сервер")
@app_commands.default_permissions(administrator=True)
@owner_only()
async def invite_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"🔗 {bot.invite_url()}", ephemeral=True)


# ───────────────────────────── СОБЫТИЯ ─────────────────────────────

WELCOME = (
    "👋 Привет! Я публикую сводки новостей, СВО и «Этот день в истории России» — по команде владельца.\n\n"
    "**Настройка (только для владельца бота).** Наберите `/` и выберите команду из списка:\n"
    "1. `/setup channel` → выберите сводку и канал (повторите для каждой из трёх)\n"
    "2. `/post_now` → опубликовать; каждый раз — только то, чего ещё не было\n\n"
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
    if isinstance(error, NotOwner):
        log.warning("Команду попытался использовать не владелец: %s (%s)", interaction.user, interaction.user.id)
        text = "⛔ Эта команда доступна только владельцу бота."
    elif isinstance(error, app_commands.MissingPermissions):
        text = "❌ У вас нет прав для этой команды."
    else:
        log.error("Ошибка команды: %s", error, exc_info=error)
        text = f"❌ Ошибка: {error}"
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
            await channel.send(WELCOME)
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
        ENV_FILE.write_text(f"DISCORD_TOKEN={token}\n", encoding="utf-8")
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
