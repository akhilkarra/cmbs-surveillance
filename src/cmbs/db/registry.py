"""Find every feature module's tables and API routes by naming convention.

A feature package `cmbs.<feature>` may contain `tables.py` (SQLAlchemy models), `routes.py`
(a FastAPI `router`) and `commands.py` (a Typer `app` plus a `NAME` for its command group).
Discovery means parallel features never edit a shared list.
"""

import importlib
import pkgutil
from types import ModuleType

import cmbs


def _feature_modules(leaf: str) -> list[ModuleType]:
    found = []
    for info in pkgutil.walk_packages(cmbs.__path__, prefix="cmbs."):
        if info.name.rsplit(".", 1)[-1] == leaf and ".migrations" not in info.name:
            found.append(importlib.import_module(info.name))
    return sorted(found, key=lambda module: module.__name__)


def load_all_tables() -> list[ModuleType]:
    return _feature_modules("tables")


def all_route_modules() -> list[ModuleType]:
    return [module for module in _feature_modules("routes") if hasattr(module, "router")]


def all_command_modules() -> list[ModuleType]:
    return [
        module
        for module in _feature_modules("commands")
        if hasattr(module, "app") and hasattr(module, "NAME")
    ]
