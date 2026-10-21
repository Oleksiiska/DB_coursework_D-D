"""Джерело: генератор документів (Шлях В, п. 2.7).

Забезпечує обсяг, потрібний для навантажувального тестування.
Працює окремим процесом: навантаження на процесор, а не на введення-виведення.
"""

from __future__ import annotations

import asyncio
import bisect
import itertools
import json
import math
import random
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Sequence

from app.domain.values import DocumentKind, SourceBatch, SourceStats, challenge_window


@dataclass(slots=True)
class ThemeProfile:
    """Тематичний профіль — джерело узгодженості полів (п. 2.7.1).

    Без кореляції між темами й монстрами запит «схожі пригоди»
    повертає шум, і роботу графової бази неможливо продемонструвати.
    Профіль задає розподіли тегів, типів монстрів і середовищ, лексику
    назв і локацій, ключові слова текстів і тональність, а також жанри
    й тональності ігрових світів, з якими він узгоджений.
    """

    name: str
    tag_weights: dict[str, float] = field(default_factory=dict)
    monster_type_weights: dict[str, float] = field(default_factory=dict)
    environment_weights: dict[str, float] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    tone: str | None = None
    setting_genres: tuple[str, ...] = ()
    title_adjectives: tuple[str, ...] = ()
    title_nouns: tuple[str, ...] = ()
    location_kinds: tuple[str, ...] = ()


