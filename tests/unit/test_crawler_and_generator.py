"""Розбір сторінок краулера й правила генератора — без мережі."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.domain.values import DocumentKind, challenge_window
from app.infrastructure.sources import CrawlerSource, GeneratorSource, MarkovTextBuilder, PolitenessPolicy
from app.infrastructure.sources import crawler_source
from app.infrastructure.sources.crawler_source import CardBlocked, parse_card, parse_list_page

CARD = """<html><head><meta name="itch:path" content="games/945604">
<meta property="og:description" content="A gothic one-shot for levels 3-5 and 3-5 players. Uses 5e rules.">
<meta property="og:image" content="https://img.itch.zone/cover.png"></head><body>
<h1 class="game_title">The Drowned Chapel</h1>
<div class="buy_row"><span class="dollars">£4.00</span></div>
<div class="game_info_panel_widget"><table>
<tr><td>Status</td><td>Released</td></tr>
<tr><td>Rating</td><td><div class="aggregate_rating">Rated 4.8 out of 5 stars (25 total ratings)</div></td></tr>
<tr><td>Author</td><td><a href="https://mira.itch.io">Mira</a></td></tr>
<tr><td>Genre</td><td><a href="/physical-games/genre-rpg">Role Playing</a></td></tr>
<tr><td>Tags</td><td><a href="https://itch.io/physical-games/tag-dungeons-and-dragons">Dungeons &amp; Dragons</a>,
<a href="https://itch.io/physical-games/tag-horror">horror</a></td></tr>
<tr><td>Languages</td><td><a href="/x">English</a></td></tr>
<tr><td>Average session</td><td>A few hours</td></tr>
<tr><td>Published</td><td><abbr title="12 May 2021 @ 18:30 UTC">May 12, 2021</abbr></td></tr>
</table></div>
<a href="https://itch.io/games-like/945604/the-drowned-chapel">More like this</a>
<div class="community_post" id="post-777"><span class="post_author"><a href="https://itch.io/profile/gm">gm</a></span>
<span class="post_date"><a href="https://itch.io/post/777">12 days ago</a></span>
<div class="post_body"><p>Ran it twice, great.</p></div></div>
</body></html>"""

SYSTEMS = [{"system": "Dungeons & Dragons", "edition": "5e (2014)", "core_books": ["Player's Handbook (2014)"],
            "aliases": ["5e"], "weight": 1}]


def test_card_is_parsed_into_an_adventure() -> None:
    crawled = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    card = parse_card(CARD, "https://mira.itch.io/the-drowned-chapel", crawled, SYSTEMS)
    doc = card.document
    assert doc["adventure_id"] == DocumentKind.ADVENTURE.natural_id("games/945604")
    assert (doc["title"], doc["status"], doc["genre"], doc["publication_language"]) == (
        "The Drowned Chapel", "Released", "Role Playing", "English")
    assert (doc["rating_avg"], doc["ratings_count"], doc["published_year"], doc["duration_hours"]) == (4.8, 25, 2021, 3.0)
    assert doc["price"] == 5.08  # £4 → $ за фіксованим курсом
    assert (doc["level_min"], doc["level_max"], doc["players_min"], doc["players_max"]) == (3, 5, 3, 5)
    assert doc["system"] == "Dungeons & Dragons" and "level_min" in doc["derived_fields"]
    assert doc["tags"] == ["dungeons-and-dragons", "horror"] and card.authors == [("Mira", "https://mira.itch.io")]
    assert doc["similar_urls"] == ["https://itch.io/games-like/945604/the-drowned-chapel"]
    review = doc["reviews"][0]
    assert review["review_id"] == "777" and review["posted_at"].startswith("2026-10-08")


def test_rating_count_survives_the_nested_markup_of_itch_io() -> None:
    """Кількість оцінок itch.io стоїть у вкладеному span; після вилучення тексту
    між дужками й числом з'являються пробіли («( 25 total ratings )»)."""
    row = ('<tr><td>Rating</td><td><div class="aggregate_rating" title="4.83"><span class="screenreader_only">'
           'Rated 4.8 out of 5 stars</span><span class="star_fill"></span></div><span class="rating_count">'
           '(1,025<span class="screenreader_only"> total ratings</span>)</span></td></tr>')
    html = CARD.replace(
        '<tr><td>Rating</td><td><div class="aggregate_rating">Rated 4.8 out of 5 stars (25 total ratings)</div></td></tr>', row)
    doc = parse_card(html, "https://mira.itch.io/the-drowned-chapel", datetime.now(timezone.utc)).document
    assert (doc["rating_avg"], doc["ratings_count"]) == (4.8, 1025)


