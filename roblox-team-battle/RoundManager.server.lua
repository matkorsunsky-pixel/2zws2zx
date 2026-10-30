--[[
	RoundManager — командные раунды ОМОН против Бандитов.
	Положить как Script в ServerScriptService.

	Что нужно в плейсе:
	  Workspace.LobbySpawn          — SpawnLocation в лобби
	  Workspace.ПоявлениеОМОНа      — обычный Part (не SpawnLocation!), где появляется ОМОН
	  Workspace.ПоявлениеБандитов   — обычный Part, где появляются бандиты
	  ServerStorage.Weapons.Primary   — папка с Tool'ами основного оружия
	  ServerStorage.Weapons.Secondary — папка с Tool'ами вторичного оружия
	Команды (Teams) скрипт создаёт сам, если их нет.
]]

local Players = game:GetService("Players")
local Teams = game:GetService("Teams")
local ServerStorage = game:GetService("ServerStorage")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local CONFIG = {
	MIN_PLAYERS = 2,       -- сколько игроков нужно для старта
	INTERMISSION = 30,     -- отсчёт в лобби, секунд
	ROUND_MAX_TIME = 300,  -- лимит раунда, секунд (0 = без лимита)
	END_PAUSE = 5,         -- пауза после победы перед возвратом в лобби

	LOBBY_TEAM = "Лобби",
	TEAM_A = "ОМОН",
	TEAM_B = "Бандиты",

	LOBBY_SPAWN = "LobbySpawn",
	SPAWN_A = "ПоявлениеОМОНа",
	SPAWN_B = "ПоявлениеБандитов",

	WEAPONS_FOLDER = "Weapons",
	PRIMARY_FOLDER = "Primary",
	SECONDARY_FOLDER = "Secondary",
}

local rng = Random.new()

-- Команды ---------------------------------------------------------------

local function getTeam(name, color, autoAssign)
	local team = Teams:FindFirstChild(name)
	if not team then
		team = Instance.new("Team")
		team.Name = name
		team.TeamColor = color
		team.Parent = Teams
	end
	team.AutoAssignable = autoAssign
	return team
end

local lobbyTeam = getTeam(CONFIG.LOBBY_TEAM, BrickColor.new("Medium stone grey"), true)
local teamA = getTeam(CONFIG.TEAM_A, BrickColor.new("Bright blue"), false)
local teamB = getTeam(CONFIG.TEAM_B, BrickColor.new("Bright red"), false)

-- Точки появления --------------------------------------------------------

local function findPart(name)
	local part = workspace:FindFirstChild(name, true)
	if not part or not part:IsA("BasePart") then
		error("[RoundManager] В Workspace нет парта '" .. name .. "'")
	end
	return part
end

local lobbySpawn = findPart(CONFIG.LOBBY_SPAWN)
local spawnA = findPart(CONFIG.SPAWN_A)
local spawnB = findPart(CONFIG.SPAWN_B)

-- Убитые и новые игроки появляются только в лобби
if lobbySpawn:IsA("SpawnLocation") then
	lobbySpawn.Neutral = false
	lobbySpawn.TeamColor = lobbyTeam.TeamColor
	lobbySpawn.AllowTeamChangeOnTouch = false
end

local weapons = ServerStorage:WaitForChild(CONFIG.WEAPONS_FOLDER)

-- Статус для интерфейса --------------------------------------------------

local status = ReplicatedStorage:FindFirstChild("RoundStatus")
if not status then
	status = Instance.new("StringValue")
	status.Name = "RoundStatus"
	status.Parent = ReplicatedStorage
end

-- Состояние раунда -------------------------------------------------------

local roundId = 0
local alive = {} -- [player] = team, только живые участники текущего раунда

local function countAlive(team)
	local n = 0
	for _, t in pairs(alive) do
		if t == team then
			n += 1
		end
	end
	return n
end

local function sendToLobby(plr)
	alive[plr] = nil
	plr.Team = lobbyTeam
end

