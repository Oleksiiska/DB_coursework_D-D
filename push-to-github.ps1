#Requires -Version 5.1
<#
.SYNOPSIS
    Коміт і відправлення на GitHub змін етапів 13–14 (24–25.11.2026).
.DESCRIPTION
    1. Перевіряє git, автора комітів і зв'язок з GitHub (git fetch).
    2. Додає всі зміни робочої копії, крім самого скрипта, і ставить біт
       виконання bash-скриптам scripts/*.sh (Windows його не ставить).
    3. Показує, що піде в коміт, і питає підтвердження.
    4. Комітить, за потреби переносить коміт поверх нових комітів з GitHub
       (git pull --rebase) і пушить. Після успішного пушу видаляє сам себе.
    Повторний запуск безпечний: якщо коміт уже є, а пуш не вдався, скрипт
    лише допушить його.
.PARAMETER Yes
    Не питати підтвердження.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\push-to-github.ps1
#>
param([switch]$Yes)

# Помилки git перевіряються за кодом виходу: так прогрес git push, який git
# пише в stderr, не зупиняє скрипт у жодному терміналі
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Stop-Script([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Stop-Script 'Git не знайдено: встановіть Git for Windows (https://git-scm.com) і відкрийте нове вікно PowerShell.'
}

Set-Location -LiteralPath $PSScriptRoot -ErrorAction Stop
if (-not (Test-Path -LiteralPath '.git')) {
    Stop-Script "У $PSScriptRoot немає репозиторію: покладіть скрипт у корінь проєкту (поруч із README.md)."
}
$self = Split-Path -Leaf $PSCommandPath

foreach ($marker in 'MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD') {
    if (Test-Path -LiteralPath (Join-Path '.git' $marker)) {
        Stop-Script 'Не завершено злиття або rebase: доведіть його до кінця (git status підкаже як), потім запустіть скрипт ще раз.'
    }
}

$branch = "$(& git rev-parse --abbrev-ref HEAD)".Trim()
if (-not $branch -or $branch -eq 'HEAD') { Stop-Script 'Не вибрано гілку: виконайте git switch main і запустіть скрипт ще раз.' }

if (-not "$(& git config user.email)".Trim()) {
    Stop-Script ("Git не знає автора комітів. Виконайте один раз:`n" +
        "  git config --global user.name `"Ваше ім’я`"`n" +
        "  git config --global user.email `"пошта, з якою ви входите в GitHub`"")
}

Write-Host "Перевіряю зв’язок з GitHub (git fetch)…" -ForegroundColor Cyan
& git fetch origin
if ($LASTEXITCODE -ne 0) {
    Stop-Script 'Не вдалося зв’язатися з GitHub: перевірте інтернет і вхід у GitHub, потім запустіть скрипт ще раз.'
}

# --- коміт ---

& git add -A . ":(exclude)$self"
if ($LASTEXITCODE -ne 0) { Stop-Script 'git add не вдався — причина в повідомленні git вище.' }
$scripts = @(& git ls-files 'scripts/*.sh')
if ($scripts.Count -gt 0) {
    & git update-index --chmod=+x @scripts
    if ($LASTEXITCODE -ne 0) { Stop-Script 'Не вдалося позначити scripts/*.sh виконуваними.' }
}

$changes = @(& git -c core.quotepath=off diff --cached --name-status)
if ($changes.Count -gt 0) {
    $large = @()
    foreach ($line in $changes) {
        $parts = $line -split "`t"
        if ($parts[0] -like 'D*') { continue }
        $item = Get-Item -LiteralPath $parts[-1] -ErrorAction SilentlyContinue
        if ($item -and $item.Length -gt 50MB) { $large += "$($parts[-1]) — $([math]::Round($item.Length / 1MB)) МБ" }
    }
    if ($large.Count -gt 0) {
        & git reset -q
        Stop-Script ("Завеликі файли для GitHub (понад 50 МБ), коміт не створено:`n  " + ($large -join "`n  "))
    }

    & git -c core.quotepath=off status --short --untracked-files=no
    $added = @($changes | Where-Object { $_ -like 'A*' }).Count
    $modified = @($changes | Where-Object { $_ -like 'M*' }).Count
    $deleted = @($changes | Where-Object { $_ -like 'D*' }).Count
    Write-Host ("У коміт піде файлів: нових — {0}, змінених — {1}, видалених — {2}." -f $added, $modified, $deleted) -ForegroundColor Cyan

    if (-not $Yes) {
        $answer = Read-Host "Закомітити й відправити в origin/$branch? [y/N]"
        if ($answer -notmatch '^(y|yes|т|так|д|да)$') {
            & git reset -q
            Write-Host 'Скасовано: файли не змінено, у git нічого не записано.'
            exit 0
        }
    }

    $message = @'
Stages 13-14: shard write benchmark and niche map-reduce analytics

Stage 13 - partitioning and sharding:
- ZoneBounds and ShardKeyService.zones(): zone bounds for N shards
- tools/shard_bench: the adventures collection written to 1, 2 and 3
  shards through the production write path (scripts/shard-bench,
  docker-compose.bench.yml); report in logs/shard-bench/

Stage 14 - one query over all shards (map-reduce):
- query group 11 "niche analytics": map per shard zone through mongos
  on one read snapshot, NicheStats reduce in the application, check
  against the built-in aggregation
- GET /api/analytics/niches and the Atlas page

Also:
- tests: 237 unit tests in 23 modules, 25 integration tests in 7 modules
- regenerated diagrams (docs/diagrams, docs/conceptual) and README
- scripts/*.sh marked executable

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XCwVUPozLGKQfm9Uzxga29
'@
    $messageFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($messageFile, ($message -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
    & git commit -q -F $messageFile
    $code = $LASTEXITCODE
    Remove-Item -LiteralPath $messageFile -ErrorAction SilentlyContinue
    if ($code -ne 0) { Stop-Script 'Коміт не створено — причина в повідомленні git вище.' }
    Write-Host "Коміт: $(& git log -1 --format='%h %s')" -ForegroundColor Green
} else {
    Write-Host 'Нових змін немає — перевіряю, чи все вже на GitHub.' -ForegroundColor Yellow
}

# --- відправлення ---

$remote = "$(& git rev-parse --verify --quiet "refs/remotes/origin/$branch")".Trim()
if ($remote) {
    $behind = [int]("$(& git rev-list --count "HEAD..origin/$branch")".Trim())
    $ahead = [int]("$(& git rev-list --count "origin/$branch..HEAD")".Trim())
} else {
    $behind = 0
    $ahead = 1
}
if ($ahead -eq 0) {
    Write-Host "Усе вже на GitHub (origin/$branch)." -ForegroundColor Green
    exit 0
}
if ($behind -gt 0) {
    Write-Host "На GitHub є нові коміти ($behind) — переношу свій коміт поверх них (git pull --rebase)…" -ForegroundColor Yellow
    & git pull --rebase origin $branch
    if ($LASTEXITCODE -ne 0) {
        Stop-Script 'Rebase зупинився на конфлікті: виправте файли, виконайте git add <файл> і git rebase --continue, потім запустіть скрипт ще раз.'
    }
}

& git push -u origin $branch
if ($LASTEXITCODE -ne 0) {
    Stop-Script 'Пуш не вдався, коміт збережено локально. Перевірте вхід у GitHub і запустіть скрипт ще раз — він лише допушить коміт.'
}

$web = "$(& git remote get-url origin)".Trim() -replace '\.git$', ''
Write-Host "Готово: $web/commit/$(& git rev-parse --short HEAD)" -ForegroundColor Green
Remove-Item -LiteralPath $PSCommandPath -ErrorAction SilentlyContinue
