"""Джерело: краулер каталогу itch.io (Шлях Б, бонусна частина; п. 2.6, 4.8).

Обмеження зчитуються з robots.txt: заборонені маршрути не запитуються,
посилання на файли модулів не збираються.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import urllib.robotparser
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable
from urllib.parse import urljoin, urlparse

from app.domain.values import DocumentKind, SourceBatch, SourceStats

log = logging.getLogger("crawler")


class PolitenessPolicy:
    """Правила ввічливого обходу (п. 2.6.1, 4.8).

    Директива Crawl-delay у джерелі відсутня, тому затримка обирається
    самостійно: 1,5–3 с із випадковим розкидом. Відповіді 429 і 503 —
    обмеження частоти: очікування за заголовком Retry-After, а без нього
    подвоєння затримки до межі 5 хв; звичайний темп повертається після
    серії успішних запитів.
    """

    #: Знімок robots.txt itch.io (п. 2.6.1) — діє, якщо файл недоступний
    DISALLOWED_PREFIXES: tuple[str, ...] = (
        "/embed/", "/embed-upload/", "/search", "/checkout/",
        "/game/download/", "/bundle/download/",
        "/register-for-purchase/", "/email-feedback/",
    )

    #: Сталий заголовок із назвою проєкту й контактною адресою (п. 2.6.1):
    #: краулер не маскується під браузер, власник сайту бачить, хто ходить
    USER_AGENT = (
        "DnD-Adventures-Coursework/0.1 "
        "(+https://github.com/Oleksiiska/DB_coursework_D-D)"
    )

    MAX_BACKOFF_SECONDS = 300.0
    #: Скільки успішних запитів поспіль повертають звичайний темп
    RECOVERY_SUCCESSES = 10

    def __init__(self, min_delay: float = 1.5, max_delay: float = 3.0) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._backoff = 0.0
        self._successes = 0
        self._robots: urllib.robotparser.RobotFileParser | None = None

    def load_robots(self, text: str | None) -> None:
        """Правила robots.txt розбирає стандартна бібліотека (urllib.robotparser)."""
        if text is None:
            self._robots = None
            return
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        self._robots = parser

    def is_allowed(self, path: str) -> bool:
        """Чи дозволяє robots.txt запит адреси (повної або шляху)."""
        if self._robots is not None:
            return self._robots.can_fetch(self.USER_AGENT, path)
        route = urlparse(path).path or path
        return not any(route.startswith(prefix) for prefix in self.DISALLOWED_PREFIXES)

    async def wait(self) -> None:
        """Випадкова затримка в заданому діапазоні — або подвоєна після обмеження частоти."""
        await asyncio.sleep(max(self._backoff, random.uniform(self._min_delay, self._max_delay)))

    def throttled(self, retry_after: float | None) -> float:
        """Тривалість очікування після 429/503 або сторінки-заглушки."""
        self._successes = 0
        if retry_after is not None:
            return min(self.MAX_BACKOFF_SECONDS, retry_after)
        self._backoff = min(self.MAX_BACKOFF_SECONDS, max(self._backoff * 2, self._max_delay * 2))
        return self._backoff

    def succeeded(self) -> None:
        self._successes += 1
        if self._successes >= self.RECOVERY_SUCCESSES:
            self._backoff = 0.0

    @property
    def user_agent(self) -> str:
        return self.USER_AGENT


class CardBlocked(Exception):
    """Сторінка картки без мета-тега itch:path — заглушка або перевірка на робота."""


@dataclass(slots=True)
class _Counters:
    """Звіт джерела, що накопичується до наступного пакета."""

    stage: str | None = None
    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    throttled: int = 0
    robots_skipped: int = 0
    discovered: int = 0
    completed: int = 0

    def flush(self) -> SourceStats:
        stats = SourceStats(self.requested, self.succeeded, self.failed, self.throttled, self.robots_skipped,
                            self.stage, self.discovered, self.completed)
        self.requested = self.succeeded = self.failed = self.throttled = 0
        self.robots_skipped = self.discovered = self.completed = 0
        return stats


@dataclass(slots=True)
class CardLink:
    url: str
    game_id: str | None = None


@dataclass(slots=True)
class ParsedCard:
    document: dict
    authors: list[tuple[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class CrawlerSource:
    """Реалізує порт `DocumentSource`.

    Обхід послідовний, одним з'єднанням: спершу сторінки списку за тегом,
    потім картки модулів, потім профілі авторів і сторінки тегів (п. 4.8).
    Швидкість обмежена ввічливою затримкою 1,5–3 с між запитами — у
    середньому близько 0,4 сторінки за секунду; повний обхід за тегом
    (~2 000 запитів) триває приблизно 75 хвилин (п. 2.6.2). Тому обсяг
    для навантаження дає не він, а генератор.
    """

    LIST_PAGE_SIZE = 36
    MAX_LIST_PAGES = 200
    #: Повтори для тайм-аутів і відповідей 5xx, крім 503
    RETRY_DELAYS = (2, 4, 8)
    #: Скільки разів поспіль чекати на обмеження частоти для однієї адреси
    MAX_THROTTLE_WAITS = 6
    REQUEST_TIMEOUT_SECONDS = 20.0
    STAGES = ("lists", "cards", "authors", "tags")

    def __init__(
        self,
        base_url: str,
        tag: str,
        policy: PolitenessPolicy,
        systems: list[dict] | None = None,
        client_factory: Callable[[], Any] | None = None,
        card_limit: int | None = None,
        sample_seed: int | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._tag = tag
        self._policy = policy
        self._systems = systems or []
        self._client_factory = client_factory or self._default_client
        #: Випадкова вибірка карток замість повного обходу (офлайн-вимірювання, п. 2.9):
        #: списки обходяться повністю, а картки — лише card_limit із відтворюваним зерном
        self._card_limit = card_limit
        self._sample_seed = sample_seed
        self._rate = 1

    @property
    def name(self) -> str:
        return "crawler"

    async def stream(self, batch_size: int = 20) -> AsyncIterator[SourceBatch]:
        """Повний обхід: списки → картки → профілі авторів → сторінки тегів.

        Документи публікуються пакетами по batch_size, а звіт джерела — після
        кожної сторінки: інакше лічильники зростали б раз на 20 сторінок
        (близько 50 с), і посекундна швидкість краулера на панелі між
        пакетами показувала б нуль (п. 4.9).
        """
        counters = _Counters()
        async with self._client_factory() as client:
            await self._load_robots(client, counters)

            counters.stage = "lists"
            links: dict[str, CardLink] = {}
            categories: dict[str, str] = {}
            for page in range(1, self.MAX_LIST_PAGES + 1):
                html = await self._fetch(client, self._list_url(page), counters)
                counters.discovered += 1
                counters.completed += 1
                if html is None:
                    # звіт про останню сторінку списку — до етапу карток, а не в ньому
                    yield SourceBatch([], counters.flush())
                    break
                page_links, page_categories = parse_list_page(html, self._base_url)
                categories.update(page_categories)
                fresh = [link for link in page_links if link.url not in links]
                links.update((link.url, link) for link in fresh)
                yield SourceBatch([], counters.flush())
                if not fresh:
                    break

            links = self._sample(links)
            counters.stage = "cards"
            counters.discovered = len(links)
            authors: dict[str, str] = {}
            tags: dict[str, None] = {}
            documents: list[dict] = []
            for link in links.values():
                card = await self._card(client, link, counters)
                counters.completed += 1
                if card is not None:
                    documents.append(card.document)
                    authors.update((url, name) for name, url in card.authors)
                    tags.update((tag, None) for tag in card.tags)
                if len(documents) >= batch_size:
                    yield SourceBatch(documents, counters.flush())
                    documents = []
                else:
                    yield SourceBatch([], counters.flush())
            if documents or counters.requested:
                yield SourceBatch(documents, counters.flush())

            counters.stage = "authors"
            counters.discovered = len(authors)
            documents = []
            for url, name in authors.items():
                html = await self._fetch(client, url, counters)
                counters.completed += 1
                documents.append({
                    "kind": DocumentKind.AUTHOR.value, "origin": "crawler",
                    "author_id": DocumentKind.AUTHOR.natural_id(url), "name": name, "profile_url": url,
                    "bio": parse_profile(html) if html else None, "platform": "itch.io",
                })
                if len(documents) >= batch_size:
                    yield SourceBatch(documents, counters.flush())
                    documents = []
                else:
                    yield SourceBatch([], counters.flush())
            if documents:
                yield SourceBatch(documents, counters.flush())

            counters.stage = "tags"
            counters.discovered = len(tags)
            documents = []
            for tag in tags:
                html = await self._fetch(client, f"{self._base_url}/physical-games/tag-{tag}", counters)
                counters.completed += 1
                documents.append({
                    "kind": DocumentKind.TAG.value, "origin": "crawler", "name": tag,
                    "description": parse_tag_page(html) if html else None, "category": categories.get(tag),
                })
                if len(documents) >= batch_size:
                    yield SourceBatch(documents, counters.flush())
                    documents = []
                else:
                    yield SourceBatch([], counters.flush())
            if documents or counters.requested:
                yield SourceBatch(documents, counters.flush())
        log.info("обхід завершено: карток %d, авторів %d, тегів %d", len(links), len(authors), len(tags))

    def _sample(self, links: dict[str, CardLink]) -> dict[str, CardLink]:
        """Вибірка карток у порядку списку; без card_limit — усі картки."""
        if self._card_limit is None or len(links) <= self._card_limit:
            return links
        chosen = set(random.Random(self._sample_seed).sample(sorted(links), self._card_limit))
        log.info("вибірка: %d карток із %d (зерно %s)", len(chosen), len(links), self._sample_seed)
        return {url: link for url, link in links.items() if url in chosen}

    async def set_rate(self, documents_per_second: int) -> None:
        """Для краулера — верхня межа, що не перевищує ввічливої затримки:
        темп задає PolitenessPolicy, тож значення лише запам'ятовується."""
        self._rate = max(1, int(documents_per_second))

    # --- обхід ---

    def _list_url(self, page: int) -> str:
        return f"{self._base_url}/physical-games/tag-{self._tag}" + (f"?page={page}" if page > 1 else "")

    def _default_client(self) -> Any:
        import httpx

        return httpx.AsyncClient(
            headers={"User-Agent": self._policy.user_agent},
            follow_redirects=True,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
        )

    async def _load_robots(self, client: Any, counters: _Counters) -> None:
        url = f"{self._base_url}/robots.txt"
        counters.requested += 1
        try:
            response = await client.get(url)
            text = response.text if response.status_code == 200 else None
        except Exception as error:  # noqa: BLE001 — без robots.txt діє збережений знімок правил
            log.warning("robots.txt недоступний (%s): діють збережені правила", error)
            text = None
        if text is None:
            counters.failed += 1
        else:
            counters.succeeded += 1
        self._policy.load_robots(text)

    async def _collect_links(self, pages: int) -> list[str]:
        """Посилання на картки з перших `pages` сторінок списку (для перевірок обходу)."""
        counters = _Counters(stage="lists")
        links: list[str] = []
        async with self._client_factory() as client:
            for page in range(1, pages + 1):
                html = await self._fetch(client, self._list_url(page), counters)
                if html is None:
                    break
                links.extend(link.url for link in parse_list_page(html, self._base_url)[0])
        return list(dict.fromkeys(links))

    async def _card(self, client: Any, link: CardLink, counters: _Counters) -> ParsedCard | None:
        """Картка з повторами: сторінка без itch:path обробляється як обмеження частоти."""
        for _ in range(self.MAX_THROTTLE_WAITS):
            html = await self._fetch(client, link.url, counters)
            if html is None:
                return None
            try:
                return await self._parse_card(link.url, html)
            except CardBlocked:
                counters.throttled += 1
                counters.succeeded -= 1
                delay = self._policy.throttled(None)
                log.warning("сторінка %s без itch:path — заглушка або перевірка на робота, очікування %.0f с",
                            link.url, delay)
                await asyncio.sleep(delay)
        counters.failed += 1
        return None

    async def _parse_card(self, url: str, html: str) -> ParsedCard:
        """Розбір картки: таблиця властивостей, ціна, коментарі."""
        return parse_card(html, url, datetime.now(timezone.utc), self._systems)

    async def _fetch(self, client: Any, url: str, counters: _Counters) -> str | None:
        """Запит із ввічливою затримкою, повторами й обробкою обмежень частоти."""
        if not self._policy.is_allowed(url):
            counters.robots_skipped += 1
            log.info("пропущено за robots.txt: %s", url)
            return None
        retries = throttles = 0
        while True:
            await self._policy.wait()
            counters.requested += 1
            try:
                response = await client.get(url)
            except Exception as error:  # noqa: BLE001 — тайм-аут або збій з'єднання
                if retries < len(self.RETRY_DELAYS):
                    await asyncio.sleep(self.RETRY_DELAYS[retries])
                    retries += 1
                    continue
                counters.failed += 1
                log.warning("%s: %s після %d повторів", url, type(error).__name__, retries)
                return None
            status = response.status_code
            if status in (429, 503):
                counters.throttled += 1
                throttles += 1
                retry_after = _retry_after(response.headers.get("Retry-After"))
                delay = self._policy.throttled(retry_after)
                log.warning("%d від itch.io, очікування %.0f с (%s)", status, delay,
                            "Retry-After" if retry_after is not None else "подвоєння затримки")
                if throttles > self.MAX_THROTTLE_WAITS:
                    counters.failed += 1
                    return None
                await asyncio.sleep(delay)
                continue
            if 500 <= status < 600 and retries < len(self.RETRY_DELAYS):
                await asyncio.sleep(self.RETRY_DELAYS[retries])
                retries += 1
                continue
            if status >= 400:
                counters.failed += 1
                log.warning("%s: відповідь %d", url, status)
                return None
            self._policy.succeeded()
            counters.succeeded += 1
            log.info("отримано %s", url)
            return response.text


