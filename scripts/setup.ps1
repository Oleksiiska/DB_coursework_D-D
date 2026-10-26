#Requires -Version 5.1
<#
.SYNOPSIS
    Усе з нуля: підготовка даних, а потім запуск системи з веб-інтерфейсом.
.DESCRIPTION
    Послідовно виконує scripts\prepare-data.ps1 і scripts\start.ps1 -Build. Якщо частину
    кроків підготовки не виконано (наприклад, недоступний архів FIREBALL), система
    однаково запускається з тими CSV, що є. csv-loader завантажує CSV із data\csv під
    час запуску, генератор калібрується за ними ж.
.PARAMETER Quick
    Вибірка 100 карток каталогу замість повного обходу (≈ 10 хв замість ≈ 2 год).
.PARAMETER NoCrawler
    Не запускати краулер у системі (каталог уже зібрано під час підготовки).
.PARAMETER NoBrowser
    Не відкривати браузер.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Quick
#>
param([switch]$Quick, [switch]$NoCrawler, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$prepare = @{}
if ($Quick) { $prepare.Quick = $true }
& (Join-Path $PSScriptRoot 'prepare-data.ps1') @prepare
$code = $LASTEXITCODE
if ($code -ne 0 -and $code -ne 3) { exit $code }

$start = @{ Build = $true }
if ($NoCrawler) { $start.NoCrawler = $true }
if ($NoBrowser) { $start.NoBrowser = $true }
& (Join-Path $PSScriptRoot 'start.ps1') @start
exit $LASTEXITCODE
