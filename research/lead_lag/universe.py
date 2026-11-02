"""Find 10-D filers whose distribution report carries a "Special Servicing Comments" column."""

import json
import sys
import time
import urllib.parse
from pathlib import Path

from cmbs import envfile

envfile.load(Path.cwd() / ".env")
from cmbs.edgar import client
from cmbs.edgar.search import EFTS_URL


def page(q, start, dfrom, dto):
    p = {"q": q, "forms": "10-D", "dateRange": "custom", "startdt": dfrom, "enddt": dto}
    if start:
        p["from"] = str(start)
    return client.get_json(f"{EFTS_URL}?{urllib.parse.urlencode(p)}")


filers = {}
q = '"Special Servicing Comments"'
for dfrom, dto in [("2026-09-01", "2026-10-31")]:
    start = 0
    while True:
        r = page(q, start, dfrom, dto)
        hits = r["hits"]["hits"]
        total = r["hits"]["total"]["value"]
        for h in hits:
            s = h["_source"]
            for cik, name in zip(s.get("ciks", []), s.get("display_names", [])):
                filers.setdefault(cik, {"name": name, "n": 0})["n"] += 1
        start += len(hits)
        time.sleep(0.25)
        if not hits or start >= total or start >= 1000:
            break
    print(dfrom, dto, "total hits", total, file=sys.stderr)
out = Path(sys.argv[1])
out.write_text(json.dumps(filers, indent=1))
print(len(filers), "filers")
for cik, v in sorted(filers.items(), key=lambda kv: kv[1]["name"]):
    print(cik, v["n"], v["name"])
