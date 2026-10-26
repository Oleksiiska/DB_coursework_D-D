"""Крок 4: каталог itch.io — офлайн-обхід тим самим CrawlerSource, що працює
в застосунку (підрозділ 2.6, таблиці 2.12, А.2–А.5).

Кожна отримана сторінка зберігається у знімок обходу
(``data/raw/itch/crawl-<дата>-full.jsonl.gz`` або ``-sample<N>`` для вибірки): з нього обхід можна відтворити без
мережі (``--catalog-replay``), а виміри — перевірити повторно. Документи
краулера записуються в adventures.csv, authors.csv і tags.csv без колонок
kind та origin: ці поля додає CsvSource (origin = csv).
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domain.values import DocumentKind
from app.infrastructure.sources.crawler_source import CrawlerSource, PolitenessPolicy, parse_list_page
from app.infrastructure.sources.generator_source import calibrate_author_pool
from tools.prepare.csvio import CsvTable
from tools.prepare.report import Measurements, number, share, top

log = logging.getLogger("prepare.catalog")

ADVENTURE_COLUMNS = (
    "adventure_id", "itch_id", "title", "synopsis", "cover_image_url", "source_url", "status", "genre",
    "publication_language", "published_year", "duration_hours", "level_min", "level_max", "players_min",
    "players_max", "system", "edition", "required_books", "price", "rating_avg", "ratings_count", "author_ids",
    "tags", "similar_urls", "reviews", "derived_fields",
)
AUTHOR_COLUMNS = ("author_id", "name", "profile_url", "bio", "platform")
TAG_COLUMNS = ("name", "description", "category")

#: Мітки таблиці картки поза моделлю (п. 2.6.5)
EXTRA_LABELS = ("content", "links", "asset license", "mentions", "accessibility", "publisher", "multiplayer")
_CURRENCY = re.compile(r"(\$|£|€|USD|GBP|EUR)")


# --------------------------------------------------------------------------
# Знімок обходу
# --------------------------------------------------------------------------


class Recorder:
    """Запис кожної відповіді в gzip JSONL: адреса, код, час і текст сторінки.

    Знімок пишеться під назвою ``….part`` і отримує остаточну лише після
    завершеного обходу: перерваний обхід не відтворюється як повний.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests = 0
        self.first: float | None = None
        self.last: float | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._part = path.with_name(path.name + ".part")
        self._stream = gzip.open(self._part, "wt", encoding="utf-8", errors="replace")

    def record(self, url: str, response: Any) -> None:
        now = time.time()
        self.first = self.first or now
        self.last = now
        self.requests += 1
        status = response.status_code
        self._stream.write(json.dumps({
            "url": url, "status": status, "fetched_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "retry_after": response.headers.get("Retry-After"),
            "text": response.text if status == 200 else None,
        }, ensure_ascii=False) + "\n")

    def close(self, completed: bool = True) -> None:
        self._stream.close()
        if completed:
            self._part.replace(self.path)


class RecordingClient:
    """Обгортка HTTP-клієнта краулера: відповіді йдуть у знімок без змін."""

    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    async def __aenter__(self) -> "RecordingClient":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._inner.__aexit__(*exc)

    async def get(self, url: str, **kwargs: Any) -> Any:
        response = await self._inner.get(url, **kwargs)
        self._recorder.record(url, response)
        return response


@dataclass(slots=True)
class SavedResponse:
    status_code: int
    text: str
    headers: dict = field(default_factory=dict)


class ReplayClient:
    """Відтворення обходу зі знімка: та сама адреса — та сама відповідь, решта — 404."""

    def __init__(self, pages: dict[str, dict]) -> None:
        self._pages = pages

    async def __aenter__(self) -> "ReplayClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> SavedResponse:
        page = self._pages.get(url)
        if page is None:
            return SavedResponse(404, "")
        headers = {"Retry-After": page["retry_after"]} if page.get("retry_after") else {}
        return SavedResponse(page["status"], page.get("text") or "", headers)


def load_snapshot(path: Path) -> dict[str, dict]:
    """Остання відповідь для кожної адреси; успішна відповідь має перевагу над помилкою."""
    pages: dict[str, dict] = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                page = json.loads(line)
                if page["status"] == 200 or pages.get(page["url"], {}).get("status") != 200:
                    pages[page["url"]] = page
    except (EOFError, OSError, ValueError) as error:  # обірваний кінець файлу — беруться прочитані сторінки
        log.warning("знімок %s прочитано не до кінця (%s): сторінок %d", path.name, error, len(pages))
    return pages


