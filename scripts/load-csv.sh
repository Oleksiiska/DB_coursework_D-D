#!/usr/bin/env bash
# Завантаження підготовлених CSV (таблиця 2.3 звіту) у запущену систему.
#   ./scripts/load-csv.sh [каталог-з-CSV]
# Копіює CSV у data/csv (якщо задано каталог), запускає csv-loader, чекає, доки
# процес запису розбере чергу, перезапускає генератор і показує кількість
# документів у колекціях. Повторне завантаження безпечне: запис ідемпотентний.
set -euo pipefail
cd "$(dirname "$0")/.."
# на Linux процеси контейнерів пишуть журнали від імені поточного користувача
export APP_UID="${APP_UID:-$(id -u)}" APP_GID="${APP_GID:-$(id -g)}"
dc() { docker compose -f docker-compose.cluster.yml "$@"; }
files=(monsters.csv characters.csv encounters.csv participations.csv adventures.csv authors.csv tags.csv)
mkdir -p data/csv

running=$(dc ps --services --status running)
for s in redis ingest-worker api; do
  if ! grep -qx "$s" <<<"$running"; then
    echo "Система не запущена (немає $s). Спершу: ./scripts/start.sh" >&2
    exit 1
  fi
done

if [ $# -ge 1 ]; then
  for f in "${files[@]}"; do
    if [ -f "$1/$f" ]; then cp "$1/$f" data/csv/; fi
  done
fi

found=0
for f in "${files[@]}"; do
  if [ -f "data/csv/$f" ]; then
    found=$((found + 1))
    printf '  %-20s %10s КБ\n' "$f" "$(( ($(wc -c <"data/csv/$f") + 1023) / 1024 ))"
  else
    printf '  %-20s немає\n' "$f"
  fi
done
if [ "$found" -eq 0 ]; then echo "У каталозі data/csv немає жодного CSV." >&2; exit 1; fi

echo "Завантаження CSV (csv-loader)..."
dc run --rm csv-loader

echo "Очікування, доки процес запису розбере чергу..."
calm=0
for _ in $(seq 900); do
  sleep 2
  depth=$(dc exec -T redis redis-cli XLEN queue:documents | tr -dc '0-9')
  depth=${depth:-0}
  echo "  у черзі пакетів: $depth"
  if [ "$depth" -le 2 ]; then calm=$((calm + 1)); else calm=0; fi
  if [ "$calm" -ge 3 ]; then break; fi
done

echo "Перезапуск генератора, щоб він узяв монстрів, теги й тексти з CSV..."
dc restart generator

echo "Документів у колекціях:"
dc exec -T mongos mongosh dnd --quiet --eval 'db.getCollectionNames().sort().forEach(c => print(c.padEnd(16) + db[c].countDocuments({})))'
echo "Готово: у веб-інтерфейсі тепер є монстри, зустрічі й вкладка «Статистика гри»."
