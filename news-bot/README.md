# Discord-бот ежедневных сводок

Каждый день в заданное время (по Москве) бот публикует Embed-сводки в выбранные каналы:

| Сводка | Что приходит |
|---|---|
| 📰 Новости | свежие публикации из RSS (ТАСС, РИА, Лента.ру, Интерфакс) |
| 🎖️ СВО | новости из тех же лент, отобранные по ключевым словам |
| 📜 История России | события с сегодняшней датой из Википедии |

Всё настраивается прямо в Discord, отдельно на каждом сервере. В файле `.env` нужен только токен.

**Пошагово с названиями кнопок русского Discord: [ИНСТРУКЦИЯ.md](ИНСТРУКЦИЯ.md).**

## Команды

| Команда | Кто может | Что делает |
|---|---|---|
| `/setup channel` | «Управлять сервером» | выбрать, какая сводка в какой канал |
| `/setup time` | «Управлять сервером» | время публикации (МСК), например `09:00` |
| `/setup status` | «Управлять сервером» | текущие настройки |
| `/setup off` | «Управлять сервером» | выключить одну сводку |
| `/post_now` | «Управлять сервером» | опубликовать сводки прямо сейчас |
| `/history` | все | «Этот день в истории России» в текущий канал |
| `/invite` | все | ссылка для добавления бота на другой сервер |

Если бот был выключен в момент публикации, он отправит сводку сразу после запуска.

## 1. Создать бота в Discord (один раз)

1. https://discord.com/developers/applications → **New Application** → имя → **Create**.
2. **Bot** → **Reset Token** → скопировать токен. Никому его не показывайте.
3. Там же можно поставить аватар и имя бота. Привилегированные Intents включать не нужно.
4. **Installation** → в **Install Link** выберите **Discord Provided Link**. В **Guild Install** добавьте scopes `bot` и `applications.commands` и права `View Channels`, `Send Messages`, `Embed Links`. Сохраните. Готовая ссылка также печатается в логе при запуске и выдаётся командой `/invite`.

## 2. Запустить бота

Нужен Python 3.10+.

```bash
cd news-bot
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Linux / macOS
pip install -r requirements.txt
```

Скопируйте `.env.example` в `.env` и впишите токен.

```bash
python bot.py
```

В логе появится строка `Ссылка-приглашение: https://discord.com/oauth2/authorize?...`

## 3. Активировать на сервере

1. Откройте ссылку-приглашение → выберите сервер → **Авторизовать**. Нужно право «Управлять сервером» на этом сервере.
2. Бот напишет приветствие с подсказкой.
3. Наберите `/setup channel`, выберите **📰 Новости** и канал. Повторите для **🎖️ СВО** и **📜 История России**.
4. `/setup time 09:00` — время публикации.
5. `/post_now` — проверка: сводки появятся сразу.

Если бот пишет, что у него нет прав в канале: откройте настройки канала → **Права доступа** → добавьте бота (или его роль) и разрешите «Просматривать канал», «Отправлять сообщения», «Встраивать ссылки».

Чтобы скрыть команды `/setup` от модераторов или открыть их другим ролям: **Настройки сервера → Интеграции → ваш бот**.

## 4. Работа 24/7 (VPS с Linux)

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
git clone https://github.com/matkorsunsky-pixel/2zws2zx.git
cd 2zws2zx && git checkout claude/confident-hypatia-zvo4x1 && cd news-bot
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env     # вписать токен
```

Автозапуск через systemd: замените `USER` и пути в `newsbot.service`, затем

```bash
sudo cp newsbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now newsbot
journalctl -u newsbot -f              # логи
```

Настройки серверов хранятся в `data.json` рядом с `bot.py`. Не удаляйте его при обновлении.

## Тонкая настройка

В начале `bot.py`: `NEWS_FEEDS` (ленты), `SVO_FEEDS` / `SVO_KEYWORDS` / `SVO_WORDS` (отбор СВО), `RUSSIA_KEYWORDS` (история), `MAX_ITEMS_PER_DIGEST`, `MAX_AGE_HOURS`. После изменений: `sudo systemctl restart newsbot`.
