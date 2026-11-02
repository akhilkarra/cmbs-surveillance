"""Study driver: backfill each deal over its whole ABS-EE range in one run (one EDGAR discovery per
run), with EDGAR fetches retried through 503/429 throttling. A month that cannot load stops a run
(the routine loads months in order), so the driver starts a new run from the month after it.
N deals in parallel. Outcomes land in app.run / app.run_month."""

import json
import multiprocessing as mp
import sys
import time
import traceback
import urllib.error
from pathlib import Path

ROOT = Path("/Users/akhilkarra/ode/cmbs-surveillance")
LOG = Path(sys.argv[2])
USER = "quinn"
LAST = (2026, 10)
FIRST = (2019, 1)


class Retrying:
    """Wraps an EDGAR source or archive: retries throttled requests with growing waits."""

    def __init__(self, inner):
        self.inner = inner

    def _call(self, name, url):
        wait = 5
        for attempt in range(8):
            try:
                return getattr(self.inner, name)(url)
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == 7:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 7:
                    raise
            time.sleep(wait)
            wait = min(wait * 2, 120)

    def get_bytes(self, url):
        return self._call("get_bytes", url)

    def get_json(self, url):
        return self._call("get_json", url)


def work(name: str) -> str:
    import datetime as dt
    from dataclasses import replace

    from cmbs import envfile

    envfile.load(ROOT / ".env")
    from sqlalchemy import text

    from cmbs.db import engine
    from cmbs.domain import Period
    from cmbs.edgar.replay import default_source
    from cmbs.edgar.service import discover_deal
    from cmbs.refresh import service
    from cmbs.refresh.sources import default_sources

    sources = default_sources()
    sources = replace(sources, edgar=Retrying(sources.edgar), archive=Retrying(sources.archive))
    out = {"deal": name, "runs": 0, "errors": []}
    t0 = time.time()
    try:
        found = discover_deal(Retrying(default_source()), name, dt.date(2026, 11, 1), user=USER)
        periods = sorted(
            {(f.period.year, f.period.month) for f in found.of_form("ABS-EE") if f.period}
        )
        periods = [p for p in periods if FIRST <= p <= LAST]
        if not periods:
            out["errors"].append("no ABS-EE months in range")
        start = periods[0] if periods else None
        while start is not None and out["runs"] < 40:
            out["runs"] += 1
            service.backfill(USER, found.deal, Period(*start), Period(*LAST), sources=sources)
            with engine.session() as db:
                row = db.execute(
                    text(
                        "select m.period from app.run_month m join silver.deal d on d.id = m.deal_id"
                        " where d.name = :n and m.outcome <> 'loaded' and m.period >= :s"
                        " and m.run_id = (select max(run_id) from app.run_month m2 where m2.deal_id = m.deal_id)"
                        " order by m.period desc limit 1"
                    ),
                    {"n": found.deal, "s": dt.date(*start, 1)},
                ).first()
            if row is None:
                break
            p = row[0]
            start = (p.year + (p.month == 12), p.month % 12 + 1)
            if start > LAST:
                break
        with engine.session() as db:
            out["loaded"] = db.execute(
                text(
                    "select count(distinct period) from silver.loan_period lp join silver.deal d"
                    " on d.id = lp.deal_id where d.name = :n"
                ),
                {"n": found.deal},
            ).scalar()
    except Exception as e:
        out["errors"].append("".join(traceback.format_exception_only(e))[:300])
    out["seconds"] = round(time.time() - t0)
    with LOG.open("a") as fh:
        fh.write(json.dumps(out) + "\n")
    return name


if __name__ == "__main__":
    names = [r[1] for r in json.load(open(sys.argv[1]))]
    # Interleave shelves so early results span issuers.
    names.sort(key=lambda n: (hash(n) % 7, n))
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    with mp.get_context("spawn").Pool(workers) as pool:
        for i, n in enumerate(pool.imap_unordered(work, names), 1):
            print(f"{i}/{len(names)} {n}", flush=True)
