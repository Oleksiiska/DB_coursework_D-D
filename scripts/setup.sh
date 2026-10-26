#!/usr/bin/env bash
# Усе з нуля: підготовка даних, потім запуск системи з веб-інтерфейсом.
#   ./scripts/setup.sh [--quick] [--no-crawler] [--no-browser]
# Якщо частину кроків підготовки не виконано, система однаково запускається з тими CSV, що є.
set -euo pipefail
cd "$(dirname "$0")/.."
prepare=()
start=(--build)
for a in "$@"; do
  case "$a" in
    --quick) prepare+=(--quick) ;;
    --no-crawler|--no-browser) start+=("$a") ;;
    *) echo "невідомий параметр: $a" >&2; exit 2 ;;
  esac
done
set +e
./scripts/prepare-data.sh ${prepare[@]+"${prepare[@]}"}
code=$?
set -e
if [ "$code" -ne 0 ] && [ "$code" -ne 3 ]; then exit "$code"; fi
exec ./scripts/start.sh "${start[@]}"
