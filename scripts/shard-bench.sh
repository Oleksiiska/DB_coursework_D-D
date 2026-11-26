#!/usr/bin/env bash
# Порівняльний замір швидкості запису на 1, 2 і 3 шардах (п. 6.4 звіту).
#   ./scripts/shard-bench.sh [--build] [параметри заміру]
# --build — перезібрати образ після оновлення коду. Параметри заміру —
# python -m tools.shard_bench --help, наприклад: --documents 20000 --repeats 1.
# Кластер docker-compose.cluster.yml піднімається сам, якщо ще не запущений;
# звіт — logs/shard-bench/shard-bench-<дата>.md і .json.
set -euo pipefail
cd "$(dirname "$0")/.."
# на Linux процес заміру пише звіт від імені поточного користувача
export APP_UID="${APP_UID:-$(id -u)}" APP_GID="${APP_GID:-$(id -g)}"
args=(run --rm)
if [ "${1:-}" = "--build" ]; then
  args+=(--build)
  shift
fi
docker compose -f docker-compose.cluster.yml -f docker-compose.bench.yml "${args[@]}" shard-bench "$@"
echo "Звіт заміру: logs/shard-bench/"
