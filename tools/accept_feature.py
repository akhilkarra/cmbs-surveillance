"""Accept reviewed feature files. Run by the human reviewer only; the agent is blocked from it.

Usage:
  uv run python tools/accept_feature.py features/proposed/<name>.feature [...]

Moves each file to features/accepted/, keeping its subfolder, refreshes the manifest, and
stages both. Commit with an Approved-by trailer, which CI requires:
  git commit -m "Accept <name>" --trailer "Approved-by: <your name>"
"""

import subprocess
import sys
from pathlib import Path

from accepted_features import ACCEPTED_DIR, MANIFEST, PROPOSED_DIR, current_hashes, write_manifest


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    for name in paths:
        source = Path(name)
        if not source.is_file() or PROPOSED_DIR not in source.parents:
            print(f"{name}: not a file under {PROPOSED_DIR}/")
            return 1
        target = ACCEPTED_DIR / source.relative_to(PROPOSED_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "mv", str(source), str(target)], check=True)
        print(f"accepted {target}")
    write_manifest(current_hashes())
    subprocess.run(["git", "add", str(MANIFEST)], check=True)
    print('Now commit with: git commit -m "Accept ..." --trailer "Approved-by: <your name>"')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
