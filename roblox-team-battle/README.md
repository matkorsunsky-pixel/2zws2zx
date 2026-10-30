# Командные раунды ОМОН vs Бандиты (Roblox)

Лобби → отсчёт 30 секунд → игроки поровну делятся на ОМОН и Бандитов → каждому выдаётся
случайное основное и вторичное оружие → телепорт на точки появления → бой.
Раунд кончается, когда в одной из команд не осталось живых (или по лимиту времени — ничья).
Убитые возрождаются в лобби и ждут следующего раунда.

## Как собрать в Roblox Studio

```
Workspace
├── LobbySpawn            (SpawnLocation в лобби)
├── ПоявлениеОМОНа        (Part)
└── ПоявлениеБандитов     (Part)

ServerStorage
└── Weapons               (Folder)
    ├── Primary           (Folder) — Tool'ы основного оружия: автоматы, дробовики...
    └── Secondary         (Folder) — Tool'ы вторичного: пистолеты, ножи...

ServerScriptService
└── RoundManager          (Script)      ← RoundManager.server.lua

StarterPlayer
└── StarterPlayerScripts
    └── RoundGui          (LocalScript) ← RoundGui.client.lua
```

1. **LobbySpawn** — это SpawnLocation. Других SpawnLocation на карте быть не должно (или у них
   должен стоять `Enabled = false`), иначе убитые будут возрождаться не в лобби.
2. **ПоявлениеОМОНа / ПоявлениеБандитов** — обычные Part, не SpawnLocation. Сделайте их
   `Anchored = true`, `CanCollide = false`, `Transparency = 1`. Чем шире парт, тем дальше
   игроки стоят друг от друга при появлении.
3. Всё оружие положите в `Weapons/Primary` и `Weapons/Secondary`. Каждое должно быть **Tool**
   (как в StarterPack) — скрипт клонирует случайное в рюкзак игрока. Из StarterPack оружие
   уберите, иначе его будут получать и в лобби.
4. Команды «Лобби», «ОМОН», «Бандиты» создаются автоматически.

Имена партов, папок и команд, время отсчёта и лимит раунда меняются в таблице `CONFIG`
в начале `RoundManager`.

## Важно: огонь по своим

Скрипт раундов сам урон не наносит — урон делает ваша система оружия. Чтобы свои не убивали
своих, в серверном коде оружия перед `TakeDamage` добавьте проверку:

```lua
local Players = game:GetService("Players")

-- shooter — игрок, который стрелял; hitHumanoid — Humanoid, в которого попали
local victim = Players:GetPlayerFromCharacter(hitHumanoid.Parent)
if victim and victim.Team == shooter.Team then
	return -- свой, урон не наносим
end
hitHumanoid:TakeDamage(damage)
```

Урон должен считаться **на сервере** (через RemoteEvent), а не в LocalScript — иначе
другие игроки его не увидят, а читеры смогут убивать кого угодно.

## Проверка без друзей

Studio → вкладка **Test** → **Clients and Servers** → выберите 2–3 игрока → **Start**.
Откроется несколько окон, и раунд запустится как на настоящем сервере.
