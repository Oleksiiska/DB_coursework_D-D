"""Адаптер графового сховища (Neo4j, асинхронний драйвер, протокол Bolt)."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Sequence

log = logging.getLogger("db.neo4j")

#: Обмеження унікальності: MERGE за ключем іде через індекс і не створює
#: дублікатів вузлів за паралельного запису кількома процесами
SCHEMA = (
    "CREATE CONSTRAINT adventure_id IF NOT EXISTS FOR (n:Adventure) REQUIRE n.adventure_id IS UNIQUE",
    "CREATE CONSTRAINT author_id IF NOT EXISTS FOR (n:Author) REQUIRE n.author_id IS UNIQUE",
    "CREATE CONSTRAINT tag_name IF NOT EXISTS FOR (n:Tag) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT setting_id IF NOT EXISTS FOR (n:Setting) REQUIRE n.setting_id IS UNIQUE",
    "CREATE CONSTRAINT monster_id IF NOT EXISTS FOR (n:Monster) REQUIRE n.monster_id IS UNIQUE",
    "CREATE CONSTRAINT encounter_id IF NOT EXISTS FOR (n:Encounter) REQUIRE n.encounter_id IS UNIQUE",
    "CREATE INDEX adventure_setting IF NOT EXISTS FOR (n:Adventure) ON (n.setting_id)",
)

#: Ідемпотентне оновлення вузлів і ребер пакетом за видом документа. Ребро
#: до ще не записаного документа створює вузол лише з ідентифікатором, а
#: властивості вузла заповнюються, коли надходить сам документ (п. 2.2)
MERGE_QUERIES: dict[str, str] = {
    "adventure": """
        UNWIND $rows AS row
        MERGE (a:Adventure {adventure_id: row.id})
        SET a.title = row.title, a.setting_id = row.setting_id,
            a.level_min = row.level_min, a.level_max = row.level_max
        FOREACH (sid IN CASE WHEN row.setting_id IS NULL THEN [] ELSE [row.setting_id] END |
            MERGE (s:Setting {setting_id: sid}) MERGE (a)-[:IN_SETTING]->(s))
        FOREACH (aid IN row.author_ids | MERGE (au:Author {author_id: aid}) MERGE (au)-[:WROTE]->(a))
        FOREACH (tag IN row.tags | MERGE (t:Tag {name: tag}) MERGE (a)-[:HAS_TAG]->(t))
        FOREACH (mid IN row.monster_ids | MERGE (m:Monster {monster_id: mid}) MERGE (a)-[:FEATURES]->(m))
        FOREACH (pid IN CASE WHEN row.prev_adventure_id IS NULL THEN [] ELSE [row.prev_adventure_id] END |
            MERGE (p:Adventure {adventure_id: pid}) MERGE (p)-[:NEXT]->(a))
        FOREACH (nid IN CASE WHEN row.next_adventure_id IS NULL THEN [] ELSE [row.next_adventure_id] END |
            MERGE (n:Adventure {adventure_id: nid}) MERGE (a)-[:NEXT]->(n))
        WITH row
        UNWIND row.author_ids AS first
        UNWIND row.author_ids AS second
        WITH first, second WHERE first < second
        MATCH (x:Author {author_id: first})
        MATCH (y:Author {author_id: second})
        MERGE (x)-[:CO_AUTHOR]-(y)
    """,
    "author": """
        UNWIND $rows AS row
        MERGE (n:Author {author_id: row.id}) SET n.name = row.name
    """,
    "tag": """
        UNWIND $rows AS row
        MERGE (n:Tag {name: row.name}) SET n.tag_id = row.id
    """,
    "setting": """
        UNWIND $rows AS row
        MERGE (s:Setting {setting_id: row.id}) SET s.name = row.name
        FOREACH (rid IN row.related_setting_ids |
            MERGE (r:Setting {setting_id: rid}) MERGE (s)-[:RELATED]-(r))
    """,
    "monster": """
        UNWIND $rows AS row
        MERGE (m:Monster {monster_id: row.id})
        SET m.name = row.name, m.type = row.type, m.challenge_rating = row.challenge_rating
    """,
    "encounter": """
        UNWIND $rows AS row
        MERGE (:Encounter {encounter_id: row.id})
    """,
    "participation": """
        UNWIND $rows AS row
        MERGE (e:Encounter {encounter_id: row.encounter_id})
        MERGE (m:Monster {monster_id: row.monster_id})
        MERGE (e)-[r:INVOLVES]->(m)
        ON CREATE SET r.instances = [row.instance_label]
        ON MATCH SET r.instances = CASE WHEN row.instance_label IN r.instances
                                        THEN r.instances ELSE r.instances + row.instance_label END
    """,
}

#: Рекомендації: кандидати — через найрідкісніші спільні теги й монстрів
#: (обхід на дві ланки від вузла пригоди), ранжування — за кількістю спільних
#: монстрів (вага 2) і тегів. Підзапити — з явним переліком змінних CALL (…),
#: синтаксисом Neo4j 5.26 замість застарілого імпорту через WITH
SIMILAR_QUERY = """
    MATCH (a:Adventure {adventure_id: $id})
    CALL (a) {
        MATCH (a)-[:HAS_TAG|FEATURES]->(f)
        WITH a, f ORDER BY COUNT { (f)<-[:HAS_TAG|FEATURES]-() } ASC LIMIT $features
        CALL (a, f) {
            MATCH (f)<-[:HAS_TAG|FEATURES]-(b:Adventure)
            WHERE b <> a
            RETURN b LIMIT $per_feature
        }
        RETURN DISTINCT b
    }
    WITH a, b,
         COUNT { (a)-[:HAS_TAG]->(:Tag)<-[:HAS_TAG]-(b) } AS shared_tags,
         COUNT { (a)-[:FEATURES]->(:Monster)<-[:FEATURES]-(b) } AS shared_monsters
    RETURN b.adventure_id AS id
    ORDER BY shared_monsters * 2 + shared_tags DESC, id
    LIMIT $limit