#: П'ять тематичних профілів (п. 2.7.1)
DEFAULT_PROFILES: list[ThemeProfile] = [
    ThemeProfile(
        name="gothic horror",
        tag_weights={"horror": 3, "gothic": 2, "investigation": 1.5, "mystery": 1.5, "undead": 1.5,
                     "dark-fantasy": 1.5, "one-shot": 1, "5e": 1, "dnd": 1},
        monster_type_weights={"undead": 5, "fiend": 2, "aberration": 1.5, "monstrosity": 1, "humanoid": 1, "beast": 0.5},
        environment_weights={"graveyard": 4, "swamp": 2, "forest": 1.5, "urban": 1, "underdark": 1},
        keywords=["curse", "crypt", "fog", "village", "ghost", "manor", "grave", "night"],
        tone="dark",
        setting_genres=("gothic horror", "dark", "grim", "gritty"),
        title_adjectives=("Drowned", "Cursed", "Hollow", "Silent", "Weeping", "Forsaken", "Ashen", "Haunted"),
        title_nouns=("Chapel", "Manor", "Village", "Crypt", "Abbey", "Orphanage", "Mill", "Lighthouse"),
        location_kinds=("ruined chapel", "family crypt", "flooded cellar", "abandoned mill", "misty graveyard",
                        "manor library", "bell tower"),
    ),
    ThemeProfile(
        name="dungeon crawl",
        tag_weights={"dungeon": 3, "dungeon-crawl": 2.5, "osr": 1.5, "fantasy": 1.5, "megadungeon": 0.7,
                     "one-shot": 1, "5e": 1, "dnd": 1},
        monster_type_weights={"monstrosity": 3, "aberration": 2, "construct": 1.5, "ooze": 1.5, "undead": 1.5,
                              "humanoid": 1.5, "dragon": 0.8},
        environment_weights={"underdark": 4, "mountain": 1, "hill": 1},
        keywords=["dungeon", "trap", "treasure", "tunnel", "vault", "door", "torch", "ancient"],
        tone="adventurous",
        setting_genres=("sword and sorcery", "classic fantasy", "high fantasy", "adventurous", "gritty"),
        title_adjectives=("Sunken", "Forgotten", "Shattered", "Black", "Endless", "Gilded", "Molten"),
        title_nouns=("Vault", "Halls", "Labyrinth", "Mines", "Tomb", "Citadel", "Pits"),
        location_kinds=("collapsed gatehouse", "fungal cavern", "trapped corridor", "flooded shrine",
                        "treasure vault", "forge hall", "spiral stair"),
    ),
    ThemeProfile(
        name="urban investigation",
        tag_weights={"investigation": 3, "mystery": 2.5, "urban": 2, "noir": 1.5, "intrigue": 1,
                     "one-shot": 1.2, "5e": 1, "dnd": 1},
        monster_type_weights={"humanoid": 4, "fiend": 1.5, "construct": 1, "aberration": 1, "undead": 1, "monstrosity": 0.5},
        environment_weights={"urban": 5},
        keywords=["murder", "guild", "alley", "witness", "clue", "city", "secret", "watch"],
        tone="noir",
        setting_genres=("urban fantasy", "magitech fantasy", "noir", "intrigue"),
        title_adjectives=("Crimson", "Velvet", "Gilded", "Midnight", "Silent", "Broken"),
        title_nouns=("Ledger", "Mask", "Market", "Quarter", "Masquerade", "Bridge"),
        location_kinds=("dockside tavern", "guild archive", "sewer junction", "noble townhouse", "black market",
                        "watch house"),
    ),
    ThemeProfile(
        name="sea voyage",
        tag_weights={"sea": 3, "pirates": 2.5, "nautical": 2, "exploration": 1.5, "sandbox": 1, "5e": 1, "dnd": 1},
        monster_type_weights={"beast": 2.5, "monstrosity": 2, "humanoid": 2, "elemental": 1.2, "undead": 0.8, "dragon": 0.6},
        environment_weights={"coastal": 4, "underwater": 4},
        keywords=["ship", "storm", "island", "reef", "captain", "tide", "harbor", "sail"],
        tone="adventurous",
        setting_genres=("high fantasy", "space fantasy", "adventurous", "whimsical", "heroic"),
        title_adjectives=("Salt", "Storm", "Sunken", "Crimson", "Lost", "Drifting"),
        title_nouns=("Reef", "Isle", "Tide", "Wreck", "Harbor", "Lighthouse"),
        location_kinds=("storm-tossed deck", "smugglers' cove", "coral reef", "lighthouse rock", "sunken wreck",
                        "harbor market"),
    ),
    ThemeProfile(
        name="political intrigue",
        tag_weights={"intrigue": 3, "politics": 2.5, "roleplay": 2, "social": 1.5, "mystery": 1, "5e": 1, "dnd": 1},
        monster_type_weights={"humanoid": 5, "fiend": 1.5, "fey": 1.2, "celestial": 0.8, "dragon": 0.6},
        environment_weights={"urban": 4, "forest": 0.5, "mountain": 0.5},
        keywords=["court", "heir", "treaty", "council", "spy", "throne", "noble", "oath"],
        tone="intrigue",
        setting_genres=("urban fantasy", "mythic fantasy", "epic fantasy", "intrigue", "magitech fantasy"),
        title_adjectives=("Gilded", "Silver", "Hollow", "Velvet", "Burning", "Last"),
        title_nouns=("Throne", "Crown", "Court", "Treaty", "Council", "Banquet"),
        location_kinds=("throne room", "embassy garden", "council chamber", "royal archive", "masked ball",
                        "prison tower"),
    ),
]

_TOPONYM_ROOTS = ("Mire", "Raven", "Ash", "Thorn", "Grim", "Wolf", "Stone", "Frost", "Black", "Elder", "Hollow",
                  "Dun", "Kel", "Mor", "Vel", "Brack", "Gal", "Hearth", "Oak", "Salt")
_TOPONYM_SUFFIXES = ("fen", "moor", "hold", "wick", "mere", "ford", "crag", "vale", "haven", "barrow", "gate",
                     "watch", "stead", "shire")
_PSEUDONYM_FIRST = ("Mira", "Oren", "Talia", "Bram", "Ilse", "Corwin", "Nadia", "Silas", "Wren", "Edda", "Fen",
                    "Lio", "Rhea", "Tobin", "Vesna", "Kai", "Ada", "Hugo", "Ysolde", "Piotr")
_PSEUDONYM_LAST = ("Thornwood", "Ashgrove", "Quill", "Blackmere", "Harrow", "Lantern", "Vance", "Dunmore",
                   "Fairweather", "Stoneglass", "Inkwell", "Marsh", "Holloway", "Redfern", "Gale")
