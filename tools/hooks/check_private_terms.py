"""Pre-commit guard: block commits that contain terms listed in private/blocked-terms.txt.

The term list lives in the gitignored private/ folder, so the terms themselves are
never published. When the list is absent (for example on CI), the check passes.
"""

import re
import sys
from pathlib import Path

TERMS_FILE = Path("private/blocked-terms.txt")


def load_patterns() -> list[re.Pattern[str]]:
    lines = TERMS_FILE.read_text(encoding="utf-8").splitlines()
    return [
        re.compile(line, re.IGNORECASE)
        for line in lines
        if line.strip() and not line.startswith("#")
    ]


def main(paths: list[str]) -> int:
    if not TERMS_FILE.is_file():
        return 0
    patterns = load_patterns()
    failed = False
    for name in paths:
        try:
            text = Path(name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(p.search(line) for p in patterns):
                print(f"{name}:{lineno}: contains a blocked private term")
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
