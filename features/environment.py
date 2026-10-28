"""behave hooks: a fresh database per scenario, and a Chromium page for @surface:ui scenarios.

Database: a template database is migrated once per process; every scenario runs in its own
clone, seeded with the fixture world (six users, settings version 7). Before each When step the
data layers are fingerprinted, so a Then step can check that nothing stored changed.

The browser starts on the first UI scenario, so runs without one never launch it. Each scenario
gets a fresh browser context, and a failing one leaves a Playwright trace in output/traces/
(open it with `uv run playwright show-trace <file>`).

Environment variables:
  CMBS_WEB_URL  base URL of the dashboard (default: the Vite dev server)
  HEADED=1      show the browser instead of running headless
"""

import itertools
import os
import re
from pathlib import Path
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

from cmbs import clock
from cmbs.db import engine
from cmbs.testing import databases, world
from cmbs.testing.bdd import data_snapshot

UI_TAG = "surface:ui"
TRACE_DIR = Path("output/traces")


def before_all(context):
    # behave drops attributes a scenario hook sets on `context` when the scenario ends, so the
    # browser and the database template live on objects created here, shared by every scenario.
    context.ui = SimpleNamespace(playwright=None, browser=None)
    # behavex can run before_all again in a worker process that already ran scenarios, when
    # DATABASE_URL still names the previous scenario's (dropped) clone; the server address saved
    # the first time is the one to use.
    server = os.environ.get("CMBS_TEST_SERVER_URL") or os.environ.get("DATABASE_URL")
    if not server:
        raise SystemExit("Feature scenarios need Postgres: set DATABASE_URL (see CLAUDE.md).")
    os.environ["CMBS_TEST_SERVER_URL"] = server
    template = f"cmbs_bdd_template_{os.getpid()}"
    databases.create_template(template)
    context.dbs = SimpleNamespace(template=template, counter=itertools.count(1), current=None)


def before_scenario(context, scenario):
    clock.set_now(None)
    name = f"cmbs_bdd_{os.getpid()}_{next(context.dbs.counter)}"
    os.environ["DATABASE_URL"] = databases.clone(context.dbs.template, name)
    context.dbs.current = name
    with engine.session() as db:
        world.seed(db)
    context.snapshot = None
    context.refusal = None
    if UI_TAG not in scenario.effective_tags:
        return
    ui = context.ui
    if ui.browser is None:
        ui.playwright = sync_playwright().start()
        ui.browser = ui.playwright.chromium.launch(headless=os.environ.get("HEADED") != "1")
    context.browser_context = ui.browser.new_context(
        base_url=os.environ.get("CMBS_WEB_URL", "http://localhost:5173")
    )
    context.browser_context.tracing.start(screenshots=True, snapshots=True)
    context.page = context.browser_context.new_page()


def before_step(context, step):
    if step.step_type == "when":
        context.snapshot = data_snapshot()


def after_scenario(context, scenario):
    engine.dispose(os.environ["DATABASE_URL"])
    databases.drop(context.dbs.current)
    clock.set_now(None)
    if UI_TAG not in scenario.effective_tags:
        return
    if scenario.status.name in ("failed", "error"):
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "-", scenario.name).strip("-").lower()
        context.browser_context.tracing.stop(path=TRACE_DIR / f"{name}.zip")
    else:
        context.browser_context.tracing.stop()
    context.browser_context.close()


def after_all(context):
    engine.dispose()
    databases.drop(context.dbs.template)
    ui = context.ui
    if ui.browser is not None:
        ui.browser.close()
        ui.playwright.stop()