_STUDIO_WORDS = ("Press", "Games", "Studio", "Workshop", "Tales", "Dice Co.")
#: Мови видання для згенерованих пригод — назви, як у мітці Languages на itch.io
_LANGUAGES = (("English", 0.9), ("Spanish", 0.03), ("German", 0.025), ("French", 0.02), ("Portuguese", 0.015),
              ("Polish", 0.01))

#: Вбудований корпус на випадок, коли підготовлених CSV ще немає
_FALLBACK_CORPUS = [
    "The party arrives in a quiet village where the bells have not rung for a week.",
    "A frightened miller begs the heroes to find his missing daughter before the next full moon.",
    "Beneath the old chapel lies a crypt sealed by a forgotten order of knights.",
    "Strange lights dance over the marsh and travellers vanish on the road at night.",
    "The heroes must recover a stolen relic before the cult completes its ritual.",
    "An ancient dungeon stirs again as goblins pour out of the collapsed gatehouse.",
    "The captain of a battered ship offers passage in exchange for protection from pirates.",
    "A noble heir is poisoned at the banquet and every guest is a suspect.",
    "The city watch is corrupt, and the only witness hides in the sewers below the market.",
    "Old maps speak of a vault guarded by constructs that never sleep.",
    "When the storm breaks, a ghost ship drifts into the harbor with its lanterns still burning.",
    "The council demands proof of treason before the treaty is signed at dawn.",
]


class MarkovTextBuilder:
    """Марковський ланцюг третього порядку на рівні слів.

    Корпус — описи довідника та анотації каталогу. Наратив реальних
    ігор не використовується: середня довжина репліки 28 символів
    зруйнувала б зв'язність (п. 2.7.4).
    """

    MAX_WORDS = 40

    def __init__(self, order: int = 3) -> None:
        self._order = order
        self._transitions: dict[tuple[str, ...], list[str]] = {}
        self._starts: list[tuple[str, ...]] = []
        self._rng = random.Random()

    def train(self, corpus: list[str]) -> None:
        sentences = [s for text in (corpus or _FALLBACK_CORPUS) for s in re.split(r"(?<=[.!?])\s+", text) if s]
        transitions: dict[tuple[str, ...], list[str]] = {}
        starts: list[tuple[str, ...]] = []
        for sentence in sentences:
            words = sentence.split()
            if len(words) <= self._order:
                continue
            starts.append(tuple(words[: self._order]))
            for i in range(len(words) - self._order):
                transitions.setdefault(tuple(words[i : i + self._order]), []).append(words[i + self._order])
        if not starts and corpus:
            return self.train([])
        self._transitions, self._starts = transitions, starts

    def generate(self, sentences: int, keywords: list[str]) -> str:
        """Генерація з примусовою вставкою ключових слів профілю: речення
        починається зі стану, що містить ключове слово, якщо такий є."""
        if not self._starts:
            self.train([])
        rng = self._rng
        wanted = {k.lower() for k in keywords}
        keyed = [s for s in self._starts if wanted & {w.lower().strip(".,;:!?") for w in s}] if wanted else []
        result: list[str] = []
        for _ in range(sentences):
            for _attempt in range(4):  # без повторів речень в одному тексті
                state = rng.choice(keyed if keyed and rng.random() < 0.5 else self._starts)
                words = list(state)
                while len(words) < self.MAX_WORDS and not words[-1].endswith((".", "!", "?")):
                    options = self._transitions.get(tuple(words[-self._order :]))
                    if not options:
                        break
                    words.append(rng.choice(options))
                sentence = " ".join(words)
                sentence = sentence if sentence.endswith((".", "!", "?")) else sentence.rstrip(",;:") + "."
                if sentence not in result:
                    break
            result.append(sentence)
        return " ".join(result)


