#!/usr/bin/env bash
# Зупинка системи.
#   ./scripts/stop.sh            — контейнери видаляються, дані лишаються в томах
#   ./scripts/stop.sh --pause    — лише зупинити контейнери (docker compose stop)
#   ./scripts/stop.sh --wipe     — видалити й дані сховищ (наступний запуск з нуля)
set -euo pipefail
cd "$(dirname "$0")/.."
dc() { docker compose -f docker-compose.cluster.yml "$@"; }
case "${1:-}" in
  "")
    dc down -t 60
    echo "Контейнери видалено, дані збережено; наступний запуск: ./scripts/start.sh" ;;
  --pause)
    dc stop -t 60
    echo "Контейнери зупинено; продовжити: docker compose -f docker-compose.cluster.yml start" ;;
  --wipe)
    read -r -p "Видалити всі дані сховищ? Для підтвердження введіть WIPE: " answer
    if [ "$answer" != WIPE ]; then echo "Скасовано."; exit 0; fi
    dc down -v -t 60
    echo "Контейнери й дані видалено." ;;
  *) echo "невідомий параметр: $1" >&2; exit 2 ;;
esac
