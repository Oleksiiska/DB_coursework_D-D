#Requires -Version 5.1
<#
.SYNOPSIS
    Завантаження підготовлених CSV (таблиця 2.3 звіту) у запущену систему.
.DESCRIPTION
    1. За потреби копіює CSV із каталогу -From у data\csv.
    2. Запускає csv-loader і показує його журнал.
    3. Чекає, доки процес запису розбере чергу.
    4. Перезапускає генератор, щоб він узяв із CSV монстрів, теги й тексти.
    5. Показує кількість документів у кожній колекції MongoDB.
    Повторне завантаження тих самих файлів безпечне: запис ідемпотентний.
.PARAMETER From
    Каталог із CSV, якщо вони лежать не в data\csv.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\load-csv.ps1 -From D:\datasets\csv
#>
param([string]$From)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

function Invoke-Compose {
    & docker compose -f docker-compose.cluster.yml @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($args -join ' ') завершився з кодом $LASTEXITCODE" }
}

$files = 'monsters.csv', 'characters.csv', 'encounters.csv', 'participations.csv', 'adventures.csv', 'authors.csv', 'tags.csv'
$target = Join-Path 'data' 'csv'
New-Item -ItemType Directory -Force -Path $target | Out-Null

$running = & docker compose -f docker-compose.cluster.yml ps --services --status running
if ($LASTEXITCODE -ne 0) { Write-Host 'Docker не відповідає — запустіть Docker Desktop.' -ForegroundColor Red; exit 1 }
foreach ($service in 'redis', 'ingest-worker', 'api') {
    if ($running -notcontains $service) {
        Write-Host "Система не запущена (немає $service). Спершу: powershell -ExecutionPolicy Bypass -File scripts\start.ps1" -ForegroundColor Red
        exit 1
    }
}

if ($From) {
    foreach ($name in $files) {
        $source = Join-Path $From $name
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination $target -Force }
    }
}

$present = 0
foreach ($name in $files) {
    $path = Join-Path $target $name
    if (Test-Path -LiteralPath $path) {
        $present++
        Write-Host ('  {0,-20} {1,10:N0} КБ' -f $name, [math]::Ceiling((Get-Item -LiteralPath $path).Length / 1KB))
    } else {
        Write-Host ('  {0,-20} немає' -f $name) -ForegroundColor Yellow
    }
}
if ($present -eq 0) { Write-Host "У каталозі $target немає жодного CSV." -ForegroundColor Red; exit 1 }

Write-Host 'Завантаження CSV (csv-loader)...'
Invoke-Compose run --rm csv-loader

Write-Host 'Очікування, доки процес запису розбере чергу...'
$calm = 0
$deadline = (Get-Date).AddMinutes(30)
while ($calm -lt 3 -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $out = & docker compose -f docker-compose.cluster.yml exec -T redis redis-cli XLEN queue:documents
    $depth = 0
    [void][int]::TryParse("$($out | Select-Object -Last 1)".Trim(), [ref]$depth)
    if ($depth -le 2) { $calm++ } else { $calm = 0 }
    Write-Host "  у черзі пакетів: $depth"
}

Write-Host 'Перезапуск генератора, щоб він узяв монстрів, теги й тексти з CSV...'
Invoke-Compose restart generator

Write-Host 'Документів у колекціях:'
Invoke-Compose exec -T mongos mongosh dnd --quiet --eval 'db.getCollectionNames().sort().forEach(c => print(c.padEnd(16) + db[c].countDocuments({})))'
Write-Host 'Готово: у веб-інтерфейсі тепер є монстри, зустрічі й вкладка «Статистика гри».' -ForegroundColor Green
