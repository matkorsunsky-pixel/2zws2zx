# Межа → Discord
# Пока это окно открыто, в вашем профиле Discord видно «Играет в Межа»: война, страна и дата.
# Как это работает: игра (программа или страница в браузере) каждые 15 секунд отправляет свой статус
# сюда, на http://localhost:47392 — только на ваш компьютер, в интернет ничего не уходит. Скрипт передаёт
# статус в Discord через его локальный канал (discord-ipc), как это делают все игры с «Rich Presence».
# Скрипт ничего не устанавливает и ничего не меняет в системе. Закройте окно — статус пропадёт.

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Host.UI.RawUI.WindowTitle = 'Межа → Discord'
$port = 47392
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$port/")
try { $listener.Start() } catch {
  Write-Host "Не удалось занять порт $port. Возможно, помощник уже запущен в другом окне." -ForegroundColor Red
  Read-Host 'Нажмите Enter, чтобы закрыть'; exit 1
}
Write-Host ''
Write-Host '  Межа → Discord: помощник работает.' -ForegroundColor Yellow
Write-Host '  1. Запустите Discord (программу на компьютере, не сайт).'
Write-Host '  2. В Меже: Настройки → Discord → «Показывать игру в Discord» и ID приложения.'
Write-Host '  Не закрывайте это окно, пока играете.'
Write-Host ''

$script:pipe = $null
$script:clientId = ''
$script:buf = New-Object byte[] 65536

function Send-Frame([int]$op, [string]$json) {
  $b = [Text.Encoding]::UTF8.GetBytes($json)
  $h = [BitConverter]::GetBytes($op) + [BitConverter]::GetBytes([int]$b.Length)
  $script:pipe.Write($h, 0, 8)
  $script:pipe.Write($b, 0, $b.Length)
  $script:pipe.Flush()
  $null = $script:pipe.Read($script:buf, 0, $script:buf.Length)
}
function Connect-Discord([string]$id) {
  if ($script:pipe) { try { $script:pipe.Dispose() } catch {} ; $script:pipe = $null }
  for ($i = 0; $i -lt 10; $i++) {
    try {
      $c = New-Object System.IO.Pipes.NamedPipeClientStream('.', "discord-ipc-$i", [System.IO.Pipes.PipeDirection]::InOut)
      $c.Connect(400)
      $script:pipe = $c
      break
    } catch {}
  }
  if (-not $script:pipe) { return $false }
  Send-Frame 0 (@{ v = 1; client_id = $id } | ConvertTo-Json -Compress)
  $script:clientId = $id
  Write-Host '  Discord подключён.' -ForegroundColor Green
  return $true
}

while ($listener.IsListening) {
  $ctx = $listener.GetContext()
  $req = $ctx.Request
  $res = $ctx.Response
  $res.Headers.Add('Access-Control-Allow-Origin', '*')
  $res.Headers.Add('Access-Control-Allow-Headers', 'Content-Type')
  $res.Headers.Add('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
  $res.Headers.Add('Access-Control-Allow-Private-Network', 'true')
  $code = 200
  $out = 'ok'
  try {
    if ($req.HttpMethod -eq 'POST') {
      $reader = New-Object IO.StreamReader($req.InputStream, [Text.Encoding]::UTF8)
      $d = $reader.ReadToEnd() | ConvertFrom-Json
      if ($d.client_id -notmatch '^\d{15,22}$') { throw 'Неверный ID приложения Discord' }
      if (-not $script:pipe -or $d.client_id -ne $script:clientId) {
        if (-not (Connect-Discord $d.client_id)) { $code = 503; $out = 'discord-not-running' }
      }
      if ($script:pipe) {
        $msg = @{ cmd = 'SET_ACTIVITY'; nonce = [guid]::NewGuid().ToString(); args = @{ pid = $PID; activity = $d.activity } } | ConvertTo-Json -Depth 8 -Compress
        try { Send-Frame 1 $msg; Write-Host ('  ' + (Get-Date).ToString('HH:mm') + '  ' + $d.activity.details + ' — ' + $d.activity.state) }
        catch { $script:pipe = $null; $code = 503; $out = 'discord-lost' }
      }
    } elseif ($req.HttpMethod -eq 'GET') { $out = 'mezha-discord' }
  } catch { $code = 400; $out = "$($_.Exception.Message)" }
  $bytes = [Text.Encoding]::UTF8.GetBytes($out)
  $res.StatusCode = $code
  try { $res.OutputStream.Write($bytes, 0, $bytes.Length) } catch {}
  $res.Close()
}