# --------------------------------------------------------------------------
# Розбір сторінок itch.io
# --------------------------------------------------------------------------

#: Мітка Average session → години; значення, коротші за годину, відкидаються (таблиця А.2)
SESSION_HOURS: dict[str, float | None] = {
    "a few seconds": None, "a few minutes": None, "about a half-hour": None,
    "about an hour": 1.0, "a few hours": 3.0, "days or more": 24.0,
}
#: Нормалізація валют до долара за фіксованими курсами (п. 2.6.3). Валюти — ті, що трапилися
#: в offers.priceCurrency повного обходу: USD, GBP, EUR і CAD
CURRENCY_TO_USD: dict[str, float] = {"$": 1.0, "USD": 1.0, "£": 1.27, "GBP": 1.27, "€": 1.08, "EUR": 1.08,
                                     "CA$": 0.73, "CAD": 0.73}

#: Рядок Rating таблиці картки. Кількість оцінок стоїть в окремому вкладеному span, тож
#: після вилучення тексту між дужками й числом можуть з'явитися пробіли: «( 25 total ratings )»
_RATING = re.compile(r"Rated\s+([\d.]+)\s+out of 5 stars(?:.*?\(\s*(\d[\d,]*)\s+total ratings?\s*\))?", re.I | re.S)
_LEVEL_RANGE = re.compile(r"\blevels?\s+(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\b", re.I)
_LEVEL_SINGLE = re.compile(r"\bfor\s+(?:a\s+party\s+of\s+)?(?:level|lvl)\s+(\d{1,2})\s+(?:characters|pcs|adventurers)\b"
                           r"|\b(\d{1,2})(?:st|nd|rd|th)[- ]level\b", re.I)
