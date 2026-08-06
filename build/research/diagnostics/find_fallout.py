import json
from pathlib import Path

path = Path(r"data\ja_wiki.jsonl")

with path.open("r", encoding="utf-8") as file:
    for line in file:
        obj = json.loads(line)
        meta = obj.get("meta") or {}
        title = str(meta.get("title") or "")

        if "fallout" in title:
            print(repr(title), meta.get("url"))