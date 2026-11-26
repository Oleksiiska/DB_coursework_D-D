#Requires -Version 5.1
<#
.SYNOPSIS
    Порівняльний замір швидкості запису на 1, 2 і 3 шардах (п. 6.4 звіту).
.DESCRIPTION
    Запускає tools.shard_bench у контейнері з тим самим образом, що й система, у мережі
    кластера docker-compose.cluster.yml; кластер піднімається сам, якщо ще не запущений.
    Колекції заміру лежать в окремій базі dnd_bench і після заміру видаляються.
    Звіт — logs\shard-bench\shard-bench-<дата>.md і .json.
    Інші параметри передаються заміру без змін (перелік — --help).
.PARAMETER Build
    Перезібрати образ перед заміром (після оновлення коду).
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\shard-bench.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\shard-bench.ps1 --documents 20000 --repeats 1
#>
param([switch]$Build)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

$compose = @('compose', '-f', 'docker-compose.cluster.yml', '-f', 'docker-compose.bench.yml', 'run', '--rm')
if ($Build) { $compose += '--build' }
& docker @compose shard-bench @args
if ($LASTEXITCODE -ne 0) {
    Write-Host "Замір завершився з кодом $LASTEXITCODE. Журнал: logs\shard-bench.log" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host 'Звіт заміру: logs\shard-bench\' -ForegroundColor Green
