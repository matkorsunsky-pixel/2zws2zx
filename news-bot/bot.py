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

DATA_FILE = BASE_DIR / "data.json"   # настройки серверов и уже отправленное
FEED_CACHE_SECONDS = 600                            # не скачивать ленты чаще раза в 10 минут

KINDS = {"news": "📰 Новости", "svo": "🎖️ СВО", "history": "📜 История России"}

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


async def collect_news(session: aiohttp.ClientSession, sent: set[str]) -> tuple[list[dict], list[dict]]:
    """Возвращает (новости, новости СВО) — только свежие и ещё не отправленные на этот сервер."""
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


def mark_sent(cfg: dict, items: list[dict]) -> None:
    for it in items:
        cfg["sent"].append(it["id"])
        if it["link"] != it["id"]:
            cfg["sent"].append(it["link"])
    save_data()


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


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(1, "год", "года", "лет") -> "1 год", plural(5, ...) -> "5 лет"."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


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


_history_cache: dict[dt.date, list[dict]] = {}


history_error = ""   # почему не удалось получить события — показывается в ответе /post_now


async def collect_history(session: aiohttp.ClientSession) -> list[dict]:
    global history_error
    day = dt.datetime.now(TZ).date()
    if day in _history_cache:
        return _history_cache[day]

    events, errors = [], []
    for source in (history_from_api, history_from_page):
        try:
            events = await source(session, day)
        except Exception as exc:
            errors.append(f"{source.__name__}: {exc!r}")
            log.warning("Википедия (%s): %r", source.__name__, exc)
            continue
        log.info("Википедия (%s): %d событий", source.__name__, len(events))
        if any(is_russian_history(e["search"]) for e in events):
            break
    if not events:
        history_error = "; ".join(errors) or "Википедия не вернула событий"
        return []
    history_error = ""

    # события России; если их нет — любые события дня, чтобы рубрика не пустовала
    russian = [e for e in events if is_russian_history(e["search"])] or events
    russian.sort(key=lambda e: e["year"] or 0)
    if len(russian) > MAX_HISTORY_EVENTS:  # равномерно по эпохам
        step = len(russian) / MAX_HISTORY_EVENTS
        russian = [russian[int(i * step)] for i in range(MAX_HISTORY_EVENTS)]
    _history_cache.clear()
    _history_cache[day] = russian
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
        name = f"🗓️ {ev['year']} год" + (f" ({plural(years_ago, 'год', 'года', 'лет')} назад)" if years_ago else "")
        value = shorten(ev["text"], 900) + (f"\n[Подробнее →]({ev['link']})" if ev["link"] else "")
        embed.add_field(name=name, value=value, inline=False)
    image = next((e["image"] for e in events if e["image"]), None)
    if image:
        embed.set_thumbnail(url=image)
    embed.set_footer(text="Источник: Википедия")
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
                await channel.send(embed=build_digest_embed(items, kind))
                mark_sent(cfg, items)
                report.append(f"{KINDS[kind]}: {plural(len(items), 'публикация', 'публикации', 'публикаций')} → {channel.mention}")

        channel = targets.get("history")
        if channel:
            events = await collect_history(self.http_session)
            if events:
                await channel.send(embed=build_history_embed(events))
                report.append(f"{KINDS['history']}: {plural(len(events), 'событие', 'события', 'событий')} → {channel.mention}")
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
    events = await collect_history(bot.http_session)
    if events:
        await interaction.followup.send(embed=build_history_embed(events))
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


def main() -> None:
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
