#Requires -Version 5.1
<#
.SYNOPSIS
    Зупинка системи. За замовчуванням контейнери видаляються, а дані лишаються в томах.
.PARAMETER Pause
    Лише зупинити контейнери (docker compose stop): найшвидший повторний старт.
.PARAMETER Wipe
    Видалити й дані сховищ (томи MongoDB, Elasticsearch, Neo4j, Redis):
    наступний запуск почнеться з порожніх сховищ.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
#>
param([switch]$Pause, [switch]$Wipe)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

function Invoke-Compose {
    & docker compose -f docker-compose.cluster.yml @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($args -join ' ') завершився з кодом $LASTEXITCODE" }
}

if ($Pause -and $Wipe) { Write-Host 'Оберіть щось одне: -Pause або -Wipe.' -ForegroundColor Red; exit 2 }
if ($Wipe) {
    $answer = Read-Host 'Видалити всі дані сховищ? Для підтвердження введіть WIPE'
    if ($answer -cne 'WIPE') { Write-Host 'Скасовано.'; exit 0 }
    Invoke-Compose down -v -t 60
    Write-Host 'Контейнери й дані видалено.' -ForegroundColor Green
} elseif ($Pause) {
    Invoke-Compose stop -t 60
    Write-Host 'Контейнери зупинено; продовжити: docker compose -f docker-compose.cluster.yml start' -ForegroundColor Green
} else {
    Invoke-Compose down -t 60
    Write-Host 'Контейнери видалено, дані збережено; наступний запуск: scripts\start.ps1' -ForegroundColor Green
}
Write-Host "Щоб звільнити пам’ять, закрийте й Docker Desktop: значок у треї → Quit Docker Desktop."