# --------------------------------------------------------------------------
# Обхід і запис CSV
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CatalogResult:
    adventures: list[dict] = field(default_factory=list)
    authors: list[dict] = field(default_factory=list)
    tags: list[dict] = field(default_factory=list)
    snapshot: Path | None = None
    requests: int = 0
    seconds: float = 0.0


async def crawl(settings: Any, systems: list[dict], raw_dir: Path, *, sample: int | None = None,
                seed: int = 2026, replay: Path | None = None) -> CatalogResult:
    """Обхід за тегом settings.crawler_tag; з replay — зі знімка без мережі й затримок."""
    result = CatalogResult()
    recorder: Recorder | None = None
    if replay is not None:
        pages = load_snapshot(replay)
        result.snapshot = replay
        policy = PolitenessPolicy(min_delay=0.0, max_delay=0.0)

        def factory() -> Any:
            return ReplayClient(pages)
    else:
        import httpx

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        mode = f"sample{sample}" if sample else "full"
        recorder = Recorder(raw_dir / "itch" / f"crawl-{stamp}-{mode}.jsonl.gz")
        result.snapshot = recorder.path
        policy = PolitenessPolicy()

        def factory() -> Any:
            return RecordingClient(httpx.AsyncClient(
                headers={"User-Agent": policy.user_agent}, follow_redirects=True,
                timeout=CrawlerSource.REQUEST_TIMEOUT_SECONDS), recorder)
    source = CrawlerSource(settings.crawler_base_url, settings.crawler_tag, policy, systems=systems,
                           client_factory=factory, card_limit=sample, sample_seed=seed)
    started = time.monotonic()
    completed = False
    try:
        async for batch in source.stream():
            for document in batch.documents:
                kind = document.get("kind")
                row = {k: v for k, v in document.items() if k not in ("kind", "origin")}
                if kind == DocumentKind.ADVENTURE.value:
                    result.adventures.append(row)
                elif kind == DocumentKind.AUTHOR.value:
                    result.authors.append(row)
                elif kind == DocumentKind.TAG.value:
                    result.tags.append(row)
        completed = True
    finally:
        if recorder is not None:
            recorder.close(completed)
            result.requests = recorder.requests
    result.seconds = time.monotonic() - started
    return result


def write_csv(result: CatalogResult, out_dir: Path) -> dict[str, int]:
    counts = {}
    for name, columns, rows in (("adventures.csv", ADVENTURE_COLUMNS, result.adventures),
                                ("authors.csv", AUTHOR_COLUMNS, result.authors),
                                ("tags.csv", TAG_COLUMNS, result.tags)):
        with CsvTable(out_dir / name, columns) as table:
            for row in rows:
                table.write({k: row.get(k) for k in columns})
        counts[name] = table.rows
        log.info("%s: %d рядків", name, table.rows)
    return counts


# --------------------------------------------------------------------------
# Виміри (підрозділ 2.6, таблиці 2.12, А.2–А.5)
# --------------------------------------------------------------------------


def card_details(html: str) -> dict[str, Any]:
    """Мітки таблиці картки, сира мітка Average session і символ валюти —
    те, чого документ пригоди вже не містить."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    labels: dict[str, str] = {}
    for row in soup.select("div.game_info_panel_widget tr, table.game_info_table tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            labels[cells[0].get_text(" ", strip=True).lower()] = cells[1].get_text(" ", strip=True)
    currency = None
    for selector in ("span[itemprop=price]", ".price_value", ".buy_row .dollars", ".buy_message"):
        element = soup.select_one(selector)
        if element is not None:
            match = _CURRENCY.search(element.get_text(" ", strip=True))
            if match:
                currency = {"USD": "$", "GBP": "£", "€": "EUR"}.get(match.group(1), match.group(1))
                break
    return {"labels": labels, "currency": currency}


@dataclass(slots=True)
class _SnapshotScan:
    """Те, що вимірюється за сторінками знімка, а не за документами."""

    first_fetched: str | None = None
    robots: str | None = None
    list_pages: int = 0
    per_page: int | None = None
    discovered: set[str] = field(default_factory=set)
    cards: list[dict] = field(default_factory=list)


def _scan_snapshot(path: Path | None, tag: str, crawled: set[str]) -> _SnapshotScan:
    """Один потоковий прохід знімка: сторінки не тримаються в пам'яті всі разом."""
    scan = _SnapshotScan()
    if path is None or not path.exists():
        return scan
    list_url = re.compile(rf"/physical-games/tag-{re.escape(tag)}(\?page=(\d+))?$")
    seen: set[str] = set()
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                page = json.loads(line)
                scan.first_fetched = min(filter(None, (scan.first_fetched, page.get("fetched_at"))), default=None)
                url, text = page["url"], page.get("text")
                if page["status"] != 200 or not text or url in seen:
                    continue
                seen.add(url)
                if url.endswith("/robots.txt"):
                    scan.robots = text
                list_match = list_url.search(url)
                if list_match:
                    links = parse_list_page(text, "https://itch.io")[0]
                    scan.list_pages += bool(links)
                    scan.discovered.update(link.url for link in links)
                    if not list_match.group(1):
                        scan.per_page = len(links)
                elif url in crawled and 'name="itch:path"' in text:
                    scan.cards.append(card_details(text))
    except (EOFError, OSError, ValueError) as error:
        log.warning("знімок %s прочитано не до кінця (%s)", path.name, error)
    return scan