def calibrate_author_pool(adventure_rows: Sequence[dict]) -> tuple[int, float]:
    """Розмір пулу синтетичних авторів і показник закону Ципфа за природним
    розподілом робіт реальних авторів каталогу (п. 2.2); без даних — 800 і 1,0."""
    counts: Counter[str] = Counter()
    for row in adventure_rows:
        raw = row.get("author_ids") or "[]"
        try:
            ids = json.loads(raw) if isinstance(raw, str) and raw.startswith("[") else str(raw).split("|")
        except json.JSONDecodeError:
            continue
        counts.update(i for i in ids if i)
    if len(counts) < 20:
        return 800, 1.0
    frequencies = sorted(counts.values(), reverse=True)
    xs = [math.log(rank) for rank in range(1, len(frequencies) + 1)]
    ys = [math.log(f) for f in frequencies]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sum((x - mean_x) ** 2 for x in xs)
    return len(counts), min(2.0, max(0.5, -slope))


class _Weighted:
    """Зважений вибір із наперед обчисленими накопиченими вагами."""

    __slots__ = ("items", "cumulative")

    def __init__(self, pairs: Sequence[tuple[object, float]]) -> None:
        pairs = [(item, weight) for item, weight in pairs if weight > 0]
        self.items = [item for item, _ in pairs]
        self.cumulative = list(itertools.accumulate(weight for _, weight in pairs))

    def __bool__(self) -> bool:
        return bool(self.items)

    def pick(self, rng: random.Random) -> object:
        return self.items[bisect.bisect(self.cumulative, rng.random() * self.cumulative[-1])]