"""

#: Умови ланцюжка кампанії на вузли шляху: той самий світ, рівні в межах
#: діапазону, неперервність рівнів між сусідами й жодного спільного монстра
_CHAIN_CONDITIONS = """
    WITH p, nodes(p) AS ns
    WHERE all(x IN ns WHERE x.setting_id = $setting_id AND x.level_min IS NOT NULL
                          AND x.level_max IS NOT NULL
                          AND x.level_max >= $level_from AND x.level_min <= $level_to)
      AND all(i IN range(0, size(ns) - 2) WHERE ns[i + 1].level_min - ns[i].level_max IN [0, 1])
    WITH ns, reduce(acc = [], x IN ns | acc + [(x)-[:FEATURES]->(m:Monster) | m.monster_id]) AS ms
    WHERE all(i IN range(0, size(ms) - 1) WHERE NOT ms[i] IN ms[i + 1..])
    WITH ns,
         CASE WHEN ns[0].level_min <= $level_from THEN $level_from ELSE ns[0].level_min END AS lo,
         CASE WHEN ns[size(ns) - 1].level_max >= $level_to THEN $level_to ELSE ns[size(ns) - 1].level_max END AS hi
    RETURN [x IN ns | x.adventure_id] AS chain
    ORDER BY hi - lo DESC, size(ns) ASC
    LIMIT 1
"""

CHAIN_THROUGH_QUERY = """
    MATCH p = (:Adventure)-[:NEXT*0..20]->(:Adventure {adventure_id: $through})-[:NEXT*0..20]->(:Adventure)
""" + _CHAIN_CONDITIONS

CHAIN_IN_SETTING_QUERY = """
    MATCH (head:Adventure {setting_id: $setting_id})
    WHERE head.level_max >= $level_from AND head.level_min <= $level_to
      AND EXISTS { (head)-[:NEXT]->() }
      AND NOT EXISTS {
          MATCH (prev:Adventure)-[:NEXT]->(head)
          WHERE prev.setting_id = $setting_id AND prev.level_max >= $level_from AND prev.level_min <= $level_to
      }
    MATCH p = (head)-[:NEXT*0..20]->(:Adventure)
