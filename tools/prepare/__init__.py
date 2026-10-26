"""Офлайн-підготовка даних (розділ 2 звіту, таблиця 2.4, кроки 1–4).

Завантажує початкові датасети, перетворює їх на CSV таблиці 2.3, перевіряє
CSV моделлю застосунку й рахує виміри, на які спираються розділ 2 і додаток А:

* ``srd`` — крок 1: 5e SRD (репозиторій 5e-bits/5e-database, зафіксований
  коміт): ``monsters.csv``, назви заклять і службові словники для зіставлення;
* ``fireball`` — кроки 2–3: архів FIREBALL, два потокові проходи (``filtered`` —
  учасники, ``data`` — метадані зустрічей): ``characters.csv``,
  ``participations.csv``, ``encounters.csv`` і ``play_count`` монстрів;
* ``catalog`` — крок 4: обхід каталогу itch.io тим самим CrawlerSource, що
  працює в застосунку, зі знімком отриманих сторінок: ``adventures.csv``,
  ``authors.csv``, ``tags.csv``;
* ``check`` — кожен рядок CSV проходить підготовку документа зі шляху запису
  (IngestDocumentHandler) без сховищ, плюс посилальна цілісність між файлами;
* ``report`` — звіт вимірювань ``measurements.md`` і ``measurements.json``.

Результат — каталог ``data/csv``; застосунок читає його процесом ``csv-loader``.
Запуск — у контейнері образу застосунку (``docker-compose.prepare.yml``)::

    docker compose -f docker-compose.prepare.yml run --rm prepare

або скриптом ``scripts/prepare-data`` (README, «Підготовка даних»).
"""