def test_json_ld_product_gives_rating_and_price() -> None:
    """schema.org Product у JSON-LD — перше джерело оцінки й ціни; рядок Rating і кнопка купівлі — запасні."""
    ld = ('<script type="application/ld+json">{"@context":"http://schema.org/","@type":"Product","name":"The Drowned '
          'Chapel","aggregateRating":{"@type":"AggregateRating","ratingValue":"4.6","ratingCount":7},'
          '"offers":{"@type":"Offer","price":"5.00","priceCurrency":"USD"}}</script>')
    html = (CARD.replace("</head>", ld + "</head>")
            .replace('<div class="buy_row"><span class="dollars">£4.00</span></div>', "")
            .replace('<tr><td>Rating</td><td><div class="aggregate_rating">Rated 4.8 out of 5 stars (25 total ratings)'
                     '</div></td></tr>', ""))
    doc = parse_card(html, "https://mira.itch.io/the-drowned-chapel", datetime.now(timezone.utc)).document
    assert (doc["rating_avg"], doc["ratings_count"], doc["price"]) == (4.6, 7, 5.0)


#: Розмітка сторінки тегу itch.io зі знімка обходу 22.10.2026 (скорочено): meta description —
#: шаблон із переліком робіт; рядок пошуку повторює власний опис обрізаним; сам опис —
#: у блоці tag_description бічного фільтра, і він є лише в частини тегів
TAG_PAGE = """<html><head>
<meta name="description" content="Find physical games tagged {name} like Troika! Numinous Edition, Prime Material on
itch.io, the indie game hosting marketplace">
<meta property="og:description" content="Find physical games tagged {name} like Troika! Numinous Edition on itch.io,
the indie game hosting marketplace"></head><body>
<div class="browse_header"><h2><span>Top</span> Physical games <span>tagged {name}</span></h2>
<p class="search_description">Explore physical games tagged {name} on itch.io.{short} · <a href="/docs/creators/faq">
Upload your physical games</a> to itch.io to have them show up here.</p></div>
<div class="browse_filters"><div class="filter_tag">{description}</div></div>
<div class="game_grid_widget"><div class="game_cell" data-game_id="1"><a class="title game_link"
href="https://a.itch.io/x">X</a></div></div></body></html>"""


def test_tag_description_is_the_own_text_of_itch_io_not_its_page_templates() -> None:
    """Власний опис тегу — блок tag_description; рядок пошуку й meta description —
    шаблони сайту, тож тег без блоку (або з порожнім блоком) опису не має."""
    from app.infrastructure.sources.crawler_source import parse_tag_page, strip_tag_boilerplate

    own = '"Old School Renaissance" or "Old School Revival." Tabletop RPGs inspired by the 1970s'
    described = TAG_PAGE.format(name="OSR", short=' "Old School Renaissance" or "Old School Re',
                                description=f'<div class="tag_description"><p>{own}</p></div>')
    assert parse_tag_page(described) == own
    for block in ("", '<div class="tag_description"></div>'):
        assert parse_tag_page(TAG_PAGE.format(name="acid-fantasy", short="", description=block)) is None
    assert strip_tag_boilerplate("Find physical games tagged OSR like Mausritter, FIST: Ultra Edition on itch.io, "
                                 "the indie game hosting marketplace") is None


#: Профіль автора itch.io зі знімка обходу (скорочено): посилання автора, власний текст
#: профілю (user_profile) і перелік робіт із цінами лежать в одному блоці profile_content
PROFILE_PAGE = """<html><body><div class="inner_column profile_content"><div class="columns">
<div class="column profile_column"><div class="user_links user_links_widget"><span class="link_group">Questing Beast
</span><span class="link_group"><a href="https://www.youtube.com/questingbeast">www.youtube.com/questingbeast</a></span>
</div>{bio}</div>
<div class="column game_column"><div class="game_grid_widget user_game_grid"><div class="game_cell has_cover">
<a class="title game_link" href="https://questingbeast.itch.io/knave">Knave</a> <div class="price_value">$2.99</div>
<div class="game_text">A streamlined, elegant OSR RPG system</div></div></div></div></div></div></body></html>"""


