#!/usr/bin/env python3
"""Locate the ty binary a project actually uses, and exec it.

A global `ty` on PATH drifts from the version a project pins, and then the agent sees
diagnostics that disagree with the project's own hooks and CI, which is the main reason
to run ty rather than another checker. So prefer the project's environment and fall back
to PATH only when there is nothing better.

Used two ways:
  * as the LSP command, `python3 ty_resolve.py server` execs `<resolved ty> server`
  * as a library, `find_ty(root)` from the rename server, so both agree on the binary
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BIN_DIRS = ("bin", "Scripts")
_cache: dict[Path, str] = {}


def log(message: str) -> None:
    print(f"[ty-resolve] {message}", file=sys.stderr, flush=True)


def _candidate(prefix: Path) -> str | None:
    for directory in BIN_DIRS:
        for name in ("ty", "ty.exe"):
            path = prefix / directory / name
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    return None


def _poetry_env(root: Path) -> str | None:
    """Poetry keeps virtualenvs outside the project, so it has to be asked."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        if "[tool.poetry" not in pyproject.read_text(encoding="utf-8", errors="ignore"):
            return None
    except OSError:
        return None
    if not shutil.which("poetry"):
        return None
    try:
        result = subprocess.run(
            ["poetry", "env", "info", "--path"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return _candidate(Path(path)) if path else None


def project_root() -> Path:
    for value in (os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if value:
            candidate = Path(value).expanduser()
            if candidate.is_dir():
                return candidate.resolve()
    return Path.cwd()


def find_ty(root: Path | None = None) -> str:
    """Return the best ty for this project, preferring its own environment."""
    root = (root or project_root()).resolve()
    if root in _cache:
        return _cache[root]

    active = os.environ.get("VIRTUAL_ENV")
    sources: list[tuple[str, str | None]] = [
        ("VIRTUAL_ENV", _candidate(Path(active)) if active else None),
        (".venv", _candidate(root / ".venv")),
        ("venv", _candidate(root / "venv")),
        ("poetry", _poetry_env(root)),
        ("PATH", shutil.which("ty")),
    ]

    for label, found in sources:
        if found:
            log(f"using ty from {label}: {found}")
            _cache[root] = found
            return found

    raise FileNotFoundError(
        "`ty` was not found in this project's virtualenv or on PATH. "
        "Install it with `uv tool install ty`, `pip install ty`, or add it to the "
        "project's dev dependencies."
    )


def main() -> int:
    try:
        binary = find_ty()
    except FileNotFoundError as exc:
        log(str(exc))
        return 127
    args = [binary, *sys.argv[1:]]
    try:
        os.execv(binary, args)
    except OSError as exc:
        log(f"could not exec {binary}: {exc}")
        return 126
    return 0


if __name__ == "__main__":
    sys.exit(main())
