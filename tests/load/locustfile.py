"""Сценарії навантаження Locust для розділу 6 (таблиця 4.11).

Застосунок має бути запущений (API на порту 8000, підрозділ 4.9), а сховища —
наповнені: спершу CsvLoader, далі генератор із потрібною швидкістю. Запуск
з кореня проєкту::

    locust -f tests/load/locustfile.py --host http://localhost:8000

Без параметрів Locust відкриває веб-інтерфейс на http://localhost:8089, де
задаються кількість користувачів і темп їх додавання. Для звіту зручніше
запуск без інтерфейсу з вивантаженням статистики в CSV::

    locust -f tests/load/locustfile.py --host http://localhost:8000 \\
        --headless -u 100 -r 10 -t 5m --csv logs/load

Профілі користувачів відтворюють групи запитів таблиці 1.2:

* SearchVisitor — пошук пригод, монстрів і зустрічей з фільтрами, сортуванням
  і сторінками (групи 1–3, 9). Частка повторюваних запитів LOAD_REPEAT_SHARE
  (за замовчуванням 0,7) береться з невеликого набору популярних і потрапляє
  в кеш пошуку, решта складається випадково — так вимірюється вплив кешу
  й часу життя його записів (підрозділ 4.7);
* CardReader — картки пригод, монстрів, авторів і персонажів, схожі модулі
  й ланцюжок кампанії (групи 4–6, 8); ідентифікатори беруться з видачі пошуку;
* StatisticsReader — статистика реальної гри (група 7);
* DashboardViewer — відкрита панель моніторингу: потік SSE /api/stats/stream,
  час до першого знімка й інтервал між знімками.

Змінні середовища:

* LOAD_STEPS=10,50,100,200 і LOAD_STEP_SECONDS=60 — ступінчасте навантаження
  (кількість користувачів на кожній сходинці): точка насичення шляху запитів
  і діаграма пропускної здатності;
* LOAD_RATES=100,1000,2500,5000 і LOAD_RATE_SECONDS=60 — оператор змінює
  цільову швидкість генерації сходинками через POST /api/rate, тож затримки
  шляху запитів вимірюються під навантаженням шляху команд (підрозділ 4.8);
* LOAD_THINK=0.5,2 — пауза користувача між діями, секунди (0 — без пауз).
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections import deque

from locust import HttpUser, LoadTestShape, between, constant, events, task

log = logging.getLogger("load")


def _numbers(name: str, kind: type = int) -> list:
    return [kind(value) for value in os.environ.get(name, "").replace(" ", "").split(",") if value]


REPEAT_SHARE = float(os.environ.get("LOAD_REPEAT_SHARE", "0.7"))
STEPS: list[int] = _numbers("LOAD_STEPS")
STEP_SECONDS = int(os.environ.get("LOAD_STEP_SECONDS", "60"))
RATES: list[int] = _numbers("LOAD_RATES")
RATE_SECONDS = int(os.environ.get("LOAD_RATE_SECONDS", "60"))
THINK: list[float] = _numbers("LOAD_THINK", float) or [0.5, 2.0]

#: Запити англійською, як і корпус (підрозділ 4.6)
WORDS = (
    "cursed", "village", "swamp", "cult", "dragon", "lair", "haunted", "manor", "goblin", "cave",
    "undead", "crypt", "heist", "city", "frozen", "tomb", "pirate", "island", "forest", "shrine",
    "giant", "keep", "mine", "ruins", "temple", "vampire", "castle", "desert", "oasis", "wizard", "tower",
)
POPULAR = (
    "cursed village", "dragon lair", "haunted manor", "goblin cave", "undead crypt",
    "heist", "vampire castle", "wizard tower", "one-shot horror", "swamp cult",
)
MONSTER_WORDS = ("skeleton", "zombie", "dragon", "goblin", "beholder", "lich", "wolf", "bandit", "ghoul", "troll")
ENCOUNTER_WORDS = ("fireball", "dragon", "ambush", "tavern", "goblins", "bridge", "undead", "boss")
TAGS = ("horror", "one-shot", "dungeon", "investigation", "wilderness", "urban", "undead", "mystery")
SORTS = ("rating", "reviews", "year", "duration")
CREATURE_TYPES = ("undead", "dragon", "fiend", "humanoid", "beast", "monstrosity")
COMMANDS = ("init", "attack", "cast", "check", "save")
CLASSES = ("Fighter", "Wizard", "Cleric", "Rogue", "Paladin", "Ranger", "Bard", "Warlock", "Druid", "Sorcerer")

#: Ідентифікатори з видачі пошуку — для карток і рекомендацій (на процес Locust)
SEEN: dict[str, deque[str]] = {
    "adventures": deque(maxlen=500),
    "monsters": deque(maxlen=500),
    "authors": deque(maxlen=300),
    "characters": deque(maxlen=300),
}
#: Відповіді пошуку з кешу й без нього — частка влучань у звіт розділу 6
CACHE = {"hit": 0, "miss": 0}


def _think():
    return constant(0) if THINK == [0.0] else between(THINK[0], THINK[-1])


def _remember(kind: str, items: list[dict]) -> None:
    for item in items:
        if kind == "adventures":
            SEEN["adventures"].append(item["adventure_id"])
            SEEN["authors"].extend(item.get("author_ids") or [])
            SEEN["monsters"].extend(item.get("monster_ids") or [])
        elif kind == "monsters":
            SEEN["monsters"].append(item["monster_id"])
        elif kind == "encounters":
            SEEN["characters"].extend(item.get("player_character_ids") or [])


class SearchVisitor(HttpUser):
    """Пошук із фільтрами, сортуванням і сторінками (групи 1–3, 9)."""

    weight = 6
    wait_time = _think()

    def _search(self, kind: str, params: dict) -> None:
        with self.client.get(f"/api/search/{kind}", params=params, name=f"/api/search/{kind}",
                             catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            body = response.json()
            CACHE["hit" if body.get("cache_hit") else "miss"] += 1
            _remember(kind, body.get("items") or [])

    @task(6)
    def adventures(self) -> None:
        if random.random() < REPEAT_SHARE:
            params: dict = {"q": random.choice(POPULAR)}
        else:
            # кожен десятий — без тексту, лише з фільтрами (match_all, підрозділ 4.6)
            params = {"q": "" if random.random() < 0.1 else " ".join(random.sample(WORDS, random.randint(1, 3)))}
            if not params["q"] or random.random() < 0.5:
                params["level"] = random.randint(1, 12)
            if random.random() < 0.3:
                params["players"] = random.randint(3, 6)
            if random.random() < 0.3:
                params["tags"] = random.sample(TAGS, random.randint(1, 2))
            if random.random() < 0.2:
                params["is_free"] = random.choice(("true", "false"))
            if random.random() < 0.3:
                params["sort"] = random.choice(SORTS)
            if random.random() < 0.1:
                params["page"] = 2
        self._search("adventures", params)

    @task(2)
    def monsters(self) -> None:
        params: dict = {"q": random.choice(MONSTER_WORDS)}
        if random.random() < 0.5:
            params["party_level"] = random.randint(1, 12)
        if random.random() < 0.2:
            params["creature_type"] = random.choice(CREATURE_TYPES)
        self._search("monsters", params)

    @task(1)
    def encounters(self) -> None:
        params: dict = {"q": random.choice(ENCOUNTER_WORDS)}
        if random.random() < 0.3:
            params["command"] = random.choice(COMMANDS)
        self._search("encounters", params)


class CardReader(HttpUser):
    """Картки й навігація (групи 4–6, 8): переходи з видачі пошуку."""

    weight = 3
    wait_time = _think()

    def on_start(self) -> None:
        if not SEEN["adventures"]:
            response = self.client.get("/api/search/adventures", params={"q": random.choice(POPULAR)},
                                       name="/api/search/adventures")
            if response.status_code == 200:
                _remember("adventures", response.json().get("items") or [])

    def _get(self, path: str, name: str, params: dict | None = None) -> None:
        with self.client.get(path, params=params, name=name, catch_response=True) as response:
            # 404 — документ уже видалено або ще не записано: не помилка навантаження
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(4)
    def adventure(self) -> None:
        if SEEN["adventures"]:
            self._get(f"/api/adventures/{random.choice(SEEN['adventures'])}", "/api/adventures/{id}")

    @task(2)
    def similar(self) -> None:
        if SEEN["adventures"]:
            self._get(f"/api/adventures/{random.choice(SEEN['adventures'])}/similar", "/api/adventures/{id}/similar")

    @task(1)
    def chain(self) -> None:
        if SEEN["adventures"]:
            level_from = random.randint(1, 5)
            self._get(f"/api/adventures/{random.choice(SEEN['adventures'])}/chain", "/api/adventures/{id}/chain",
                      {"level_from": level_from, "level_to": level_from + random.randint(3, 9)})

    @task(2)
    def monster(self) -> None:
        if SEEN["monsters"]:
            self._get(f"/api/monsters/{random.choice(SEEN['monsters'])}", "/api/monsters/{id}")

    @task(1)
    def author(self) -> None:
        if SEEN["authors"]:
            self._get(f"/api/authors/{random.choice(SEEN['authors'])}", "/api/authors/{id}")

    @task(1)
    def character(self) -> None:
        if SEEN["characters"]:
            self._get(f"/api/characters/{random.choice(SEEN['characters'])}", "/api/characters/{id}")


class StatisticsReader(HttpUser):
    """Статистика реальної гри (група 7): агрегації з кешем на 1 год."""

    weight = 1
    wait_time = _think()

    @task(3)
    def monsters(self) -> None:
        self.client.get("/api/statistics/monsters", params={"limit": 10}, name="/api/statistics/monsters")

    @task(2)
    def spells(self) -> None:
        self.client.get("/api/statistics/spells", name="/api/statistics/spells",
                        params={"char_class": random.choice(CLASSES), "level": random.randint(1, 15)})

    @task(1)
    def party(self) -> None:
        self.client.get("/api/statistics/party", name="/api/statistics/party")

    @task(1)
    def combat(self) -> None:
        self.client.get("/api/statistics/combat", name="/api/statistics/combat")


class DashboardViewer(HttpUser):
    """Відкрита панель моніторингу: читає кілька знімків потоку SSE.

    Locust рахує час відповіді потоку до заголовків; окремими записами типу
    SSE фіксуються час до першого знімка й інтервал між знімками (≈1 с).
    """

    weight = 1
    wait_time = between(2, 5)
    SNAPSHOTS = 5

    @task
    def watch(self) -> None:
        started = time.perf_counter()
        previous = None
        received = 0
        with self.client.get("/api/stats/stream", stream=True, name="/api/stats/stream", timeout=30,
                             catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                now = time.perf_counter()
                name, since = ("first snapshot", started) if previous is None else ("snapshot interval", previous)
                events.request.fire(request_type="SSE", name=name, response_time=(now - since) * 1000,
                                    response_length=len(line), exception=None, context={})
                previous = now
                received += 1
                if received >= self.SNAPSHOTS:
                    break
            response.success()


if RATES:

    class RateOperator(HttpUser):
        """Оператор панелі: цільова швидкість генерації сходинками LOAD_RATES
        (кожна — LOAD_RATE_SECONDS), як повзунок на вкладці «Моніторинг»."""

        fixed_count = 1
        wait_time = constant(1)

        def on_start(self) -> None:
            self._started = time.monotonic()
            self._applied: int | None = None

        @task
        def follow_schedule(self) -> None:
            step = min(int((time.monotonic() - self._started) // RATE_SECONDS), len(RATES) - 1)
            if RATES[step] != self._applied:
                response = self.client.post("/api/rate", json={"documents_per_second": RATES[step]}, name="/api/rate")
                if response.status_code == 200:
                    self._applied = RATES[step]
                    log.info("цільова швидкість генерації: %d док/с", RATES[step])


if STEPS:

    class StepLoadShape(LoadTestShape):
        """Ступінчасте навантаження: LOAD_STEPS користувачів по LOAD_STEP_SECONDS
        на сходинку; після останньої сходинки тест завершується."""

        def tick(self):
            step = int(self.get_run_time() // STEP_SECONDS)
            if step >= len(STEPS):
                return None
            users = STEPS[step]
            return users, max(1, users // 10)


@events.test_stop.add_listener
def report_cache_share(**_kwargs) -> None:
    total = CACHE["hit"] + CACHE["miss"]
    if total:
        log.info("пошук: %d відповідей, з кешу %d (%.1f %%)", total, CACHE["hit"], CACHE["hit"] * 100 / total)