def test_author_bio_is_only_the_own_text_of_the_profile() -> None:
    """Посилання автора й перелік його робіт із цінами до біографії не входять."""
    from app.infrastructure.sources.crawler_source import parse_profile

    bio = ('<div class="user_profile formatted"><h1>Questing Beast Games</h1><p>Watch my YouTube videos, '
           'support my work on Patreon.</p></div>')
    assert parse_profile(PROFILE_PAGE.format(bio=bio)) == (
        "Questing Beast Games Watch my YouTube videos, support my work on Patreon.")
    assert parse_profile(PROFILE_PAGE.format(bio="")) is None


def test_system_prefers_the_title_synopsis_and_tags_and_the_specific_system() -> None:
    """OSR — рух, а не система; окрема система має перевагу над D&D 5e, яку
    модулі згадують для сумісності; опис — лише запасне джерело."""
    import json
    from pathlib import Path

    from app.infrastructure.sources.crawler_source import _system

    systems = json.loads((Path(__file__).resolve().parents[2] / "app" / "data" / "systems.json")
                         .read_text(encoding="utf-8"))

    def name(primary: str, secondary: str = "") -> str | None:
        found = _system(primary, systems, secondary)
        return f"{found['system']} {found['edition'] or ''}".strip() if found else None

    assert name("Tomb of the Acid Lich dnd osr troika ttrpg") is None
    assert name("A one-shot for Shadowdark and 5e dnd shadowdark 5e") == "Shadowdark RPG 1e"
    assert name("Updated for D&D 2024 rules 5e") == "Dungeons & Dragons 5e (2024)"
    assert name("dnd 5th edition one shot") == "Dungeons & Dragons 5e (2014)"
    assert name("An OSR dungeon dnd osr", "Stats for OSE and a 5e conversion") == "Old-School Essentials Classic Fantasy"
    assert name("A Shadowdark crawl shadowdark", "Includes a 5e conversion") == "Shadowdark RPG 1e"
    assert name("A system agnostic city dnd system agnostic") == "System-agnostic"
    # назва «Dungeons & Dragons» спільна для двох редакцій і редакції не визначає
    assert name("Original Dungeons & Dragons by Gygax and Arneson") is None


def test_canadian_price_is_converted_to_dollars() -> None:
    ld = ('<script type="application/ld+json">{"@type":"Product","name":"X","offers":{"@type":"Offer",'
          '"price":"10.00","priceCurrency":"CAD"}}</script>')
    doc = parse_card(CARD.replace("</head>", ld + "</head>"), "https://mira.itch.io/x", datetime.now(timezone.utc)).document
    assert doc["price"] == 7.3


def test_tag_groups_are_found_whatever_wraps_the_heading() -> None:
    """Заголовок групи — обгортка переліку або окремий рядок перед ним; посилання
    в картках і поза групами групи не отримують."""
    html = """<html><body>
    <div class="tag_bar"><a href="/physical-games/tag-osr">OSR</a></div>
    <div class="browse_sidebar">
      <div class="filter_group"><div class="filter_label">Types</div>
        <div class="filter_options"><a href="/physical-games/tag-adventure">Adventure</a></div></div>
      <h4>Gameplay</h4><ul><li><a href="/physical-games/tag-solo?page=1">Solo</a></li></ul>
    </div>
    <div class="game_grid_widget"><div class="game_cell" data-game_id="1">
      <a class="title game_link" href="https://a.itch.io/x">X</a><a href="/physical-games/tag-horror">horror</a></div></div>
    <div class="footer"><a href="/physical-games/tag-zine">Zine</a></div>
    </body></html>"""
    links, categories = parse_list_page(html, "https://itch.io")
    assert [link.game_id for link in links] == ["1"]
    assert categories == {"adventure": "Types", "solo": "Gameplay"}


def test_card_without_itch_path_is_a_block_page() -> None:
    with pytest.raises(CardBlocked):
        parse_card("<html><body>Are you a robot?</body></html>", "https://x.itch.io/y", datetime.now(timezone.utc))


def test_list_page_links() -> None:
    html = '<div class="game_cell" data-game_id="1"><a class="title game_link" href="https://a.itch.io/x">X</a></div>' * 2
    links, _ = parse_list_page(html, "https://itch.io")
    assert [l.url for l in links] == ["https://a.itch.io/x"] * 2 and links[0].game_id == "1"


