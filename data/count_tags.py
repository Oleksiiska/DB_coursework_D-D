import glob, gzip, json, zlib
from datetime import datetime, timezone
from app.infrastructure.sources.crawler_source import parse_card
path = sorted(glob.glob("/data/raw/itch/crawl-*-full.jsonl.gz.part"))[-1]
tags, cards = set(), 0
try:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            t = p.get("text") or ""
            if p["status"] == 200 and 'name="itch:path"' in t:
                cards += 1
                tags.update(parse_card(t, p["url"], datetime.now(timezone.utc)).tags)
except (EOFError, OSError, ValueError, zlib.error):
    pass
print(f"карток: {cards}, різних тегів усього: {len(tags)}")
