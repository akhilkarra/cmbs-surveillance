"""behave hooks: a Chromium page for every scenario tagged @surface:ui.

The browser starts on the first UI scenario, so runs without one never launch it. Each scenario
gets a fresh browser context, and a failing one leaves a Playwright trace in output/traces/
(open it with `uv run playwright show-trace <file>`).

Environment variables:
  CMBS_WEB_URL  base URL of the dashboard (default: the Vite dev server)
  HEADED=1      show the browser instead of running headless
"""

import os
import re
from pathlib import Path
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

UI_TAG = "surface:ui"
TRACE_DIR = Path("output/traces")


def before_all(context):
    # behave drops attributes a scenario hook sets on `context` when the scenario ends, so the
    # browser lives on an object created here, which every scenario shares.
    context.ui = SimpleNamespace(playwright=None, browser=None)


def before_scenario(context, scenario):
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


def after_scenario(context, scenario):
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
    ui = context.ui
    if ui.browser is not None:
        ui.browser.close()
        ui.playwright.stop()
