# Межа для Windows

`Mezha-Windows.zip` — готовое приложение для Windows 10 и 11: распакуйте архив и запустите `Mezha\Mezha.exe`.
Программа открывается в своём окне (без браузера), работа сохраняется сама.

Как это устроено:

- `Mezha.exe` — оболочка [Neutralinojs](https://neutralino.js.org) 6.10 (лицензия MIT) с иконкой «Межи».
  Она открывает окно на встроенном в Windows движке Microsoft Edge WebView2 и отдаёт программу с локального
  адреса `127.0.0.1:47391` (порт постоянный, поэтому сохранённая работа не теряется между запусками).
- `resources.neu` — архив ASAR с самой программой (`index.html`), иконкой и настройками окна
  (`neutralino.config.json`).

Тот же архив собирает кнопка «Скачать программу на ПК» прямо в программе: оболочка для Windows вшита в страницу,
а `resources.neu` и zip составляются в браузере из текущей версии программы.

Пересборка оболочки: `neu build` в папке с этим `neutralino.config.json`, `resources/index.html` и `resources/icon.png`
(пакет `@neutralinojs/neu`), затем `Mezha-win_x64.exe` вшивается в `index.html` как `<script id="winshell">` в base64.
