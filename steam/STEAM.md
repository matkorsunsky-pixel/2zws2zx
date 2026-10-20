# Межа — версия для Steam

Отдельная сборка игры для Steam: Windows и Linux (подходит и для Steam Deck).

## Чем она отличается от обычной версии

| | Обычная версия | Версия для Steam |
|---|---|---|
| Оболочка | Neutralino (8 МБ) | Electron 33 + steamworks.js |
| Достижения Steam, оверлей Shift+Tab | — | есть (12 достижений) |
| Спутниковые снимки | Esri World Imagery | NASA Blue Marble (общественное достояние, обзорного качества) |
| Подписи и слои «Улицы», «Рельеф» поверх карты | Esri | убраны (у игры свои подписи) |
| Поиск мест | сервер OpenStreetMap (Nominatim) | без интернета: города всех сценариев, страны и регионы |
| Шрифты | Google Fonts из интернета | встроены в игру |
| «Кризис на Украине» (2014–2026, 136 этапов) | есть | **убран** (см. ниже) |
| Полный экран | — | F11 или Alt+Enter |
| Кнопка «Выйти из игры» в меню | — | есть |

**Почему убрана Украина.** Её линии фронта построены по картам DeepState и uacontrolmap.com. Их данные не лицензированы для продаваемой игры. Кроме того, для автора из России этот сценарий — юридический риск. Если вы получите письменное разрешение авторов этих карт, соберите с ключом `--with-ukraine`: всё вернётся на место.

## Как собрать

Нужен Python 3. Интернет нужен для первой загрузки Electron, steamworks.js и шрифтов, потом всё берётся из `steam/.cache`.

```
python3 steam/build.py                  # обе платформы, тестовый App ID 480
python3 steam/build.py --appid 1234560  # ваш App ID из Steamworks
python3 steam/build.py --platform win   # только Windows
```

Результат появится в `steam/dist/`:
- папки `Mezha-Steam-Windows/` и `Mezha-Steam-Linux/` — их и загружают в Steam;
- zip-архивы этих папок.

С App ID 480 (это тестовая игра Valve «Spacewar») рядом с программой кладётся `steam_appid.txt`. С ним игра запускается двойным щелчком, без перезапуска через Steam. В релиз его не загружают: шаблоны в `contentbuilder/` его исключают.

## Путь до выпуска

1. **Регистрация.** Зарегистрируйтесь на partner.steamgames.com и подпишите соглашение. Заполните налоговую анкету и банковские данные, пройдите проверку личности. Valve не платит на российские банковские счета: нужен счёт в другой стране или издатель.
2. **Взнос.** Оплатите Steam Direct (100 $), получите App ID. С этого момента 30 дней ожидания до выпуска.
3. **Сборка с вашим App ID.** Выполните `python3 steam/build.py --appid ВАШ_ID`.
4. **Настройки в Steamworks → Installation → General.** Добавьте два способа запуска:
   - Windows: `Mezha.exe`;
   - Linux: `Mezha`.
5. **Депо.** Steamworks → SteamPipe → Depots: создайте два депо, Windows и Linux. Впишите их номера и App ID в `contentbuilder/*.vdf` вместо 480, 481, 482.
6. **Загрузка.**
   - Скачайте Steamworks SDK и положите `contentbuilder/` рядом с `steam/dist`.
   - Выполните: `steamcmd +login ВАШ_ЛОГИН +run_app_build ..\scripts\app_build.vdf +quit`.
   - В Steamworks → Builds назначьте сборку на ветку `default`.
7. **Достижения.** Steamworks → Stats & Achievements: заведите 12 достижений из таблицы ниже с этими API-именами. Значки 64×64 лежат в `store/achievements/` (у каждого цветной и серый вариант `_locked`). Опубликуйте изменения.
8. **Страница магазина.** Нужно:
   - описание (короткое и полное), жанры: Strategy, Simulation, Indie;
   - скриншоты: 7 штук в 1920×1080 лежат в `store/screenshot_*.png`;
   - обложки (`store/`):
     - header 920×430, small 462×174, main 1232×706, vertical 748×896;
     - library capsule 600×900, library hero 3840×1240, library logo 1280×720 (с прозрачностью).
   - трейлер (по желанию): снимается встроенной записью видео, кнопка «Видео» на шкале этапов.
   - анкета о содержании: игра изображает реальные войны и реальных военачальников, сцен насилия нет.
9. **Ранний доступ.** Можно выпускаться в Early Access. Страница «Скоро выйдет» должна провисеть не меньше двух недель.
10. **Проверка Valve и выпуск.** Valve проверяет и страницу, и сборку. Обычно это занимает несколько рабочих дней.

## Достижения

| API-имя | Название | Условие | Name (EN) | Description (EN) |
|---|---|---|---|---|
| `ACH_FIRST_BATTLE` | Время пошло | Запустите время в своей игре с войсками на карте | The Clock Is Ticking | Start the clock in a game with troops on the map |
| `ACH_CITY` | Первый город | Возьмите город | First City | Take a city |
| `ACH_CAPITAL` | Столица пала | Возьмите вражескую столицу | The Capital Falls | Take an enemy capital |
| `ACH_POCKET` | Котёл | Окружите и ликвидируйте вражеский котёл | Encirclement | Encircle and destroy an enemy pocket |
| `ACH_POCKET_BIG` | Большой котёл | Возьмите в плен 20 и больше отрядов в одном котле | The Great Cauldron | Capture 20 or more units in a single pocket |
| `ACH_VICTORY` | Победа | Выполните цели этапа | Victory | Complete a stage's objectives |
| `ACH_PERFECT` | Три звезды | Победите с тремя звёздами | Three Stars | Win with three stars |
| `ACH_CHRONICLE` | Хроника | Досмотрите войну по этапам до последнего | Chronicle | Watch a war through to its last stage |
| `ACH_HISTORIAN` | Историк | Досмотрите до конца пять разных войн | Historian | Watch five different wars to the end |
| `ACH_ALL_ERAS` | Сквозь века | Сыграйте войну каждой эпохи: XIX век, империи и революции, Вторая мировая, холодная война, новейшее время | Through the Ages | Play a war from every era |
| `ACH_ARROW` | План удара | Нарисуйте стрелку удара | Plan of Attack | Draw an arrow of attack |
| `ACH_SANDBOX` | Картограф | Начните игру в песочнице | Cartographer | Start a sandbox game |

Игра отмечает достижения сама: `ach(...)` в `index.html` → `preload.js` → `main.js` → `steamworks.js`. Если Steam не запущен, игра работает как обычно, просто без достижений.

## Сохранения

Сохранения лежат в профиле пользователя: Windows — `%APPDATA%\Mezha`, Linux — `~/.config/Mezha`. Проекты сохраняются в файлы `.json` через обычное окно «Сохранить».

Steam Cloud для внутреннего хранилища браузера (IndexedDB) лучше не включать: Steam синхронизирует файлы, которые в этот момент открыты, и может их испортить.

## Лицензии

Источники данных, шрифты и программные компоненты перечислены в `CREDITS.txt`, он лежит рядом с игрой. Перед выпуском проверьте, что название «Межа» не занято в Steam и не зарегистрировано как торговая марка.