def measure(result: CatalogResult, m: Measurements, sample: int | None, tag: str = "dnd") -> None:
    key = "catalog"
    m.section(key, "Крок 4. Каталог itch.io (підрозділ 2.6)")
    adventures = result.adventures
    n = len(adventures)
    # лише картки цього обходу: вибірку можна відтворити й зі знімка повного обходу
    scan = _scan_snapshot(result.snapshot, tag, {a.get("source_url") for a in adventures})
    day = datetime.fromisoformat(scan.first_fetched).strftime("%d.%m.%Y") if scan.first_fetched else "—"
    m.snapshots["itch.io"] = (f"обхід {day}, тег {tag}, "
                              f"{'вибірка ' + number(sample) + ' карток' if sample else 'усі картки'}; "
                              f"знімок {result.snapshot.name if result.snapshot else '—'}")

    if scan.robots:
        lines = [line.strip() for line in scan.robots.splitlines()]
        disallow = [line.split(":", 1)[1].strip() for line in lines
                    if line.lower().startswith("disallow:") and line.split(":", 1)[1].strip()]
        delay = any(line.lower().startswith("crawl-delay") for line in lines)
        m.add(key, "п. 2.6.1", "robots.txt: заборонені маршрути", ", ".join(dict.fromkeys(disallow)),
              "/embed/, /embed-upload/, /search, /checkout/, /game/download/, /bundle/download/, "
              "/register-for-purchase/, /email-feedback/")
        m.add(key, "п. 2.6.1", "директива Crawl-delay", "є" if delay else "відсутня", "відсутня")
    m.add(key, "п. 2.6.2", "сторінок списку з картками / карток на сторінці",
          f"{number(scan.list_pages)} / {number(scan.per_page)}", "54 / 36")
    m.add(key, "табл. 2.1, п. 2.6.2", f"модулів за тегом {tag}", number(len(scan.discovered)), "1 909")
    m.add(key, "табл. 2.3, А.1", "рядків adventures.csv", number(n), "1 909")
    m.add(key, "табл. 2.3, А.1, А.13", "рядків authors.csv", number(len(result.authors)), "~800")
    m.add(key, "табл. 2.3, А.1, А.13", "рядків tags.csv", number(len(result.tags)), "40–60")
    if result.requests:
        m.add(key, "п. 2.6.2", "запитів / тривалість обходу",
              f"{number(result.requests)} / {number(result.seconds / 60, 0)} хв "
              f"({number(result.seconds / max(1, result.requests), 2)} с на запит)", "≈ 2 000 + 850 / ≈ 75 хв + профілі")
    if not n:
        return

    def filled(name: str) -> int:
        return sum(1 for a in adventures if a.get(name) not in (None, "", [], {}))

    showcase = sum(1 for a in adventures if all(a.get(f) not in (None, "", []) for f in
                                                ("title", "cover_image_url", "itch_id", "status", "tags", "author_ids")))
    m.add(key, "табл. 2.12", "вітрина: назва, обкладинка, ID, статус, теги, автор", share(showcase, n), "100%")
    for name, field_name, reported in (
        ("назва", "title", "100%"), ("обкладинка", "cover_image_url", "100%"), ("статус", "status", "100%"),
        ("теги", "tags", "100%"), ("автор", "author_ids", "100%"),
        ("рейтинг", "rating_avg", "99%"), ("кількість оцінок", "ratings_count", "99%"), ("синопсис", "synopsis", "96%"),
        ("жанр", "genre", "67%"), ("ціна (платні)", "price", "63%"), ("мова видання", "publication_language", "12%"),
        ("рік виходу", "published_year", "8%"), ("рівень персонажів", "level_min", "18%"),
        ("кількість гравців", "players_min", "3%"), ("тривалість (після відкидання < 1 год)", "duration_hours", "10%"),
        ("система (за systems.json)", "system", "18%"), ("similar_urls", "similar_urls", "100%"),
    ):
        m.add(key, "табл. 2.12, А.2", name, share(filled(field_name), n), reported)
    m.add(key, "табл. 2.13, п. 2.7.3", "безкоштовні (без ціни)", share(n - filled("price"), n), "37%")

    details = scan.cards
    if details:
        sessions = Counter(d["labels"].get("average session") for d in details if d["labels"].get("average session"))
        m.add(key, "табл. 2.12, п. 2.6.3", "мітка Average session", share(sum(sessions.values()), len(details)), "14%")
        m.add(key, "п. 2.6.3", "значення Average session", top(sessions, 8), "4 з 14 — «A few seconds»")
        currencies = Counter(d["currency"] for d in details if d["currency"])
        m.add(key, "п. 2.6.3", "валюти цін", top(currencies, 5), "$ — 57, £ — 4, EUR — 2")
        dates = sum(1 for d in details if any(k in d["labels"] for k in ("published", "release date", "updated")))
        m.add(key, "табл. 2.12", "дата виходу або оновлення", share(dates, len(details)), "8%")
        extra = ", ".join(f"{label.capitalize()} {share(sum(1 for d in details if label in d['labels']), len(details))}"
                          for label in EXTRA_LABELS)
        m.add(key, "п. 2.6.5", "додаткові мітки картки", extra,
              "Content 33%, Links 7%, Asset license 4%, Mentions 2%, Accessibility 2%, Publisher 1%, Multiplayer 1%")
    several = sum(1 for a in adventures if len(a.get("author_ids") or []) > 1)
    m.add(key, "п. 2.6.3, табл. 2.13", "множинне авторство", share(several, n), "1 з 100 (1%)")
    reviews = [len(a.get("reviews") or []) for a in adventures]
    with_reviews = sum(1 for r in reviews if r)
    m.add(key, "п. 2.6.4, табл. А.2", "картки з коментарями", share(with_reviews, n), "79%")
    m.add(key, "п. 2.6.4", "коментарів на картку / разом",
          f"{number(sum(reviews) / n, 1)} / {number(sum(reviews))}", "9,6 / 958 на вибірці (≈ 18 000 на 1 909)")
    authors = result.authors
    m.add(key, "табл. А.4", "автор: bio", share(sum(1 for a in authors if a.get("bio")), len(authors) or 1),
          "TODO: виміряти")
    tags = result.tags
    m.add(key, "табл. А.5", "тег: description", share(sum(1 for t in tags if t.get("description")), len(tags) or 1),
          "TODO: виміряти")
    m.add(key, "табл. А.5", "тег: category (бічний фільтр)",
          share(sum(1 for t in tags if t.get("category")), len(tags) or 1), "TODO: виміряти")
    categories = Counter(t.get("category") for t in tags if t.get("category"))
    m.add(key, "п. 2.6.5", "категорії тегів", top(categories, 4), "Types, Gameplay, Format, Theme")
    rows = [{"author_ids": json.dumps(a.get("author_ids") or [])} for a in adventures]
    pool, exponent = calibrate_author_pool(rows)
    works = Counter(i for a in adventures for i in a.get("author_ids") or [])
    m.add(key, "п. 2.1.1, табл. 2.13", "авторів / робіт у найпродуктивнішого / показник Ципфа (пул генератора)",
          f"{number(len(works))} / {number(max(works.values()) if works else 0)} / {number(exponent, 2)} "
          f"(пул {number(pool)})")
    if sample:
        m.note(key, f"Виміри на випадковій вибірці {sample} карток: похибка частки — до ±{_margin(sample)} в.п. "
               "(95% довірчий інтервал), як у підрозділі 2.9.")


def _margin(n: int) -> str:
    return number(round(196 * (0.25 / n) ** 0.5, 1), 1)
