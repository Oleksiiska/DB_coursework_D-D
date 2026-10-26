#!/usr/bin/env bash
# Підготовка даних (розділ 2 звіту): 5e SRD, FIREBALL (архів ≈ 7,7 ГБ, один раз),
# каталог itch.io (повний обхід ≈ 2 год, паралельно з FIREBALL), перевірка CSV
# моделлю застосунку і звіт вимірювань data/csv/measurements.md.
#   ./scripts/prepare-data.sh [--quick] [--catalog-sample N] [--fresh-catalog]
#                             [--skip-catalog] [--skip-fireball] [--offline]
# --quick — вибірка 100 карток каталогу (≈ 10 хв); повторний запуск продовжує з місця зупинки.
set -euo pipefail
cd "$(dirname "$0")/.."
export APP_UID="${APP_UID:-$(id -u)}" APP_GID="${APP_GID:-$(id -g)}"
archive=data/raw/fireball/fireball-anonymized-nov-28-2022-kfdjl.tar.gz
args=()
sample=""
skip_fireball=0
while [ $# -gt 0 ]; do
  case "$1" in
    --quick) sample="${sample:-100}" ;;
    --catalog-sample) shift; sample="$1" ;;
    --fresh-catalog) args+=(--catalog-fresh) ;;
    --skip-catalog) args+=(--skip-catalog) ;;
    --skip-fireball) args+=(--skip-fireball); skip_fireball=1 ;;
    --offline) args+=(--offline) ;;
    *) echo "невідомий параметр: $1" >&2; exit 2 ;;
  esac
  shift
done
if [ -n "$sample" ]; then args+=(--catalog-sample "$sample"); fi
mkdir -p data/csv data/raw logs

if [ "$skip_fireball" = 0 ] && [ ! -f "$archive" ]; then
  free_kb=$(df -Pk data/raw | awk 'NR == 2 {print $4}')
  if [ "${free_kb:-0}" -lt $((9 * 1024 * 1024)) ]; then
    echo "Вільно $((free_kb / 1024 / 1024)) ГБ, а архіву FIREBALL потрібно ≈ 8 ГБ." >&2
  fi
  echo "Архів FIREBALL (≈ 7,7 ГБ) буде завантажено в data/raw/fireball; якщо він уже є, покладіть його туди."
fi

echo "Збирання образу застосунку..."
docker compose -f docker-compose.prepare.yml build
echo "Підготовка даних (журнал також у logs/prepare.log)..."
set +e
docker compose -f docker-compose.prepare.yml run --rm prepare ${args[@]+"${args[@]}"}
code=$?
set -e
case "$code" in
  0) echo "Дані підготовлено: data/csv. Звіт вимірювань: data/csv/measurements.md"
     echo "Далі: ./scripts/start.sh" ;;
  3) echo "Частину кроків не виконано — причини вище і в data/csv/measurements.md." >&2
     echo "Повторний запуск продовжить з місця зупинки. Систему можна запускати й з тим, що є: ./scripts/start.sh" ;;
  130) echo "Підготовку перервано. Повторний запуск продовжить з місця зупинки." >&2 ;;
  *) echo "Підготовка завершилася з кодом $code — див. logs/prepare.log." >&2 ;;
esac
exit "$code"
