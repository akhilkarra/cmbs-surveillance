#!/usr/bin/env python3
"""PreToolUse hook: deny the agent any write to accepted feature files.

Edit/Write/NotebookEdit calls on a path under features/accepted/ are denied. Bash commands
that name features/accepted or the accept tool are denied too, since a shell command could
move, rewrite or delete those files. Reading them with the Read tool is still allowed.
"""

import json
import sys

PROTECTED = ("features/accepted", "accept_feature.py")
REASON = (
    "Accepted feature files are the agreed requirements and only the human changes them. "
    "Propose a change as a new file in features/proposed/ instead. "
    "Use the Read tool to read accepted files, and run tests on the whole features/ folder."
)


def decide(event: dict) -> str | None:
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input", {})
    if tool == "Bash":
        text = tool_input.get("command", "")
    else:
        text = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
    text = text.replace("\\", "/")
    return REASON if any(marker in text for marker in PROTECTED) else None


def main() -> int:
    reason = decide(json.load(sys.stdin))
    if reason:
        decision = {"permissionDecision": "deny", "permissionDecisionReason": reason}
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **decision}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
