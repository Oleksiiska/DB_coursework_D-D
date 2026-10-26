#!/usr/bin/env bash
# Запуск повної системи й відкриття веб-інтерфейсу.
#   ./scripts/start.sh [--build] [--no-crawler] [--no-browser]
# --build — перезібрати образ після оновлення коду; --no-crawler — без краулера itch.io.
set -euo pipefail
cd "$(dirname "$0")/.."
# на Linux процеси контейнерів пишуть журнали від імені поточного користувача
export APP_UID="${APP_UID:-$(id -u)}" APP_GID="${APP_GID:-$(id -g)}"
args=(up -d)
browser=1
for a in "$@"; do
  case "$a" in
    --build) args+=(--build) ;;
    --no-crawler) args+=(--scale crawler=0) ;;
    --no-browser) browser=0 ;;
    *) echo "невідомий параметр: $a" >&2; exit 2 ;;
  esac
done
if ! docker compose -f docker-compose.cluster.yml "${args[@]}"; then
  echo "Якщо порт зайнятий, задайте інший у файлі .env: MONGOS_PORT=27018 або API_PORT=8080." >&2
  exit 1
fi
port="${API_PORT:-}"
if [ -z "$port" ] && [ -f .env ]; then
  port=$(sed -n 's/^[[:space:]]*API_PORT[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' .env | head -n 1)
fi
url="http://127.0.0.1:${port:-8000}"
echo "Очікування API на $url ..."
for _ in $(seq 100); do
  if curl -fs "$url/api/rate" >/dev/null 2>&1; then
    echo "Систему запущено: $url"
    if [ "$browser" = 1 ]; then
      if command -v xdg-open >/dev/null; then xdg-open "$url" >/dev/null 2>&1 || true
      elif command -v open >/dev/null; then open "$url" || true; fi
    fi
    exit 0
  fi
  sleep 3
done
echo "API не відповідає 5 хв. Журнал: docker compose -f docker-compose.cluster.yml logs api" >&2
exit 1
