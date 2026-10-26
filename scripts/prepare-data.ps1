#Requires -Version 5.1
<#
.SYNOPSIS
    Підготовка даних (розділ 2 звіту): завантаження датасетів, CSV таблиці 2.3 і звіт вимірювань.
.DESCRIPTION
    Запускає tools/prepare в образі застосунку (docker-compose.prepare.yml):
      1. 5e SRD — monsters.csv, назви заклять і словники (≈ 3 МБ);
      2–3. FIREBALL — characters.csv, encounters.csv, participations.csv і популярність
           монстрів; архів ≈ 7,7 ГБ завантажується один раз у data\raw\fireball;
      4. каталог itch.io — adventures.csv, authors.csv, tags.csv; повний обхід із
         ввічливою затримкою триває близько 2 годин і йде паралельно з кроками 2–3.
    Потім CSV перевіряються моделлю застосунку, а виміри записуються в
    data\csv\measurements.md. Повторний запуск безпечний: завантаження продовжується
    з місця обриву, а каталог відтворюється зі знімка останнього обходу.
.PARAMETER Quick
    Випадкова вибірка 100 карток каталогу замість повного обходу (≈ 10 хв). Решту
    каталогу збере краулер запущеної системи.
.PARAMETER CatalogSample
    Розмір випадкової вибірки карток (замість 100 у -Quick).
.PARAMETER FreshCatalog
    Новий обхід каталогу навіть за наявного знімка в data\raw\itch.
.PARAMETER SkipCatalog
    Без каталогу itch.io.
.PARAMETER SkipFireball
    Без FIREBALL (зустрічі, персонажі, статистика гри будуть порожні).
.PARAMETER Offline
    Без мережі: лише вже завантажені файли з data\raw.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prepare-data.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prepare-data.ps1 -Quick
#>
param([switch]$Quick, [int]$CatalogSample = 0, [switch]$FreshCatalog, [switch]$SkipCatalog,
      [switch]$SkipFireball, [switch]$Offline)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

$archive = 'data\raw\fireball\fireball-anonymized-nov-28-2022-kfdjl.tar.gz'
foreach ($dir in 'data\csv', 'data\raw', 'logs') { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

if (-not $SkipFireball -and -not (Test-Path -LiteralPath $archive)) {
    try {
        $drive = Get-PSDrive -Name (Get-Location).Drive.Name
        if ($drive.Free -lt 9GB) {
            Write-Host ("На диску {0}: вільно {1:N1} ГБ, а архіву FIREBALL потрібно ≈ 8 ГБ." -f $drive.Name, ($drive.Free / 1GB)) -ForegroundColor Yellow
        }
    } catch { }
    Write-Host 'Архів FIREBALL (≈ 7,7 ГБ) буде завантажено в data\raw\fireball; якщо він уже є, покладіть його туди.'
}

$prepareArgs = @()
if ($Quick -and -not $CatalogSample) { $CatalogSample = 100 }
if ($CatalogSample -gt 0) { $prepareArgs += @('--catalog-sample', "$CatalogSample") }
if ($FreshCatalog) { $prepareArgs += '--catalog-fresh' }
if ($SkipCatalog) { $prepareArgs += '--skip-catalog' }
if ($SkipFireball) { $prepareArgs += '--skip-fireball' }
if ($Offline) { $prepareArgs += '--offline' }

Write-Host 'Збирання образу застосунку...'
& docker compose -f docker-compose.prepare.yml build
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Образ не зібрано. Чи запущено Docker Desktop?' -ForegroundColor Red
    exit 1
}

Write-Host 'Підготовка даних (журнал також у logs\prepare.log)...'
& docker compose -f docker-compose.prepare.yml run --rm prepare @prepareArgs
$code = $LASTEXITCODE

switch ($code) {
    0 {
        Write-Host 'Дані підготовлено: data\csv. Звіт вимірювань: data\csv\measurements.md' -ForegroundColor Green
        Write-Host 'Далі: powershell -ExecutionPolicy Bypass -File scripts\start.ps1'
    }
    3 {
        Write-Host 'Частину кроків не виконано — причини вище і в data\csv\measurements.md.' -ForegroundColor Yellow
        Write-Host 'Повторний запуск цього скрипту продовжить з місця зупинки. Систему можна запускати й з тим, що є: scripts\start.ps1'
    }
    130 { Write-Host 'Підготовку перервано. Повторний запуск продовжить з місця зупинки.' -ForegroundColor Yellow }
    default { Write-Host "Підготовка завершилася з кодом $code — див. logs\prepare.log." -ForegroundColor Red }
}
exit $code
