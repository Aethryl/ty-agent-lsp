#!/usr/bin/env python3
"""PreToolUse guard that steers Python reading toward the language server.

  * Bash `cat`/`sed`/`head`/`tail`/`less`/`bat` on a .py file is denied: use Read or LSP.
  * Read of a .py file over READ_LIMIT lines without offset/limit is denied: outline first.
  * grep/rg (Bash or the Grep tool) over Python gets a reminder to use the LSP for symbols.

Subagents have no LSP tool, so their calls pass through untouched.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

READ_LIMIT = 300
VIEWERS = {"cat", "sed", "head", "tail", "less", "more", "bat", "nl"}
SEARCHERS = {"grep", "rg", "egrep"}
PYTHON_FILE = re.compile(r"\.pyi?$")
PYTHON_FILTER = re.compile(
    r"(--type[= ]py\b|-t ?py\b|--include=\S*\.py|\.pyi?\b|-g ?\S*\.py)"
)
SEPARATORS = re.compile(r"&&|\|\||[;|\n]")

DENY_BASH = (
    "Don't read Python through {tool}. Use the LSP tool (documentSymbol to outline the file, "
    "hover/goToDefinition for one symbol), then Read with offset/limit for the lines you need."
)
DENY_READ = (
    "{path} has {lines} lines. Outline it first with LSP documentSymbol, then Read the symbol's "
    "range with offset/limit. Pass an explicit offset/limit if you really need the whole file."
)
NUDGE_SEARCH = (
    "Searching Python: if the pattern is a symbol name, use LSP findReferences or "
    "workspaceSymbol instead. Grep is for strings, comments, dynamic access and non-Python files."
)


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def nudge(context: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": context,
                }
            }
        )
    )


def words(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def check_bash(command: str) -> None:
    if "<<" in command:
        return
    for segment in SEPARATORS.split(command):
        argv = words(segment)
        if not argv:
            continue
        tool = Path(argv[0]).name
        if tool == "rtk" and len(argv) > 1:
            tool, argv = argv[1], argv[1:]
        viewing = tool in VIEWERS and ">" not in segment and "-i" not in argv[1:]
        if viewing and any(PYTHON_FILE.search(arg) for arg in argv[1:]):
            deny(DENY_BASH.format(tool=tool))
            return
        if tool in SEARCHERS and PYTHON_FILTER.search(segment):
            nudge(NUDGE_SEARCH)
            return


def check_read(tool_input: dict) -> None:
    path = tool_input.get("file_path", "")
    if (
        not PYTHON_FILE.search(path)
        or tool_input.get("offset")
        or tool_input.get("limit")
    ):
        return
    try:
        with open(path, "rb") as handle:
            lines = sum(1 for _ in handle)
    except OSError:
        return
    if lines > READ_LIMIT:
        deny(DENY_READ.format(path=Path(path).name, lines=lines))


def check_grep(tool_input: dict) -> None:
    scope = f"{tool_input.get('glob', '')} {tool_input.get('type', '')} {tool_input.get('path', '')}"
    if re.search(r"\bpy\b|\.pyi?\b", scope):
        nudge(NUDGE_SEARCH)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if event.get("agent_id"):
        return
    tool, tool_input = event.get("tool_name"), event.get("tool_input") or {}
    if tool == "Bash":
        check_bash(tool_input.get("command", ""))
    elif tool == "Read":
        check_read(tool_input)
    elif tool == "Grep":
        check_grep(tool_input)


if __name__ == "__main__":
    main()