""" + _CHAIN_CONDITIONS


class GraphAdapter:
    """Реалізує порт `GraphRepository`.

    Граф тримає лише ідентифікатори, назви та ребра: описи й тексти
    лишаються в документному сховищі, інакше обходи сповільнюються.
    """

    SIMILAR_FEATURES = 12
    SIMILAR_PER_FEATURE = 200

    def __init__(self, driver: Any, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    async def ensure_schema(self) -> None:
        """Обмеження унікальності й індекси; повторний виклик нічого не змінює."""
        for statement in SCHEMA:
            await self._driver.execute_query(statement, database_=self._database)

    async def similar_adventures(self, adventure_id: str, limit: int = 10) -> list[str]:
        """Обхід через спільні теги та монстрів із ранжуванням."""
        records = await self._read(SIMILAR_QUERY, id=adventure_id, limit=limit,
                                   features=self.SIMILAR_FEATURES, per_feature=self.SIMILAR_PER_FEATURE)
        return [record["id"] for record in records]

    async def adventures_with_monster(self, monster_id: str, limit: int = 20) -> list[tuple[str, str]]:
        records = await self._read(
            "MATCH (:Monster {monster_id: $id})<-[:FEATURES]-(a:Adventure) "
            "RETURN a.adventure_id AS id, coalesce(a.title, a.adventure_id) AS title LIMIT $limit",
            id=monster_id, limit=limit,
        )
        return [(record["id"], record["title"]) for record in records]

    async def co_authors(self, author_id: str) -> list[str]:
        records = await self._read(
            "MATCH (:Author {author_id: $id})-[:CO_AUTHOR]-(o:Author) RETURN DISTINCT o.author_id AS id ORDER BY id",
            id=author_id,
        )
        return [record["id"] for record in records]

    async def seen_together(self, monster_id: str, limit: int = 10) -> list[tuple[str, int]]:
        """Обхід на дві ланки через вузли зустрічей, ранжування за
        кількістю спільних боїв."""
        records = await self._read(
            "MATCH (m:Monster {monster_id: $id})<-[:INVOLVES]-(e:Encounter)-[:INVOLVES]->(o:Monster) "
            "WHERE o <> m "
            "RETURN o.monster_id AS id, count(DISTINCT e) AS together ORDER BY together DESC, id LIMIT $limit",
            id=monster_id, limit=limit,
        )
        return [(record["id"], record["together"]) for record in records]

    async def campaign_chain(
        self, setting_id: str, level_from: int, level_to: int, through: str | None = None
    ) -> list[str]:
        """Найдорожчий запит системи: шаблон шляху змінної довжини за ребрами
        NEXT з умовами на вузли [12]. Обирається ланцюжок, що найповніше
        покриває діапазон рівнів, а з рівних — найкоротший."""
        query = CHAIN_THROUGH_QUERY if through else CHAIN_IN_SETTING_QUERY
        records = await self._read(query, setting_id=setting_id, level_from=level_from, level_to=level_to,
                                   through=through)
        return list(records[0]["chain"]) if records else []

    async def merge_nodes_and_edges(self, batch: Sequence[dict]) -> int:
        """Ідемпотентне оновлення: повторний запис пакета не дублює ребер.
        Повертає кількість ребер, оновлених пакетом."""
        groups: dict[str, list[dict]] = defaultdict(list)
        for document in batch:
            groups[document["kind"]].append(document)
        edges = 0
        for kind, rows in groups.items():
            started = time.perf_counter()
            await self._driver.execute_query(MERGE_QUERIES[kind], rows=rows, database_=self._database)
            log.debug("merge %s %d док. — %.0f мс", kind, len(rows), (time.perf_counter() - started) * 1000)
            edges += sum(self._edges_of(kind, row) for row in rows)
        return edges

    @staticmethod
    def _edges_of(kind: str, row: dict) -> int:
        if kind == "adventure":
            authors = len(row.get("author_ids") or [])
            return (authors + len(row.get("tags") or []) + len(row.get("monster_ids") or [])
                    + sum(1 for k in ("setting_id", "prev_adventure_id", "next_adventure_id") if row.get(k))
                    + authors * (authors - 1) // 2)
        if kind == "setting":
            return len(row.get("related_setting_ids") or [])
        return 1 if kind == "participation" else 0

    async def _read(self, query: str, **parameters: Any) -> list[Any]:
        from neo4j import RoutingControl

        started = time.perf_counter()
        result = await self._driver.execute_query(
            query, parameters_=parameters, routing_=RoutingControl.READ, database_=self._database
        )
        log.debug("%s — %.0f мс", " ".join(query.split())[:60], (time.perf_counter() - started) * 1000)
        return list(result.records)
