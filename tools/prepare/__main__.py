"""Офлайн-підготовка даних: ``python -m tools.prepare [параметри]``.

Кроки таблиці 2.4: 1 — SRD; 2–3 — FIREBALL і популярність монстрів;
4 — каталог itch.io. Крок 4 від кроків 1–3 не залежить і виконується
паралельно з ними (рисунок 2.1): обхід обмежений ввічливою затримкою, а
розбір FIREBALL — процесором. Після кроків — перевірка CSV моделлю
застосунку і звіт вимірювань.

Повторний запуск безпечний і швидкий: завантажені файли беруться з
``data/raw``, а каталог — зі знімка останнього обходу, якщо не задано
``--catalog-fresh``.

Коди завершення: 0 — усі кроки виконано; 3 — частину кроків не виконано
(решта CSV записано); 1 — підготовка неможлива.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import tarfile
import threading
import time
import zlib
from collections import Counter
from pathlib import Path

from tools.prepare import catalog, check, fireball, srd
from tools.prepare.report import Measurements, number
from tools.prepare.sources import DownloadError, download, file_name, fireball_url

log = logging.getLogger("prepare")

EXIT_OK, EXIT_FAILED, EXIT_PARTIAL = 0, 1, 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env = os.environ
    parser = argparse.ArgumentParser(
        prog="python -m tools.prepare",
        description="Офлайн-підготовка даних (кроки 1–4 таблиці 2.4): 5e SRD, FIREBALL і каталог itch.io → "
                    "CSV таблиці 2.3, перевірка їх моделлю застосунку й звіт вимірювань measurements.md.",
        epilog="Коди завершення: 0 — усі кроки виконано; 3 — частину кроків не виконано (решту CSV записано); "
               "1 — підготовка неможлива.")
    parser.add_argument("--out", type=Path, default=Path(env.get("CSV_DIR", "data/csv")),
                        help="каталог CSV (за замовчуванням data/csv)")
    parser.add_argument("--raw", type=Path, default=Path(env.get("RAW_DIR", "data/raw")),
                        help="каталог завантажених датасетів і знімків обходу (data/raw)")
    parser.add_argument("--logs", type=Path, default=Path(env.get("LOG_DIR", "logs")),
                        help="каталог журналу prepare.log (logs)")
    parser.add_argument("--skip-fireball", action="store_true", help="без кроків 2–3 (FIREBALL)")
    parser.add_argument("--skip-catalog", action="store_true", help="без кроку 4 (каталог itch.io)")
    parser.add_argument("--fireball-archive", type=Path, default=Path(env["FIREBALL_ARCHIVE"])
                        if env.get("FIREBALL_ARCHIVE") else None, help="вже завантажений архів FIREBALL")
    parser.add_argument("--fireball-limit", type=int, default=None, help="лише перші N боїв (перевірка)")
    parser.add_argument("--catalog-sample", type=int, default=int(env["CATALOG_SAMPLE"])
                        if env.get("CATALOG_SAMPLE") else None, help="випадкова вибірка N карток замість усіх")
    parser.add_argument("--catalog-fresh", action="store_true", help="новий обхід навіть за наявного знімка")
    parser.add_argument("--catalog-replay", type=Path, default=None, help="відтворити обхід зі знімка")
    parser.add_argument("--offline", action="store_true", help="без мережі: лише файли з data/raw і знімок обходу")
    return parser.parse_args(argv)


def configure_logging(log_dir: Path) -> None:
    """Журнал у консоль і в logs/prepare.log (дописується)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in [h for h in root.handlers if getattr(h, "prepare", False)]:
        root.removeHandler(handler)
        handler.close()
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_dir / "prepare.log", encoding="utf-8")):
        handler.setFormatter(formatter)
        handler.prepare = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    for quiet in ("httpx", "httpcore"):
        logging.getLogger(quiet).setLevel(logging.WARNING)


