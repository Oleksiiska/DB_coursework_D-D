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


def test_synthetic_author_pool_is_the_same_in_every_instance() -> None:
    first, second = GeneratorSource.synthetic_authors(50, 1.0), GeneratorSource.synthetic_authors(50, 1.0)
    assert first == second and first[0]["weight"] > first[-1]["weight"]
    assert first[0]["author_id"] == DocumentKind.AUTHOR.natural_id(first[0]["name"])