class GeneratorSource:
    """Реалізує порт `DocumentSource`.

    Цільову частку швидкості r задає процес генератора (set_rate); джерело
    виробляє пакети по r · Δt документів, де Δt = min(1 с; 500 / r), і
    переносить дробову частину на наступний пакет (п. 4.8). Якщо формування
    пакета триває довше за Δt, генератор працює на межі процесора.
    """

    MIN_RATE = 10
    MAX_RATE = 5000
    #: Пакет не перевищує 500 документів, за малих швидкостей — раз на секунду
    MAX_BATCH = 500
    #: Калібрування за реальними даними (п. 2.7.3): частка безкоштовних модулів
    #: на itch.io і частка множинного авторства
    FREE_SHARE = 0.37
    MULTI_AUTHOR_SHARE = 0.01
    #: Імовірність продовження ланцюжка кампанії того самого світу (таблиця 2.13)
    CHAIN_SHARE = 0.3
    #: Простір імен ігрових псевдонімів: однаковий пул у всіх екземплярах
    POOL_SEED = 2026

    def __init__(
        self,
        profiles: list[ThemeProfile],
        text_builder: MarkovTextBuilder,
        monster_pool: list[dict],
        author_pool: list[dict],
        rate: int = 100,
        settings: list[dict] | None = None,
        systems: list[dict] | None = None,
        publishers: list[dict] | None = None,
        tags: list[str] | None = None,
        party_median: int = 4,
        seed: int | None = None,
    ) -> None:
        self._profiles = profiles or DEFAULT_PROFILES
        self._text_builder = text_builder
        self._monster_pool = monster_pool
        self._author_pool = author_pool or self.synthetic_authors(800, 1.0)
        self._rate = rate
        #: Списки вручну з app/data/: світ пригоди, система з редакцією
        #: й базовими книгами, видавництво (таблиці 2.13, А.2)
        self._settings = settings or []
        self._systems = systems or []
        self._publishers = publishers or []
        self._tags = set(tags or [])
        self._party_median = party_median
        self._rng = random.Random(seed)
        self._authors = _Weighted([(a, a["weight"]) for a in self._author_pool])
        self._systems_choice = _Weighted([(s, s.get("weight", 1.0)) for s in self._systems])
        self._publishers_choice = _Weighted([(p["name"], p.get("weight", 1.0)) for p in self._publishers])
        self._languages = _Weighted(_LANGUAGES)
        self._profile_tags = {p.name: self._tag_choice(p) for p in self._profiles}
        self._profile_settings = {p.name: self._setting_choice(p) for p in self._profiles}
        self._monster_choices: dict[tuple[str, int], _Weighted] = {}
        self._emitted_authors: set[str] = set()

    @property
    def name(self) -> str:
        return "generator"

    @staticmethod
    def synthetic_authors(size: int, exponent: float) -> list[dict]:
        """Пул синтетичних псевдонімів за шаблоном (таблиця А.4) з вагами
        закону Ципфа; генерується з фіксованим зерном, тож у всіх екземплярах
        генератора однаковий, а ключ автора — UUIDv5 від псевдоніма."""
        rng = random.Random(GeneratorSource.POOL_SEED)
        names: list[str] = []
        seen: set[str] = set()
        while len(names) < size:
            first, last = rng.choice(_PSEUDONYM_FIRST), rng.choice(_PSEUDONYM_LAST)
            name = f"{last} {rng.choice(_STUDIO_WORDS)}" if rng.random() < 0.25 else f"{first} {last}"
            if name in seen:
                name = f"{name} {len(names)}"
            seen.add(name)
            names.append(name)
        return [
            {"author_id": DocumentKind.AUTHOR.natural_id(name), "name": name, "weight": 1 / (rank ** exponent)}
            for rank, name in enumerate(names, start=1)
        ]

    async def stream(self, batch_size: int = 500) -> AsyncIterator[SourceBatch]:
        """Нескінченний потік пакетів зі швидкістю, заданою set_rate.

        Швидкість рахується в документах: до неї входять і пригоди, і
        документи синтетичних авторів, що з'являються вперше, — тож
        фактична швидкість генератора на панелі збігається з цільовою.
        """
        carry = 0.0
        next_tick = time.monotonic()
        while True:
            rate = max(1, self._rate)
            interval = min(1.0, self.MAX_BATCH / rate)
            wanted = rate * interval + carry
            count = int(wanted)
            carry = wanted - count
            documents: list[dict] = []
            generated = 0
            while generated < count:
                chain, authors = self._build_chain()
                documents.extend(authors)
                documents.extend(chain)
                generated += len(chain) + len(authors)
            carry -= generated - count
            if documents:
                yield SourceBatch(documents, SourceStats(requested=len(documents), succeeded=len(documents)))
            next_tick += interval
            delay = next_tick - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_tick = time.monotonic()  # межа процесора: темп не наздоганяється пакетами-сплесками
                await asyncio.sleep(0)

    async def set_rate(self, documents_per_second: int) -> None:
        """Частка r = R / N може бути дробовою: дробова частина пакета
        переноситься на наступний."""
        self._rate = max(1.0, float(documents_per_second))

    def build_document(self) -> dict:
        """Формування одного документа за обраним профілем."""
        profile = self._rng.choice(self._profiles)
        setting = self._pick_setting(profile)
        level_min = 1 + int(16 * self._rng.random() ** 2)  # 1–17 зі зміщенням до низьких рівнів
        document = self._adventure(profile, setting, level_min, exclude_monsters=set())
        document.pop("_authors")
        return document

    def _build_chain(self) -> tuple[list[dict], list[dict]]:
        """Пригода й, з імовірністю 30% на кожну ланку, її продовження в тому
        самому світі: рівні неперервні, монстри в ланцюжку не повторюються."""
        rng = self._rng
        profile = rng.choice(self._profiles)
        setting = self._pick_setting(profile)
        chain = [self._adventure(profile, setting, 1 + int(16 * rng.random() ** 2), set())]
        used = set(chain[0]["monster_ids"])
        while rng.random() < self.CHAIN_SHARE and chain[-1]["level_max"] < 20:
            level_min = min(20, chain[-1]["level_max"] + rng.choice((0, 1)))
            following = self._adventure(profile, setting, level_min, used)
            following["prev_adventure_id"] = chain[-1]["adventure_id"]
            chain[-1]["next_adventure_id"] = following["adventure_id"]
            used |= set(following["monster_ids"])
            chain.append(following)
        authors = []
        for document in chain:
            for author in document.pop("_authors"):
                if author["author_id"] not in self._emitted_authors:
                    self._emitted_authors.add(author["author_id"])
                    authors.append(self._author_document(author, profile))
        return chain, authors

    def _adventure(self, profile: ThemeProfile, setting: dict | None, level_min: int, exclude_monsters: set[str]) -> dict:
        rng = self._rng
        level_max = min(20, level_min + rng.randint(0, 3))
        roll = rng.random()
        session_count = 1 if roll < 0.55 else rng.randint(2, 3) if roll < 0.85 else rng.randint(4, 8)
        duration = max(1.0, round(rng.gauss(4 * session_count, 0.75 * session_count), 1))
        players_min = max(1, self._party_median + rng.choice((-1, -1, 0, 0, 0, 1)))
        monsters = self._pick_monsters(level_min, level_max, profile, exclude_monsters)
        difficulty = max(1, min(5, round(1 + level_max / 5 + (len(monsters) - 3) / 3 + rng.gauss(0, 0.5))))
        toponym = self._toponym()
        locations = [self._toponym() for _ in range(rng.randint(3, 7))]
        antagonist = max(monsters, key=lambda m: m["challenge_rating"], default=None)
        system = self._systems_choice.pick(rng) if self._systems_choice else {}
        synopsis = self._text_builder.generate(rng.randint(3, 5), profile.keywords)
        full_text = self._full_text(profile, locations, monsters, synopsis)
        price = 0.0 if rng.random() < self.FREE_SHARE else round(math.exp(rng.gauss(1.6, 0.6)), 2)
        authors = self._pick_authors()
        return {
            "kind": DocumentKind.ADVENTURE.value,
            "origin": "generated",
            "adventure_id": str(uuid.uuid4()),
            "title": f"The {rng.choice(profile.title_adjectives)} {rng.choice(profile.title_nouns)} of {toponym}",
            "hook": self._hook(profile, locations[0], toponym, antagonist),
            "synopsis": synopsis,
            "full_text": full_text,
            "location_names": locations,
            "system": system.get("system"),
            "edition": system.get("edition"),
            "required_books": list(system.get("core_books") or []),
            "publication_language": self._languages.pick(rng),
            "level_min": level_min,
            "level_max": level_max,
            "players_min": players_min,
            "players_max": players_min + rng.randint(0, 2),
            "duration_hours": duration,
            "session_count": session_count,
            "difficulty": difficulty,
            "published_year": 2026 - min(12, int(rng.expovariate(0.35))),
            "page_count": max(2, len(full_text) // 1800 + rng.randint(1, 6)),
            "price": price,
            "publisher": self._publishers_choice.pick(rng) if self._publishers_choice else None,
            "setting_id": setting.get("setting_id") if setting else None,
            "tags": self._pick_tags(profile),
            "monster_ids": [m["monster_id"] for m in monsters],
            "author_ids": [a["author_id"] for a in authors],
            "prev_adventure_id": None,
            "next_adventure_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "derived_fields": ["difficulty", "duration_hours", "page_count"],
            "_authors": authors,
        }

    def _pick_monsters(self, level_min: int, level_max: int, profile: ThemeProfile, exclude: set[str]) -> list[dict]:
        """Відбір за рівнем небезпеки під рівень партії: 3–8 монстрів SRD.

        Ваги пропорційні реальній популярності (play_count із FIREBALL) і
        вагам типу й середовища профілю: часті істоти трапляються часто,
        рідкісні — рідко.
        """
        level = (level_min + level_max) // 2
        key = (profile.name, level)
        if key not in self._monster_choices:
            low, high = challenge_window(level, self._party_median)
            candidates = []
            for monster in self._monster_pool:
                if not low <= monster["challenge_rating"] <= high:
                    continue
                type_weight = profile.monster_type_weights.get(str(monster["type"]).split(" ")[0].lower(), 0.2)
                env_weight = max([profile.environment_weights.get(e, 0.3) for e in monster.get("environment") or []] or [0.3])
                candidates.append((monster, ((monster.get("play_count") or 0) + 1) * type_weight * env_weight))
            self._monster_choices[key] = _Weighted(candidates)
        choice = self._monster_choices[key]
        if not choice:
            return []
        wanted = min(self._rng.randint(3, 8), len(choice.items))
        picked: dict[str, dict] = {}
        for _ in range(wanted * 6):
            monster = choice.pick(self._rng)
            if monster["monster_id"] not in exclude:
                picked[monster["monster_id"]] = monster
            if len(picked) >= wanted:
                break
        return list(picked.values())

    def _pick_authors(self) -> list[dict]:
        """Вибір за законом Ципфа: 1 автор — 99%, 2–3 — 1%."""
        count = 1 if self._rng.random() >= self.MULTI_AUTHOR_SHARE else self._rng.randint(2, 3)
        picked: dict[str, dict] = {}
        while len(picked) < count:
            author = self._authors.pick(self._rng)
            picked[author["author_id"]] = author
        return list(picked.values())

    def _pick_tags(self, profile: ThemeProfile) -> list[str]:
        """2–5 тегів із розподілу профілю, лише з довідника тегів."""
        choice = self._profile_tags[profile.name]
        wanted = min(self._rng.randint(2, 5), len(choice.items))
        picked: dict[str, None] = {}
        for _ in range(wanted * 6):
            picked[choice.pick(self._rng)] = None
            if len(picked) >= wanted:
                break
        return list(picked)

    def _pick_setting(self, profile: ThemeProfile) -> dict | None:
        choice = self._profile_settings[profile.name]
        return choice.pick(self._rng) if choice else None

    def _tag_choice(self, profile: ThemeProfile) -> _Weighted:
        weights = {tag: w for tag, w in profile.tag_weights.items() if not self._tags or tag in self._tags}
        if len(weights) < 2 and self._tags:
            weights.update({tag: 0.2 for tag in sorted(self._tags)})
        return _Weighted(sorted(weights.items()))

    def _setting_choice(self, profile: ThemeProfile) -> _Weighted:
        """Світ, узгоджений із жанром профілю: збіг жанру чи тональності
        світу важить у п'ять разів більше за решту світів."""
        return _Weighted([
            (s, 5.0 if {s.get("genre"), s.get("tone")} & set(profile.setting_genres) else 1.0) for s in self._settings
        ])

    def _toponym(self) -> str:
        """Генератор топонімів «корінь + суфікс»: Mirefen, Ravenhold."""
        return self._rng.choice(_TOPONYM_ROOTS) + self._rng.choice(_TOPONYM_SUFFIXES)

    def _hook(self, profile: ThemeProfile, location: str, toponym: str, antagonist: dict | None) -> str:
        threat = antagonist["name"] if antagonist else self._rng.choice(profile.keywords)
        return (f"When {threat} is seen near {location}, the people of {toponym} beg the heroes "
                f"to end the {self._rng.choice(profile.keywords)} before it is too late.")

    def _full_text(self, profile: ThemeProfile, locations: list[str], monsters: list[dict], synopsis: str) -> str:
        """Структурна збірка: вступ, 3–7 локацій, 2–5 зустрічей, фінал (таблиця 2.13)."""
        rng = self._rng
        parts = ["Introduction. " + synopsis]
        for number, name in enumerate(locations, start=1):
            parts.append(f"Location {number}: {name}, a {rng.choice(profile.location_kinds)}. "
                         f"The heroes find signs of {rng.choice(profile.keywords)} here.")
        for number in range(1, rng.randint(2, 5) + 1):
            foe = rng.choice(monsters)["name"] if monsters else "a hidden foe"
            parts.append(f"Encounter {number}: {foe} attacks near {rng.choice(locations)}.")
        parts.append("Conclusion. " + self._text_builder.generate(1, profile.keywords))
        return "\n\n".join(parts)

    def _author_document(self, author: dict, profile: ThemeProfile) -> dict:
        """Документ синтетичного автора — надходить разом із першою його пригодою."""
        return {
            "kind": DocumentKind.AUTHOR.value,
            "origin": "generated",
            "author_id": author["author_id"],
            "name": author["name"],
            "bio": f"{author['name']} writes tabletop adventures. " + self._text_builder.generate(1, profile.keywords),
        }
