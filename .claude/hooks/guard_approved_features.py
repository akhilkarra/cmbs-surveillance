#!/usr/bin/env python3
"""PreToolUse hook: the agent may write drafts but never approve or change approved features.

Denied:
- Edit/Write/NotebookEdit on a feature file whose Feature line is tagged @approved.
- Any write that puts the @approved tag into a feature file.
- Edits to features/APPROVED.sha256.
- Shell commands that name the approval tool, the manifest, the @approved tag, or an
  approved feature file. Commands that reach files by wildcard are caught by the CI hash check.
Self-contained on purpose: it lives in .claude/, which the agent cannot change without a prompt.
"""

import json
import re
import sys
from pathlib import Path

APPROVED = "@approved"
REASON = (
    "Approved feature files are the agreed requirements; only the human reviewer approves or "
    "changes them. Propose a change as a @draft file named <name>.revision.feature next to "
    "the original. Read approved files with the Read tool."
)


def _is_approved(path: Path) -> bool:
    if not path.is_file():
        return False
    for line in path.read_text().splitlines():
        if line.strip().startswith("Feature:"):
            return False
        if APPROVED in line.split("#", 1)[0].split():
            return True
    return False


def decide(event: dict) -> str | None:
    cwd = Path(event.get("cwd", "."))
    tool_input = event.get("tool_input", {})
    if event.get("tool_name") == "Bash":
        command = tool_input.get("command", "")
        if any(marker in command for marker in ("approve_feature", "APPROVED.sha256", APPROVED)):
            return REASON
        for name in re.findall(r"[\w./-]+\.feature\b", command):
            if _is_approved(cwd / name):
                return REASON
        return None
    target = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
    if not target:
        return None
    path = cwd / target
    if path.name == "APPROVED.sha256":
        return REASON
    if path.suffix == ".feature":
        new_text = tool_input.get("content", "") + tool_input.get("new_string", "")
        if _is_approved(path) or APPROVED in new_text:
            return REASON
    return None


def main() -> int:
    reason = decide(json.load(sys.stdin))
    if reason:
        decision = {"permissionDecision": "deny", "permissionDecisionReason": reason}
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **decision}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
