"""Seed an empty local database with the fixture world, so the `cmbs` command can be tried.

A fresh database has no users, and granting roles needs an operator, so nothing can run yet.
This adds the six fixture users of features/README.md (pat, alex, quinn, rae, olly, morgan) and
settings version 7 (cmbs.testing.world.seed), and refuses a database that already has users.
For local development and demos only; it reads DATABASE_URL like the `cmbs` command (and .env).

  uv run alembic upgrade head
  uv run python tools/seed_demo.py
"""

import sys
from pathlib import Path

from sqlalchemy import func, select

from cmbs import envfile
from cmbs.access.tables import User
from cmbs.db import engine
from cmbs.testing import world


def main() -> int:
    envfile.load(Path.cwd() / ".env")
    with engine.session() as db:
        users = db.scalar(select(func.count()).select_from(User)) or 0
        if users:
            print(f"the database already has {users} users; nothing seeded")
            return 1
        world.seed(db)
        db.commit()
    names = ", ".join(world.USERS)
    print(f"seeded users {names} and settings version {world.BASELINE_SETTINGS_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