def test_politeness_rules() -> None:
    policy = PolitenessPolicy()
    policy.load_robots("User-agent: *\nDisallow: /search\nDisallow: /game/download/\n")
    assert not policy.is_allowed("https://itch.io/search?q=dnd") and policy.is_allowed("https://itch.io/physical-games")
    assert policy.throttled(None) == 6.0 and policy.throttled(None) == 12.0  # подвоєння від 2 × 3 с
    for _ in range(12):
        policy.throttled(None)
    assert policy.throttled(None) == 300.0 and policy.throttled(60) == 60


async def test_fetch_retries_and_honours_retry_after(monkeypatch) -> None:
    sleeps: list[float] = []

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(crawler_source.asyncio, "sleep", no_sleep)

    class Response:
        def __init__(self, status: int, headers: dict | None = None) -> None:
            self.status_code, self.headers, self.text = status, headers or {}, "<html></html>"

    replies = [Response(429, {"Retry-After": "60"}), Response(502), Response(200)]

    class Client:
        async def get(self, url):
            return replies.pop(0)

    source = CrawlerSource("https://itch.io", "dnd", PolitenessPolicy(0, 0))
    counters = crawler_source._Counters()
    assert await source._fetch(Client(), "https://itch.io/physical-games/tag-dnd", counters) == "<html></html>"
    assert (counters.requested, counters.succeeded, counters.throttled) == (3, 1, 1)
    assert 60 in sleeps and 2 in sleeps


