#!/usr/bin/env python3
"""Locate a *usable* Python environment for a project, and run ty inside it.

Finding an environment is not enough; it has to contain the project's dependencies.
Poetry keys its virtualenvs to the project path, so a git worktree gets a different key
than its main checkout and one of two things happens:

  * Poetry has no environment for that path, so ty falls back to system site-packages; or
  * Poetry creates an empty one, and a shell hook activates it, so `VIRTUAL_ENV` is set
    and useless.

Either way ty resolves no third-party imports and reports every one of them as an error,
on one real codebase, 1,724 of them. Under an agent those land in its context after each
edit, which is worse than useless: it is told working imports are broken. So each candidate
environment is validated against the project's declared dependencies before it is accepted,
and a worktree falls back to its main checkout's environment.

Used two ways:
  * as the LSP command: `python3 ty_resolve.py server` execs ty with the environment set
  * as a library: `resolve(root)` from the rename server, so both agree on binary and env
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

BIN_DIRS = ("bin", "Scripts")
DEPENDENCY_TABLES = (
    ("tool", "poetry", "dependencies"),
    ("project", "dependencies"),
    ("project", "optional-dependencies"),
)
BOOTSTRAP = {
    "pip",
    "setuptools",
    "wheel",
    "pkg_resources",
    "_distutils_hack",
    "__pycache__",
}


class Environment(NamedTuple):
    """A resolved ty invocation: which binary, and which environment it should see."""

    binary: str
    prefix: Path | None
    source: str


_cache: dict[Path, Environment] = {}


def log(message: str) -> None:
    print(f"[ty-resolve] {message}", file=sys.stderr, flush=True)


def bin_dir(prefix: Path) -> Path:
    for name in BIN_DIRS:
        if (prefix / name).is_dir():
            return prefix / name
    return prefix / "bin"


def binary_in(prefix: Path) -> str | None:
    for directory in BIN_DIRS:
        for name in ("ty", "ty.exe"):
            candidate = prefix / directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def site_packages(prefix: Path) -> Path | None:
    for pattern in (
        "lib/python*/site-packages",
        "Lib/site-packages",
        "lib/site-packages",
    ):
        for path in sorted(prefix.glob(pattern)):
            if path.is_dir():
                return path
    return None


def read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def dependency_tables(root: Path) -> dict[str, object]:
    """The dependency-bearing tables only.

    Comparing whole pyproject files would flag a `[tool.ruff]` tweak on a branch as a
    dependency change, so only these tables are considered.
    """
    data = read_toml(root / "pyproject.toml")
    tables: dict[str, object] = {}
    for keys in DEPENDENCY_TABLES:
        cursor: object = data
        for key in keys:
            cursor = cursor.get(key) if isinstance(cursor, dict) else None
            if cursor is None:
                break
        if cursor is not None:
            tables[".".join(keys)] = cursor
    groups = data.get("tool", {}).get("poetry", {}).get("group", {})
    if isinstance(groups, dict):
        for name, body in groups.items():
            if isinstance(body, dict) and "dependencies" in body:
                tables[f"group.{name}"] = body["dependencies"]
    return tables


def declared_dependencies(root: Path) -> set[str]:
    """Top-level distribution names the project declares, lowercased with - and _ folded."""
    names: set[str] = set()
    for value in dependency_tables(root).values():
        if isinstance(value, dict):
            names.update(str(key) for key in value)
        elif isinstance(value, list):
            for item in value:
                text = str(item)
                for separator in "[<>=!~; ":
                    text = text.split(separator)[0]
                if text:
                    names.add(text)
    names.discard("python")
    return {name.lower().replace("-", "_") for name in names if name}


def is_provisioned(prefix: Path, wanted: set[str]) -> bool:
    """True when this environment actually has the project's dependencies installed.

    An environment holding only pip/setuptools passes a naive existence check but resolves
    nothing, which is exactly the trap a path-keyed Poetry venv sets in a worktree.
    """
    packages = site_packages(prefix)
    if packages is None:
        return False
    try:
        entries = list(packages.iterdir())
    except OSError:
        return False

    installed: set[str] = set()
    for entry in entries:
        name = entry.name
        if name in BOOTSTRAP:
            continue
        if name.endswith((".dist-info", ".egg-info")):
            name = name.split("-")[0]
        installed.add(name.removesuffix(".py").lower().replace("-", "_"))

    if wanted:
        return bool(wanted & installed)
    return len(installed - BOOTSTRAP) >= 10


def main_checkout(root: Path) -> Path | None:
    """The main checkout behind a git worktree, or None when root is not a worktree."""
    if not shutil.which("git"):
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (root / common).resolve()
    parent = common.parent.resolve()
    return parent if parent != root.resolve() else None


def poetry_env(root: Path) -> Path | None:
    """Ask Poetry for its environment; it keeps them outside the project."""
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
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return Path(path) if path and Path(path).is_dir() else None


def project_root() -> Path:
    for value in (os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if value:
            candidate = Path(value).expanduser()
            if candidate.is_dir():
                return candidate.resolve()
    return Path.cwd()


def resolve(root: Path | None = None) -> Environment:
    """Pick the ty binary and the environment it should resolve imports against."""
    root = (root or project_root()).resolve()
    if root in _cache:
        return _cache[root]

    wanted = declared_dependencies(root)
    main = main_checkout(root)

    candidates: list[tuple[str, Path | None]] = []
    active = os.environ.get("VIRTUAL_ENV")
    if active:
        candidates.append(("$VIRTUAL_ENV", Path(active)))
    candidates.append((".venv", root / ".venv"))
    candidates.append(("venv", root / "venv"))
    candidates.append(("poetry", poetry_env(root)))
    if main is not None:
        candidates.append(("worktree-main .venv", main / ".venv"))
        candidates.append(("worktree-main poetry", poetry_env(main)))

    chosen: Environment | None = None
    for source, prefix in candidates:
        if prefix is None or not prefix.is_dir():
            continue
        if not is_provisioned(prefix, wanted):
            log(
                f"skipping {source} ({prefix}): project dependencies not installed there"
            )
            continue
        binary = binary_in(prefix) or shutil.which("ty")
        if binary is None:
            log(f"skipping {source} ({prefix}): no ty binary in it or on PATH")
            continue
        chosen = Environment(binary, prefix, source)
        break

    if chosen is None:
        fallback = shutil.which("ty")
        if fallback is None:
            raise FileNotFoundError(
                "no usable Python environment found and `ty` is not on PATH. Install ty "
                "with `uv tool install ty` or add it to the project's dev dependencies."
            )
        log(
            "no environment with the project's dependencies found; using ty from PATH "
            f"({fallback}) with no environment; third-party imports will not resolve"
        )
        chosen = Environment(fallback, None, "PATH")

    if main is not None and chosen.source.startswith("worktree-main"):
        if dependency_tables(root) != dependency_tables(main):
            log(
                "WARNING: this worktree's dependency tables differ from its main checkout, "
                "so the shared environment may be wrong for it. Give it its own: "
                "`rm .venv && poetry install` in the worktree."
            )

    log(
        f"using ty from {chosen.source}: {chosen.binary}"
        + (f" (env: {chosen.prefix})" if chosen.prefix else "")
    )
    _cache[root] = chosen
    return chosen


def environ_for(env: Environment, base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment variables that make ty resolve imports against the chosen prefix.

    ty's discovery order is VIRTUAL_ENV, then a `.venv` in the project root, then explicit
    configuration. `ty server` takes no --python flag, so for the language server this is
    the only lever.
    """
    result = dict(os.environ if base is None else base)
    if env.prefix is None:
        return result
    result["VIRTUAL_ENV"] = str(env.prefix)
    result["PATH"] = os.pathsep.join(
        [str(bin_dir(env.prefix)), result.get("PATH", "")]
    ).rstrip(os.pathsep)
    result.pop("PYTHONHOME", None)
    return result


def find_ty(root: Path | None = None) -> str:
    """Backwards-compatible accessor for just the binary path."""
    return resolve(root).binary


def main() -> int:
    try:
        env = resolve()
    except FileNotFoundError as exc:
        log(str(exc))
        return 127
    os.environ.update(environ_for(env))
    try:
        os.execv(env.binary, [env.binary, *sys.argv[1:]])
    except OSError as exc:
        log(f"could not exec {env.binary}: {exc}")
        return 126
    return 0


if __name__ == "__main__":
    sys.exit(main())