local function giveRandom(folderName, plr)
	local folder = weapons:FindFirstChild(folderName)
	if not folder then
		warn("[RoundManager] Нет папки " .. CONFIG.WEAPONS_FOLDER .. "." .. folderName)
		return
	end
	local list = {}
	for _, item in ipairs(folder:GetChildren()) do
		if item:IsA("Tool") then
			table.insert(list, item)
		end
	end
	if #list > 0 then
		list[rng:NextInteger(1, #list)]:Clone().Parent = plr.Backpack
	end
end

-- Случайная точка на верхней грани парта, чтобы игроки не стояли друг в друге
local function randomPointOn(part)
	local s = part.Size
	local offset = Vector3.new(
		rng:NextNumber(-0.4, 0.4) * s.X,
		s.Y / 2 + 3,
		rng:NextNumber(-0.4, 0.4) * s.Z
	)
	return CFrame.new(part.CFrame:PointToWorldSpace(offset))
end

local function deployPlayer(plr, team, spawnPart, id)
	plr.Team = team
	plr:LoadCharacter() -- свежий персонаж: полное здоровье, пустой рюкзак
	if id ~= roundId or plr.Parent == nil then
		return
	end

	local char = plr.Character
	local hum = char and char:WaitForChild("Humanoid", 5)
	local root = char and char:WaitForChild("HumanoidRootPart", 5)
	if not hum or not root then
		sendToLobby(plr)
		return
	end

	giveRandom(CONFIG.PRIMARY_FOLDER, plr)
	giveRandom(CONFIG.SECONDARY_FOLDER, plr)
	char:PivotTo(randomPointOn(spawnPart))

	alive[plr] = team
	hum.Died:Connect(function()
		if id == roundId and alive[plr] then
			sendToLobby(plr) -- возродится уже в лобби
		end
	end)
end

local function shuffle(list)
	for i = #list, 2, -1 do
		local j = rng:NextInteger(1, i)
		list[i], list[j] = list[j], list[i]
	end
	return list
end

local function runRound()
	roundId += 1
	local id = roundId
	alive = {}

	-- Делим поровну: перемешали и раздаём через одного.
	-- При нечётном числе лишний игрок случайно достаётся одной из команд.
	local participants = shuffle(Players:GetPlayers())
	local flip = rng:NextInteger(0, 1)
	local pending = #participants
	status.Value = "Раунд начинается!"

	for i, plr in ipairs(participants) do
		local toA = (i + flip) % 2 == 0
		task.spawn(function()
			local ok, err = pcall(deployPlayer, plr,
				toA and teamA or teamB,
				toA and spawnA or spawnB,
				id)
			if not ok then
				warn("[RoundManager] " .. tostring(err))
				sendToLobby(plr)
			end
			pending -= 1
		end)
	end

	local waitStart = os.clock()
	while pending > 0 and os.clock() - waitStart < 10 do
		task.wait()
	end

	-- Бой идёт, пока в обеих командах кто-то жив
	local started = os.clock()
	local winner, draw
	while true do
		local a, b = countAlive(teamA), countAlive(teamB)
		if a == 0 and b == 0 then
			draw = true
			break
		elseif a == 0 then
			winner = teamB
			break
		elseif b == 0 then
			winner = teamA
			break
		end

		local elapsed = os.clock() - started
		if CONFIG.ROUND_MAX_TIME > 0 and elapsed >= CONFIG.ROUND_MAX_TIME then
			draw = true
			break
		end

		local timeText = ""
		if CONFIG.ROUND_MAX_TIME > 0 then
			local left = math.ceil(CONFIG.ROUND_MAX_TIME - elapsed)
			timeText = string.format("   %d:%02d", left // 60, left % 60)
		end
		status.Value = string.format("%s: %d  vs  %s: %d%s", teamA.Name, a, teamB.Name, b, timeText)
		task.wait(0.25)
	end

	status.Value = draw and "Ничья!" or ("Победили " .. winner.Name .. "!")
	task.wait(CONFIG.END_PAUSE)

	-- Выжившие — обратно в лобби, оружие пропадёт вместе со старым персонажем
	local survivors = {}
	for plr in pairs(alive) do
		table.insert(survivors, plr)
	end
	roundId += 1 -- старые обработчики смерти больше не срабатывают
	alive = {}
	for _, plr in ipairs(survivors) do
		if plr.Parent then
			plr.Team = lobbyTeam
			task.spawn(plr.LoadCharacter, plr)
		end
	end
end

-- Игроки -----------------------------------------------------------------

Players.PlayerAdded:Connect(function(plr)
	plr.Team = lobbyTeam -- зашедшие посреди раунда ждут следующего в лобби
end)
for _, plr in ipairs(Players:GetPlayers()) do
	plr.Team = lobbyTeam
end

Players.PlayerRemoving:Connect(function(plr)
	alive[plr] = nil -- вышедший считается выбывшим
end)

-- Главный цикл -----------------------------------------------------------

while true do
	while #Players:GetPlayers() < CONFIG.MIN_PLAYERS do
		status.Value = string.format("Ждём игроков (%d/%d)", #Players:GetPlayers(), CONFIG.MIN_PLAYERS)
		task.wait(1)
	end

	local cancelled = false
	for t = CONFIG.INTERMISSION, 1, -1 do
		if #Players:GetPlayers() < CONFIG.MIN_PLAYERS then
			cancelled = true
			break
		end
		status.Value = "Раунд начнётся через " .. t
		task.wait(1)
	end

	if not cancelled then
		runRound()
	end
end