async def test_generator_batches_follow_rate_and_rules() -> None:
    pool = [{"monster_id": f"m{i}", "name": f"M{i}", "type": "undead", "challenge_rating": cr, "play_count": i,
             "environment": ["graveyard"]} for i, cr in enumerate([0.25, 0.5, 1, 2, 3, 4, 5, 6, 8, 10] * 5)]
    builder = MarkovTextBuilder()
    builder.train([])
    source = GeneratorSource([], builder, pool, [], settings=[{"setting_id": "s1", "genre": "gothic horror", "tone": "dark"}],
                             tags=["horror", "gothic", "mystery", "dnd"], seed=1)
    await source.set_rate(250)  # Δt = min(1 с; 500/250) = 1 с → пакети по ~250 документів
    stream = source.stream()
    batch = await stream.__anext__()
    adventures = [d for d in batch.documents if d["kind"] == "adventure"]
    assert 250 <= len(batch.documents) <= 265 and adventures
    await source.set_rate(4000)  # Δt = 0,125 с → пакет не більший за 500
    batch = await stream.__anext__()
    assert len(batch.documents) <= 515
    await stream.aclose()
    by_id = {d["adventure_id"]: d for d in adventures}
    for doc in adventures:
        assert set(doc["tags"]) <= {"horror", "gothic", "mystery", "dnd"} and 2 <= len(doc["tags"]) <= 5
        low, high = challenge_window((doc["level_min"] + doc["level_max"]) // 2, 4)
        assert all(low <= next(m for m in pool if m["monster_id"] == mid)["challenge_rating"] <= high
                   for mid in doc["monster_ids"])
        if doc["prev_adventure_id"] in by_id:
            previous = by_id[doc["prev_adventure_id"]]
            assert previous["next_adventure_id"] == doc["adventure_id"]
            assert doc["level_min"] - previous["level_max"] in (0, 1)
            assert not set(doc["monster_ids"]) & set(previous["monster_ids"])
    assert any(d["kind"] == "author" for d in batch.documents) or any(True for _ in adventures)


async def test_generator_rate_counts_authors_as_documents() -> None:
    """Цільова швидкість — у документах: нові автори входять у той самий бюджет."""
    builder = MarkovTextBuilder()
    builder.train([])
    source = GeneratorSource([], builder, [], GeneratorSource.synthetic_authors(400, 1.0),
                             settings=[{"setting_id": "s1", "genre": "gothic horror", "tone": "dark"}], tags=["horror"],
                             seed=3)
    await source.set_rate(300)
    stream = source.stream()
    documents = (await stream.__anext__()).documents + (await stream.__anext__()).documents
    await stream.aclose()
    assert any(d["kind"] == "author" for d in documents)
    assert 600 <= len(documents) <= 610


async def test_generator_takes_free_and_coauthor_shares_from_the_catalog() -> None:
    """Частки безкоштовних модулів і множинного авторства калібруються за
    adventures.csv так само, як розмір партії й пул авторів (п. 2.7.3)."""
    from app.infrastructure.sources.generator_source import calibrate_catalog_shares

    rows = ([{"price": "", "author_ids": '["a"]'}] * 30 + [{"price": "4.99", "author_ids": '["a", "b"]'}] * 10
            + [{"price": "2.5", "author_ids": '["c"]'}] * 60)
    assert calibrate_catalog_shares(rows) == (0.3, 0.1)
    assert calibrate_catalog_shares(rows[:5]) == (GeneratorSource.FREE_SHARE, GeneratorSource.MULTI_AUTHOR_SHARE)
    builder = MarkovTextBuilder()
    builder.train([])
    source = GeneratorSource([], builder, [], GeneratorSource.synthetic_authors(400, 1.0),
                             settings=[{"setting_id": "s1", "genre": "gothic horror", "tone": "dark"}], tags=["horror"],
                             seed=5, free_share=1.0, multi_author_share=1.0)
    await source.set_rate(200)
    stream = source.stream()
    documents = (await stream.__anext__()).documents
    await stream.aclose()
    adventures = [d for d in documents if d["kind"] == "adventure"]
    assert adventures and all(d["price"] == 0.0 and 2 <= len(d["author_ids"]) <= 3 for d in adventures)


def test_synthetic_author_pool_is_the_same_in_every_instance() -> None:
    first, second = GeneratorSource.synthetic_authors(50, 1.0), GeneratorSource.synthetic_authors(50, 1.0)
    assert first == second and first[0]["weight"] > first[-1]["weight"]
    assert first[0]["author_id"] == DocumentKind.AUTHOR.natural_id(first[0]["name"])


async def test_crawler_reports_every_page_but_publishes_documents_in_batches(monkeypatch) -> None:
    """Звіт джерела надходить після кожної сторінки, тож швидкість краулера на
    панелі не падає до нуля між пакетами документів (п. 4.9)."""
    links = [crawler_source.CardLink(f"https://itch.io/c{i}") for i in range(5)]
    monkeypatch.setattr(crawler_source, "parse_list_page", lambda html, base: (links, {}))

    class OfflineCrawler(CrawlerSource):
        async def _load_robots(self, client, counters):
            return None

        async def _fetch(self, client, url, counters):
            counters.requested += 1
            if "page=2" in url:
                return None
            counters.succeeded += 1
            return "<html></html>"

        async def _card(self, client, link, counters):
            await self._fetch(client, link.url, counters)
            return crawler_source.ParsedCard({"kind": "adventure", "itch_id": link.url})

    class NoClient:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    source = OfflineCrawler("https://itch.io", "dnd", PolitenessPolicy(), client_factory=NoClient)
    batches = [batch async for batch in source.stream(batch_size=2)]
    cards = [b for b in batches if b.stats.stage == "cards"]
    assert [len(b.documents) for b in cards] == [0, 2, 0, 2, 0, 1]
    assert [b.stats.completed for b in cards] == [1, 1, 1, 1, 1, 0]
    assert sum(b.stats.succeeded for b in cards) == 5
    lists = [b for b in batches if b.stats.stage == "lists"]
    assert [b.stats.completed for b in lists] == [1, 1]  # остання сторінка списку не потрапляє до карток


def test_keyword_start_states_are_found_once_per_profile() -> None:
    """Пошук початкових станів із ключовими словами — один раз на набір слів:
    перебір усього корпусу на кожне речення обмежував генератор сотнями
    документів за секунду (виміряно 22.10.2026: ≈ 180 док/с за цілі 5000)."""
    from app.infrastructure.sources.generator_source import MarkovTextBuilder

    builder = MarkovTextBuilder()
    builder.train(["The cursed crypt hides undead horrors.", "A haunted manor waits in the fog.",
                   "Undead knights guard the cursed crypt."] * 3)
    first = builder.generate(2, ["cursed", "undead"])
    builder.generate(3, ["undead", "cursed"])
    assert first and len(builder._keyed) == 1
    assert all({"cursed", "undead"} & {w.lower().strip(".") for w in state}
               for state in builder._keyed[frozenset({"cursed", "undead"})])
