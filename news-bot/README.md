# Discord-бот ежедневных сводок

Каждый день в заданное время (по Москве) бот публикует три Embed-сообщения в три канала:

| Канал | Что приходит |
|---|---|
| `NEWS_CHANNEL_ID` | 📰 Сводка новостей — свежие публикации из RSS (ТАСС, РИА, Лента.ру, Интерфакс) |
| `SVO_CHANNEL_ID` | 🎖️ Сводка СВО — новости из тех же лент, отобранные по ключевым словам |
| `HISTORY_CHANNEL_ID` | 📜 Этот день в истории России — события с сегодняшней датой из Википедии |

Бот запоминает отправленное в `sent.json` и не повторяет новости. Команда `/post_now` (только для администраторов) публикует сводки сразу — удобно для проверки.

## 1. Создать бота в Discord

1. Откройте https://discord.com/developers/applications → **New Application** → назовите.
2. Слева **Bot** → **Reset Token** → скопируйте токен (никому его не показывайте).
3. Слева **OAuth2 → URL Generator**: отметьте `bot` и `applications.commands`, ниже права `Send Messages`, `Embed Links`, `View Channels`. Откройте получившуюся ссылку и добавьте бота на сервер.
4. В Discord: **Настройки → Расширенные → Режим разработчика** — включить. Теперь ПКМ по каналу → **Копировать ID**.

Каналы могут быть на разных серверах — бота нужно просто добавить на каждый.

## 2. Установить и запустить на своём компьютере

Нужен Python 3.10+ (https://www.python.org/downloads/, при установке на Windows отметьте «Add Python to PATH»).

```bash
cd news-bot
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux / macOS
pip install -r requirements.txt
```

Скопируйте `.env.example` в `.env` и впишите токен, три ID каналов и время (`POST_TIMES=09:00`).

```bash
python bot.py
```

В Discord наберите `/post_now`, чтобы сразу проверить сводки.

## 3. Работа 24/7 (VPS с Linux)

Компьютер дома должен быть всегда включён, поэтому лучше арендовать недорогой VPS (Ubuntu) у любого хостинга.

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
git clone <ссылка на этот репозиторий> && cd <папка>/news-bot
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env     # вписать токен и ID
```

Автозапуск и перезапуск при сбоях через systemd: отредактируйте `newsbot.service` (пути и имя пользователя), затем

```bash
sudo cp newsbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now newsbot
journalctl -u newsbot -f              # смотреть логи
```

## Настройка

Всё в начале `bot.py`: `NEWS_FEEDS` (ленты новостей), `SVO_FEEDS` и `SVO_KEYWORDS` (отбор СВО), `RUSSIA_KEYWORDS` (отбор исторических событий), `MAX_ITEMS_PER_DIGEST`, `MAX_AGE_HOURS`. После изменений перезапустите бота (`sudo systemctl restart newsbot`).