_PLAYERS = re.compile(r"\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s+(?:players|pcs)\b", re.I)
_PRICE = re.compile(r"(CA\$|\$|£|€|USD|GBP|EUR|CAD)\s*(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*(USD|GBP|EUR|CAD|€)")
_RELATIVE = re.compile(r"(\d+|an?|one)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
_UNIT_DAYS = {"second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def _soup(html: str) -> Any:
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "lxml")


def parse_list_page(html: str, base_url: str) -> tuple[list[CardLink], dict[str, str]]:
    """Картки сторінки списку (36 на сторінку) і категорії тегів бічного
    фільтра: itch.io сам групує теги на Types, Gameplay, Format і Theme."""
    soup = _soup(html)
    links: list[CardLink] = []
    for cell in soup.select("div.game_cell"):
        anchor = cell.select_one("a.title.game_link") or cell.select_one("a.game_link") or cell.find("a", href=True)
        if anchor and anchor.get("href"):
            links.append(CardLink(urljoin(base_url + "/", anchor["href"]), cell.get("data-game_id")))
    return links, _tag_categories(soup)


#: Заголовки груп тегів бічного фільтра сторінки списку (п. 2.6.5)
TAG_CATEGORY_HEADINGS = ("Types", "Gameplay", "Format", "Theme")
_CATEGORY_HEADING = re.compile(r"^\s*(" + "|".join(TAG_CATEGORY_HEADINGS) + r")\s*$")


def _tag_categories(soup: Any) -> dict[str, str]:
    """Тег → група бічного фільтра. Розмітка груп не фіксується: тег належить
    найближчому заголовку перед ним, якщо обидва лежать в одному блоці, що не
    містить карток списку. Так заголовок може бути й обгорткою групи, й
    окремим рядком перед переліком посилань."""
    categories: dict[str, str] = {}
    for anchor in soup.find_all("a", href=re.compile(r"/tag-")):
        if anchor.find_parent("div", class_="game_cell") is not None:
            continue
        heading = anchor.find_previous(string=_CATEGORY_HEADING)
        if heading is None:
            continue
        ancestors = {id(parent) for parent in anchor.parents}
        common = next((parent for parent in heading.parents if id(parent) in ancestors), None)
        if common is None or common.select_one("div.game_cell") is not None:
            continue
        slug = anchor["href"].rsplit("/tag-", 1)[-1].split("?")[0].split("/")[0]
        if slug:
            categories.setdefault(slug, heading.strip())
    return categories


def parse_card(html: str, url: str, crawled_at: datetime, systems: list[dict] | None = None) -> ParsedCard:
    """Розбір картки модуля в документ пригоди (таблиця А.2)."""
    soup = _soup(html)
    path_tag = soup.find("meta", attrs={"name": "itch:path"})
    if path_tag is None or not path_tag.get("content"):
        raise CardBlocked(url)

    def meta(prop: str) -> str | None:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return tag.get("content") if tag and tag.get("content") else None

    rows: dict[str, Any] = {}
    for row in soup.select("div.game_info_panel_widget tr, table.game_info_table tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            rows[cells[0].get_text(" ", strip=True).lower()] = cells[1]

    def text(label: str) -> str | None:
        cell = rows.get(label)
        return cell.get_text(" ", strip=True) if cell is not None else None

    title_tag = soup.select_one("h1.game_title") or soup.find("h1")
    title = title_tag.get_text(" ", strip=True) if title_tag else meta("og:title")
    synopsis = meta("og:description")
    derived: list[str] = []

    authors: list[tuple[str, str]] = []
    for label in ("author", "authors"):
        cell = rows.get(label)
        if cell is not None:
            authors.extend((a.get_text(" ", strip=True), _profile_url(a["href"])) for a in cell.find_all("a", href=True))
    if not authors:
        anchor = soup.select_one(".game_author a, .user_name a")
        if anchor and anchor.get("href"):
            authors.append((anchor.get_text(" ", strip=True), _profile_url(anchor["href"])))

    tags: list[str] = []
    cell = rows.get("tags")
    if cell is not None:
        for anchor in cell.find_all("a"):
            href = anchor.get("href") or ""
            tags.append(href.rsplit("/tag-", 1)[-1].split("?")[0] if "/tag-" in href else anchor.get_text(strip=True))

    product = json_ld_product(soup)
    rating_avg, ratings_count = _rating(product, text("rating") or soup.get_text(" ", strip=True))

    published_year = None
    for label in ("published", "release date", "released"):
        cell = rows.get(label)
        if cell is not None:
            abbr = cell.find("abbr")
            found = re.search(r"(19|20)\d{2}", (abbr.get("title") if abbr else "") or cell.get_text(" ", strip=True))
            if found:
                published_year = int(found.group(0))
                break

    session = (text("average session") or "").strip().lower()
    duration_hours = SESSION_HOURS.get(session)

    description = _description_text(soup)
    corpus = " ".join(filter(None, [title, synopsis, description]))
    levels = _levels(corpus)
    players = _players(corpus)
    if levels:
        derived += ["level_min", "level_max"]
    if players:
        derived += ["players_min", "players_max"]
    # дефіси й підкреслення тегів — як пробіли: тег «5th-edition» збігається з псевдонімом «5th edition»
    tag_words = " ".join(re.sub(r"[-_]+", " ", tag) for tag in tags)
    system = _system(" ".join(filter(None, [title, synopsis, tag_words])), systems or [], description)
    if system:
        derived += ["system", "edition", "required_books"]

    languages = rows.get("languages")
    language = None
    if languages is not None:
        first = languages.find("a")
        language = (first.get_text(" ", strip=True) if first else languages.get_text(" ", strip=True).split(",")[0]) or None

    document = {
        "kind": DocumentKind.ADVENTURE.value,
        "origin": "crawler",
        "itch_id": path_tag["content"],
        "adventure_id": DocumentKind.ADVENTURE.natural_id(path_tag["content"]),
        "title": title,
        "synopsis": synopsis,
        "cover_image_url": meta("og:image"),
        "source_url": url,
        "status": text("status"),
        "genre": text("genre"),
        "publication_language": language,
        "published_year": published_year,
        "duration_hours": duration_hours,
        "level_min": levels[0] if levels else None,
        "level_max": levels[1] if levels else None,
        "players_min": players[0] if players else None,
        "players_max": players[1] if players else None,
        "system": system.get("system") if system else None,
        "edition": system.get("edition") if system else None,
        "required_books": list(system.get("core_books") or []) if system else [],
        "price": _price(soup, product),
        "rating_avg": rating_avg,
        "ratings_count": ratings_count,
        "author_ids": [DocumentKind.AUTHOR.natural_id(profile) for _, profile in authors],
        "tags": tags,
        "similar_urls": sorted({urljoin(url, a["href"]) for a in soup.find_all("a", href=re.compile(r"/games-like/"))}),
        "reviews": _reviews(soup, crawled_at),
        "derived_fields": derived,
    }
    return ParsedCard(document, authors, tags)


def parse_profile(html: str) -> str | None:
    """Біографія автора — власний текст профілю itch.io (блок user_profile).

    Решта вмісту профілю (profile_content) — це посилання автора на сайти й
    соцмережі та перелік його робіт із цінами, тож до біографії не входить.
    Профіль без блоку user_profile біографії не має."""
    block = _soup(html).select_one("div.user_profile")
    text = block.get_text(" ", strip=True) if block is not None else ""
    return text or None


#: Шаблонні тексти itch.io на сторінці тегу. Рядок пошуку: «Explore physical games tagged X
#: on itch.io.» … «· Upload your physical games to itch.io to have them show up here.»;
#: meta description: «Find physical games tagged X like <перші роботи> on itch.io, the indie
#: game hosting marketplace». Вони є на кожній сторінці тегу, тож описом не вважаються
_TAG_BOILERPLATE = (
    re.compile(r"^\s*Find\s+.+?\s+tagged\s+.+?\s+on\s+itch\.io,\s+the\s+indie\s+game\s+hosting\s+marketplace\s*\.?\s*$",
               re.I | re.S),
    re.compile(r"^\s*Explore\s+.+?\s+tagged\s+.+?\s+on\s+itch\.io\s*\.?", re.I | re.S),
    re.compile(r"[\s·]*Upload\s+your\s+.+?\s+to\s+itch\.io\s+to\s+have\s+them\s+show\s+up\s+here\s*\.?\s*$", re.I | re.S),
)


def parse_tag_page(html: str) -> str | None:
    """Опис тегу — власний текст itch.io у блоці tag_description бічного
    фільтра. Такий опис є лише в частини тегів; рядок пошуку на сторінці
    повторює його обрізаним, а meta description — шаблон із переліком робіт,
    тож тег без блоку tag_description опису не має."""
    block = _soup(html).select_one("div.tag_description")
    return strip_tag_boilerplate(block.get_text(" ", strip=True)) if block is not None else None


def strip_tag_boilerplate(text: str | None) -> str | None:
    """Опис тегу без шаблонних речень itch.io або None."""
    text = text or ""
    for pattern in _TAG_BOILERPLATE:
        text = pattern.sub("", text, count=1)
    text = text.strip(" ·\t\n")
    return text or None


def _profile_url(href: str) -> str:
    parsed = urlparse(href)
    return f"{parsed.scheme or 'https'}://{parsed.netloc}" if parsed.netloc else href


def _description_text(soup: Any) -> str:
    block = soup.select_one("div.formatted_description, div.game_description")
    return block.get_text(" ", strip=True) if block is not None else ""


def _levels(text: str) -> tuple[int, int] | None:
    """Рівні персонажів із тексту опису: «levels 3–5», «for level 1 characters»."""
    match = _LEVEL_RANGE.search(text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if 1 <= low <= high <= 20:
            return low, high
    match = _LEVEL_SINGLE.search(text)
    if match:
        level = int(match.group(1) or match.group(2))
        if 1 <= level <= 20:
            return level, level
    return None


def _players(text: str) -> tuple[int, int] | None:
    """Кількість гравців із тексту: «3–5 players»."""
    match = _PLAYERS.search(text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if 1 <= low <= high <= 12:
            return low, high
    return None


def _system(primary: str, systems: list[dict], secondary: str = "") -> dict | None:
    """Ігрова система за назвами й псевдонімами зі списку вручну (systems.json).

    Спершу шукається в назві, синопсисі й тегах (primary), і лише якщо там
    системи немає — в описі картки (secondary): опис часто перелічує сумісні
    системи й конверсії. Порядок списку — пріоритет: окремі системи (Shadowdark,
    DCC, OSE, Pathfinder) стоять перед D&D 5e, яку модулі згадують для
    сумісності, а «System-agnostic» — останньою. Назва системи, спільна для
    кількох редакцій («Dungeons & Dragons»), редакції не визначає, тож для
    таких записів рахуються лише псевдоніми."""
    shared = {name for name, count in Counter(s.get("system") for s in systems).items() if count > 1}
    for text in (primary, secondary):
        lowered = f" {(text or '').lower()} "
        for system in systems:
            own = system.get("system", "")
            names = [own if own not in shared else "", *(system.get("aliases") or [])]
            for name in names:
                if name and re.search(r"(?<![\w])" + re.escape(name.lower()) + r"(?![\w])", lowered):
                    return system
    return None


def json_ld_product(soup: Any) -> dict | None:
    """Блок schema.org Product у JSON-LD картки: itch.io кладе туди назву,
    середню оцінку з кількістю оцінок і, для платних модулів, ціну з валютою."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    return None


def _rating(product: dict | None, row_text: str) -> tuple[float | None, int | None]:
    """Середня оцінка й кількість оцінок: спершу aggregateRating із JSON-LD,
    інакше текст рядка Rating («Rated 4.8 out of 5 stars (25 total ratings)»)."""
    aggregate = (product or {}).get("aggregateRating")
    if isinstance(aggregate, dict):
        try:
            value = float(aggregate.get("ratingValue"))
        except (TypeError, ValueError):
            value = None
        if value is not None:
            try:
                count = int(str(aggregate.get("ratingCount")).replace(",", ""))
            except ValueError:
                count = None
            return value, count
    match = _RATING.search(row_text or "")
    if match is None:
        return None, None
    return float(match.group(1)), int(match.group(2).replace(",", "")) if match.group(2) else None


def _price(soup: Any, product: dict | None = None) -> float | None:
    """Ціна в доларах; без ціни (безкоштовна, «Name your own price») — None.
    Спершу offers із JSON-LD (є лише в платних модулів), потім розмітка кнопки купівлі."""
    offers = (product or {}).get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if isinstance(offers, dict) and offers.get("price") not in (None, ""):
        try:
            amount = float(str(offers["price"]).replace(",", "."))
        except ValueError:
            amount = None
        if amount is not None:
            rate = CURRENCY_TO_USD.get(str(offers.get("priceCurrency") or "USD").upper())
            if rate is not None:
                return round(amount * rate, 2) if amount > 0 else None
    for selector in ("span[itemprop=price]", ".price_value", ".buy_row .dollars", ".buy_message"):
        element = soup.select_one(selector)
        if element is None:
            continue
        content = element.get("content")
        source = f"$ {content}" if content and re.fullmatch(r"\d+(\.\d+)?", content) else element.get_text(" ", strip=True)
        match = _PRICE.search(source)
        if match:
            symbol = match.group(1) or match.group(4)
            amount = float((match.group(2) or match.group(3)).replace(",", "."))
            return round(amount * CURRENCY_TO_USD.get(symbol, 1.0), 2) if amount > 0 else None
    return None


def _reviews(soup: Any, crawled_at: datetime) -> list[dict]:
    """Коментарі (таблиця А.3): дата обчислюється з відносної відносно часу обходу."""
    reviews = []
    for post in soup.select("div.community_post"):
        body = post.select_one(".post_body")
        author = post.select_one(".post_author a")
        if body is None or author is None:
            continue
        identifier = (post.get("id") or "").replace("post-", "")
        permalink = post.find("a", href=re.compile(r"/post/\d+"))
        if permalink is not None:
            identifier = re.search(r"/post/(\d+)", permalink["href"]).group(1)
        date_tag = post.select_one(".post_date")
        reviews.append({
            "review_id": identifier or None,
            "author_name": author.get_text(" ", strip=True),
            "author_profile_url": author.get("href"),
            "text": body.get_text(" ", strip=True),
            "posted_at": _posted_at(date_tag, crawled_at),
        })
    return [review for review in reviews if review["review_id"]]


def _posted_at(tag: Any, crawled_at: datetime) -> str | None:
    if tag is None:
        return None
    match = _RELATIVE.search(tag.get_text(" ", strip=True))
    if match:
        amount = 1 if match.group(1).lower() in ("a", "an", "one") else int(match.group(1))
        moment = crawled_at - timedelta(days=amount * _UNIT_DAYS[match.group(2).lower()])
        return moment.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    title = tag.get("title")
    if title:
        try:
            parsed = datetime.fromisoformat(title.replace(" ", "T"))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).isoformat()
        except ValueError:
            return None
    return None


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None