class CatalogStep:
    """Крок 4 в окремому потоці з власним циклом подій."""

    def __init__(self, args: argparse.Namespace, settings: object, systems: list[dict]) -> None:
        self.args = args
        self.settings = settings
        self.systems = systems
        self.result: catalog.CatalogResult | None = None
        self.counts: dict[str, int] = {}
        self.error: BaseException | None = None
        self.replay: Path | None = None

    def choose_replay(self) -> Path | None:
        """Знімок для відтворення: повний обхід придатний для будь-якого запуску,
        вибірка — лише для вибірки того самого розміру."""
        if self.args.catalog_replay:
            return self.args.catalog_replay
        wanted = self.args.catalog_sample
        snapshots = []
        for path in sorted((self.args.raw / "itch").glob("crawl-*.jsonl.gz")):
            mode = re.search(r"-(full|sample(\d+))\.jsonl\.gz$", path.name)
            if mode and (mode.group(1) == "full" or (wanted is not None and int(mode.group(2)) == wanted)):
                snapshots.append(path)
        if self.args.offline or (snapshots and not self.args.catalog_fresh):
            if not snapshots:
                raise FileNotFoundError("придатного знімка обходу немає, а мережу вимкнено (--offline)")
            return snapshots[-1]
        return None

    def run(self) -> None:
        try:
            self.replay = self.choose_replay()
            if self.replay:
                log.info("каталог: відтворення обходу зі знімка %s", self.replay)
            else:
                sample = self.args.catalog_sample
                log.info("каталог: обхід itch.io за тегом %s (%s); затримка 1,5–3 с між запитами",
                         getattr(self.settings, "crawler_tag", "dnd"),
                         f"вибірка {sample} карток" if sample else "усі картки, близько 2 годин")
            self.result = asyncio.run(catalog.crawl(self.settings, self.systems, self.args.raw,
                                                    sample=self.args.catalog_sample, replay=self.replay))
            if not self.result.adventures:
                raise RuntimeError("жодної картки не отримано — див. попередження краулера в журналі")
            self.counts = catalog.write_csv(self.result, self.args.out)
        except BaseException as error:  # noqa: BLE001 — помилку кроку показує звіт, решта кроків триває
            self.error = error
            log.error("каталог: %s", error)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.logs)
    from app.infrastructure.config.container import Container, Settings

    settings = Settings.from_env()
    container = Container(settings)
    args.out.mkdir(parents=True, exist_ok=True)
    m = Measurements()
    for name in ("1. SRD", "2–3. FIREBALL", "4. Каталог itch.io"):
        m.step(name, "не виконано")
    status = EXIT_OK
    started = time.monotonic()
    log.info("підготовка даних: CSV → %s, датасети → %s", args.out, args.raw)

    # крок 1 — SRD
    try:
        reference = srd.load(args.raw, offline=args.offline)
    except Exception as error:  # noqa: BLE001
        log.error("SRD: %s", error)
        m.step("1. SRD", f"помилка: {error}")
        m.save(args.out)
        return EXIT_FAILED
    m.snapshots["5e SRD"] = f"5e-bits/5e-database, коміт {reference.commit}, каталог src/2014/en"
    srd.measure(reference, m)

    # крок 4 — паралельно з кроками 2–3
    catalog_step = CatalogStep(args, settings, container.manual_list("systems.json"))
    worker = None
    if args.skip_catalog:
        m.step("4. Каталог itch.io", "пропущено (--skip-catalog)")
    else:
        worker = threading.Thread(target=catalog_step.run, name="catalog", daemon=True)
        worker.start()

    # кроки 2–3 — FIREBALL
    play_counts: Counter = Counter()
    if args.skip_fireball:
        m.step("2–3. FIREBALL", "пропущено (--skip-fireball)")
    else:
        try:
            archive = args.fireball_archive or args.raw / "fireball" / file_name(fireball_url())
            if not archive.exists():
                if args.offline:
                    raise FileNotFoundError(f"архіву {archive} немає, а мережу вимкнено (--offline)")
                log.info("FIREBALL: завантаження %s (≈ 7,7 ГБ) у %s", fireball_url(), archive)
                download(fireball_url(), archive)
            result = fireball.FireballPreparation(archive, reference, args.out, m, limit=args.fireball_limit).run()
            play_counts = result.play_counts
            m.step("2–3. FIREBALL", f"готово: characters.csv {number(result.characters)}, encounters.csv "
                   f"{number(result.encounters)}, participations.csv {number(result.participations)}"
                   + (f" (лише {args.fireball_limit} боїв)" if args.fireball_limit else ""))
        except (tarfile.TarError, zlib.error, EOFError) as error:
            status = EXIT_PARTIAL
            log.error("FIREBALL: архів пошкоджено або це не tar.gz (%s)", error)
            m.step("2–3. FIREBALL", f"помилка читання архіву {archive.name}: {error}. Видаліть файл і запустіть "
                   "підготовку знову — архів завантажиться повторно")
        except (DownloadError, OSError, ValueError) as error:
            status = EXIT_PARTIAL
            log.error("FIREBALL: %s", error)
            m.step("2–3. FIREBALL", f"помилка: {error}. Повторний запуск продовжить завантаження; іншу адресу "
                   "архіву задає змінна FIREBALL_URL, а вже завантажений архів можна покласти в data/raw/fireball/")

    rows = srd.write_monsters(reference, args.out / "monsters.csv", play_counts)
    m.step("1. SRD", f"готово: monsters.csv {number(rows)}" + ("" if play_counts else ", play_count = 0"))

    if worker is not None:
        while worker.is_alive():
            worker.join(5)
        if catalog_step.error is not None:
            status = EXIT_PARTIAL
            m.step("4. Каталог itch.io", f"помилка: {catalog_step.error}")
        else:
            counts = catalog_step.counts
            m.step("4. Каталог itch.io", ("відтворено зі знімка: " if catalog_step.replay else "готово: ")
                   + ", ".join(f"{name} {number(n)}" for name, n in counts.items()))
            catalog.measure(catalog_step.result, m, args.catalog_sample, getattr(settings, "crawler_tag", "dnd"))

    rejected = check.check(args.out, m, check.tag_aliases(Path(settings.data_dir)))
    m.step("Перевірка моделлю застосунку", f"відхилено б {number(rejected)} рядків")
    if rejected and status == EXIT_OK:
        status = EXIT_PARTIAL
    markdown, _ = m.save(args.out)
    minutes = (time.monotonic() - started) / 60
    log.info("підготовку завершено за %.0f хв; звіт вимірювань: %s", minutes, markdown)
    for name, value in m.steps.items():
        log.info("  %s — %s", name, value)
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.warning("підготовку перервано; повторний запуск продовжить з місця зупинки завантаження")
        sys.exit(130)
