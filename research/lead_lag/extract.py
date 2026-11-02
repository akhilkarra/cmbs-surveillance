"""Study driver: extract stored comments with Jev (the model in force, settings version 9), one
partition of the (deal, month) list per process. Stops itself once the study database holds
CAP Jev calls, so total spend stays under the approved budget (about $0.0001 a comment)."""

import json
import sys
import time
import zlib
from pathlib import Path

from cmbs import envfile

envfile.load(Path("/Users/akhilkarra/ode/cmbs-surveillance/.env"))
from sqlalchemy import text  # noqa: E402

from cmbs.db import engine  # noqa: E402
from cmbs.domain import Period  # noqa: E402
from cmbs.extraction.service import extract_comments  # noqa: E402
from cmbs.model.client import ModelClient  # noqa: E402
from cmbs.model.providers import provider_for  # noqa: E402
from cmbs.model.recordings import DbRecordings  # noqa: E402
from cmbs.settings.service import in_force  # noqa: E402

LOG, PART, PARTS, IDLE_MIN = (
    Path(sys.argv[1]),
    int(sys.argv[2]),
    int(sys.argv[3]),
    float(sys.argv[4]),
)
CAP = 25000
with engine.session() as db:
    model = in_force(db).values.model
assert model == "jev-1.14.0", model
provider = provider_for(model)
done, last_new = set(), time.time()
while time.time() - last_new < IDLE_MIN * 60:
    with engine.session() as db:
        calls = db.execute(
            text("select count(*) from app.model_call where model = 'jev-1.14.0'")
        ).scalar()
        todo = db.execute(
            text(
                "select distinct d.name, c.period from silver.report_comment c"
                " join silver.deal d on d.id = c.deal_id order by c.period, d.name"
            )
        ).all()
    if calls >= CAP:
        print(f"cap reached: {calls} calls", flush=True)
        break
    todo = [
        (n, p) for n, p in todo if (n, p) not in done and zlib.crc32(n.encode()) % PARTS == PART
    ]
    if not todo:
        time.sleep(60)
        continue
    last_new = time.time()
    for name, period in todo:
        t0 = time.time()
        try:
            with engine.session() as db:
                client = ModelClient(DbRecordings(db), provider=provider, replay_only=False)
                summary = extract_comments(
                    db,
                    user="quinn",
                    deal=name,
                    period=Period(period.year, period.month),
                    client=client,
                )
            row = {
                "deal": name,
                "period": str(period),
                "s": round(time.time() - t0, 1),
                "n": len(summary.comments),
            }
        except Exception as e:
            row = {"deal": name, "period": str(period), "error": f"{type(e).__name__}: {e}"[:300]}
        done.add((name, period))
        with LOG.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
