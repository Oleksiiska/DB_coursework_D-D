#Requires -Version 5.1
<#
.SYNOPSIS
    Запуск повної системи (docker-compose.cluster.yml) і відкриття веб-інтерфейсу.
.DESCRIPTION
    Піднімає всі контейнери у фоні, чекає, доки API почне відповідати, і відкриває
    сторінку в браузері. Порт API береться зі змінної середовища API_PORT або з
    файлу .env (за замовчуванням 8000).
.PARAMETER Build
    Перезібрати образ застосунку — після оновлення коду.
.PARAMETER NoCrawler
    Не запускати краулер itch.io (повний обхід триває години й потребує інтернету).
.PARAMETER NoBrowser
    Не відкривати браузер.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Build
#>
param([switch]$Build, [switch]$NoCrawler, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

function Invoke-Compose {
    & docker compose -f docker-compose.cluster.yml @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($args -join ' ') завершився з кодом $LASTEXITCODE" }
}

function Get-Setting([string]$Name, [string]$Default) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ($value) { return $value }
    if (Test-Path -LiteralPath '.env') {
        foreach ($line in Get-Content -LiteralPath '.env') {
            if ($line -match "^\s*$Name\s*=\s*(\S+)") { return $Matches[1] }
        }
    }
    return $Default
}

$upArgs = @('up', '-d')
if ($Build) { $upArgs += '--build' }
if ($NoCrawler) { $upArgs += @('--scale', 'crawler=0') }
try {
    Invoke-Compose @upArgs
} catch {
    Write-Host $_ -ForegroundColor Red
    Write-Host 'Якщо порт зайнятий, задайте інший у файлі .env: MONGOS_PORT=27018 або API_PORT=8080.' -ForegroundColor Yellow
    exit 1
}

$url = "http://127.0.0.1:$(Get-Setting 'API_PORT' '8000')"
Write-Host "Очікування API на $url ..."
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    try {
        Invoke-RestMethod -Uri "$url/api/rate" -TimeoutSec 5 | Out-Null
        Write-Host "Систему запущено: $url" -ForegroundColor Green
        if (-not $NoBrowser) { Start-Process $url }
        exit 0
    } catch {
        Start-Sleep -Seconds 3
    }
}
Write-Host 'API не відповідає 5 хв. Журнал: docker compose -f docker-compose.cluster.yml logs api' -ForegroundColor Red
exit 1
