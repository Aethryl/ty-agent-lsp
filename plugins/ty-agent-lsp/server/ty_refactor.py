#!/usr/bin/env python3
"""MCP server exposing semantic rename for Python, backed by Astral ty's language server.

Claude Code's built-in LSP tool covers navigation (goToDefinition, findReferences,
documentSymbol, workspaceSymbol, hover, implementation, call hierarchy) but does not
expose textDocument/rename. This server fills exactly that gap and nothing else.

Two transports meet here. Claude Code speaks MCP over stdio, which is newline-delimited
JSON-RPC. ty speaks LSP, which is Content-Length-framed JSON-RPC. Both live below.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

SERVER_NAME = "ty-refactor"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LSP_TIMEOUT = 120.0


def log(message: str) -> None:
    """Diagnostics go to stderr; stdout carries only MCP frames."""
    print(f"[{SERVER_NAME}] {message}", file=sys.stderr, flush=True)


class LspError(Exception):
    """A failure that should be reported to the caller as tool output, not a crash."""


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def utf16_to_index(line: str, utf16_col: int) -> int:
    """Convert an LSP utf-16 column into a Python string index."""
    if utf16_col <= 0:
        return 0
    consumed = 0
    for index, char in enumerate(line):
        if consumed >= utf16_col:
            return index
        consumed += 2 if ord(char) > 0xFFFF else 1
    return len(line)


def index_to_utf16(line: str, index: int) -> int:
    return utf16_len(line[:index])


class LspClient:
    """Minimal LSP client over stdio, sufficient for symbol lookup and rename."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root_uri = root.as_uri()
        self._next_id = 0
        self._opened: set[str] = set()
        try:
            self.proc = subprocess.Popen(
                ["ty", "server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(root),
            )
        except FileNotFoundError:
            raise LspError(
                "`ty` was not found on PATH. Install it with `uv tool install ty`, "
                "`pip install ty`, or add your project's virtualenv to PATH."
            )

    def _send(
        self, method: str, params: dict[str, Any], notify: bool = False
    ) -> int | None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        request_id = None
        if not notify:
            self._next_id += 1
            request_id = self._next_id
            message["id"] = request_id
        body = json.dumps(message).encode("utf-8")
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
            self.proc.stdin.flush()
        except BrokenPipeError:
            raise LspError("the ty language server exited unexpectedly")
        return request_id

    def _read_message(self) -> dict[str, Any] | None:
        assert self.proc.stdout is not None
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.proc.stdout.read(1)
            if not chunk:
                return None
            header += chunk
            if len(header) > 8192:
                return None
        length = 0
        for line in header.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        body = b""
        while len(body) < length:
            chunk = self.proc.stdout.read(length - len(body))
            if not chunk:
                return None
            body += chunk
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a request and return its result, skipping interleaved notifications.

        Reading until the id matches matters: servers emit window/logMessage and
        progress notifications first, and treating the first frame as the answer
        silently yields empty results.
        """
        request_id = self._send(method, params)
        result: list[Any] = []
        error: list[Any] = []

        def pump() -> None:
            while True:
                message = self._read_message()
                if message is None:
                    return
                if message.get("id") == request_id:
                    if "error" in message:
                        error.append(message["error"])
                    else:
                        result.append(message.get("result"))
                    return

        worker = threading.Thread(target=pump, daemon=True)
        worker.start()
        worker.join(timeout=LSP_TIMEOUT)
        if worker.is_alive():
            raise LspError(f"ty did not answer {method} within {LSP_TIMEOUT:.0f}s")
        if error:
            raise LspError(f"ty rejected {method}: {error[0].get('message', error[0])}")
        if not result:
            raise LspError(
                f"the ty language server closed the connection during {method}"
            )
        return result[0]

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send(method, params, notify=True)

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.root_uri,
                "workspaceFolders": [{"uri": self.root_uri, "name": self.root.name}],
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16"]},
                    "workspace": {
                        "symbol": {},
                        "workspaceEdit": {"documentChanges": True},
                    },
                    "textDocument": {
                        "rename": {"prepareSupport": True},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "references": {},
                        "definition": {},
                    },
                },
            },
        )
        self.notify("initialized", {})

    def open_document(self, path: Path) -> str:
        uri = path.as_uri()
        if uri in self._opened:
            return uri
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LspError(f"cannot read {path}: {exc}")
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": 1,
                    "text": text,
                }
            },
        )
        self._opened.add(uri)
        return uri

    def close_documents(self) -> None:
        """Release documents so long sessions don't accumulate open files."""
        for uri in list(self._opened):
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        self._opened.clear()

    def shutdown(self) -> None:
        try:
            self.close_documents()
            self.proc.kill()
        except Exception:
            pass


def uri_to_path(uri: str) -> Path:
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    return Path(unquote(parsed.path))


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def resolve_root(explicit: str | None) -> Path:
    for candidate in (explicit, os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if candidate:
            root = Path(candidate).expanduser().resolve()
            if root.is_dir():
                return root
    raise LspError("could not determine the project root; pass project_root explicitly")


def loose_symbol_names(client: LspClient, symbol: str) -> set[str]:
    """Retry workspace/symbol with shrinking prefixes to recover near-misses.

    An exact query for a misspelled name can come back completely empty, which would
    otherwise leave the caller with no idea what the real name is.
    """
    sizes = {
        len(symbol) - 1,
        len(symbol) - 2,
        len(symbol) * 2 // 3,
        len(symbol) // 2,
        4,
    }
    names: set[str] = set()
    for size in sorted((s for s in sizes if 3 < s < len(symbol)), reverse=True):
        results = client.request("workspace/symbol", {"query": symbol[:size]}) or []
        names.update(entry.get("name", "") for entry in results if entry.get("name"))
    return {name for name in names if name}


def rank_candidates(symbol: str, names: set[str]) -> list[str]:
    """Pick the most plausible intended names.

    ty truncates workspace/symbol results, so a short prefix query drags in unrelated
    fixtures. Restricting to names that share a stem with the query removes that noise
    before fuzzy ranking.
    """
    import difflib

    if not names:
        return []
    stem = symbol[:4].lower()
    same_stem = sorted(name for name in names if name[:4].lower() == stem)
    pool = same_stem or sorted(names)
    close = difflib.get_close_matches(symbol, pool, n=8, cutoff=0.6)
    return close or pool[:8]


def find_symbol(client: LspClient, symbol: str) -> dict[str, Any]:
    """Resolve a bare symbol name to a single definition location.

    workspace/symbol is fuzzy, so exact-match filtering is required. Ambiguity and
    near-misses are reported back rather than guessed at, a wrong guess here would
    rewrite the wrong symbol across the tree.
    """
    matches = client.request("workspace/symbol", {"query": symbol}) or []
    exact = [entry for entry in matches if entry.get("name") == symbol]

    if not exact:
        names = {entry.get("name", "") for entry in matches if entry.get("name")}
        names |= loose_symbol_names(client, symbol)
        shown = rank_candidates(symbol, names)
        hint = f" Did you mean: {', '.join(shown)}?" if shown else ""
        raise LspError(f"no symbol named {symbol!r} found in the project.{hint}")

    if len(exact) > 1:
        lines = []
        for entry in exact[:10]:
            location = entry.get("location", {})
            path = uri_to_path(location.get("uri", ""))
            start = location.get("range", {}).get("start", {})
            lines.append(
                f"  {relative(path, client.root)}:{start.get('line', 0) + 1}"
                f":{start.get('character', 0) + 1}"
            )
        listing = "\n".join(lines)
        raise LspError(
            f"{symbol!r} is ambiguous, {len(exact)} definitions match:\n{listing}\n"
            "Use rename_at with the file, line, and character of the one you mean."
        )

    return exact[0]


def locate_identifier(
    path: Path, line_number: int, symbol: str, fallback_col: int
) -> int:
    """Return a utf-16 column that lands on the identifier itself.

    A symbol's range start is not always on its name (decorators, `class Foo(Base)`
    variations), so prefer the first occurrence of the name on that line.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return fallback_col
    if line_number >= len(lines):
        return fallback_col
    text = lines[line_number]
    index = text.find(symbol)
    if index < 0:
        return fallback_col
    return index_to_utf16(text, index)


def collect_edits(
    workspace_edit: dict[str, Any] | None,
) -> tuple[dict[Path, list[dict]], list[str]]:
    """Flatten a WorkspaceEdit into per-file text edits, noting anything unsupported."""
    if not workspace_edit:
        return {}, []

    edits: dict[Path, list[dict]] = {}
    warnings: list[str] = []

    for uri, file_edits in (workspace_edit.get("changes") or {}).items():
        edits.setdefault(uri_to_path(uri), []).extend(file_edits)

    for change in workspace_edit.get("documentChanges") or []:
        if "textDocument" in change:
            uri = change["textDocument"].get("uri", "")
            edits.setdefault(uri_to_path(uri), []).extend(change.get("edits") or [])
        else:
            kind = change.get("kind", "unknown")
            warnings.append(f"skipped unsupported {kind} file operation")

    return edits, warnings


def split_keeping_endings(text: str) -> list[tuple[str, str]]:
    """Split into (content, line-ending) pairs so each line keeps its own terminator.

    Files must be read with newline="" for this to see real endings; universal-newline
    translation would rewrite CRLF as LF and silently reformat the whole file on save.
    """
    rows: list[tuple[str, str]] = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        rows.append((stripped, line[len(stripped) :]))
    if not rows or text.endswith(("\n", "\r")):
        rows.append(("", ""))
    return rows


def apply_edits(edits: dict[Path, list[dict]]) -> tuple[int, list[str]]:
    """Apply text edits in place, bottom-up so earlier edits never shift later offsets."""
    applied = 0
    problems: list[str] = []

    for path, file_edits in edits.items():
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                original = handle.read()
        except OSError as exc:
            problems.append(f"{path}: {exc}")
            continue

        rows = split_keeping_endings(original)

        def sort_key(edit: dict) -> tuple[int, int]:
            start = edit["range"]["start"]
            return (start["line"], start["character"])

        for edit in sorted(file_edits, key=sort_key, reverse=True):
            start = edit["range"]["start"]
            end = edit["range"]["end"]
            if start["line"] != end["line"]:
                problems.append(
                    f"{path}: skipped a multi-line edit, which rename should not produce"
                )
                continue
            row = start["line"]
            if row >= len(rows):
                problems.append(
                    f"{path}: edit points past end of file (line {row + 1})"
                )
                continue
            text, ending = rows[row]
            begin = utf16_to_index(text, start["character"])
            finish = utf16_to_index(text, end["character"])
            rows[row] = (text[:begin] + edit.get("newText", "") + text[finish:], ending)
            applied += 1

        rebuilt = "".join(text + ending for text, ending in rows)
        try:
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(rebuilt)
        except OSError as exc:
            problems.append(f"{path}: {exc}")

    return applied, problems


def format_report(
    edits: dict[Path, list[dict]],
    root: Path,
    old_name: str,
    new_name: str,
    dry_run: bool,
    warnings: list[str],
    problems: list[str],
) -> str:
    total = sum(len(items) for items in edits.values())
    if not total:
        return (
            f"ty reported no edits for renaming {old_name!r} to {new_name!r}. "
            "The symbol may be unreferenced, or defined outside this project."
        )

    header = (
        f"{'DRY RUN, nothing written' if dry_run else 'APPLIED'}: "
        f"{old_name} -> {new_name}\n{total} edits across {len(edits)} files"
    )

    rows = sorted(
        ((relative(path, root), len(items)) for path, items in edits.items()),
        key=lambda row: (-row[1], row[0]),
    )
    listing = "\n".join(f"  {count:4d}  {name}" for name, count in rows)

    parts = [header, listing]
    if warnings:
        parts.append("Warnings:\n" + "\n".join(f"  {item}" for item in warnings))
    if problems:
        parts.append("Problems:\n" + "\n".join(f"  {item}" for item in problems))
    if dry_run:
        parts.append("Re-run with dry_run: false to apply.")
    else:
        parts.append(
            "Verify with `ty check` (or your project's typecheck command), review `git diff`, "
            "and grep for dynamic references ty cannot see: getattr, string-keyed lookups, "
            "migrations, templates."
        )
    return "\n\n".join(parts)


def do_rename(
    root: Path,
    path: Path,
    line: int,
    character: int,
    new_name: str,
    old_name: str,
    dry_run: bool,
) -> str:
    """Run prepareRename then rename at a resolved position. line/character are 0-based."""
    client = LspClient(root)
    try:
        client.initialize()
        uri = client.open_document(path)
        position = {"line": line, "character": character}

        try:
            client.request(
                "textDocument/prepareRename",
                {"textDocument": {"uri": uri}, "position": position},
            )
        except LspError as exc:
            raise LspError(
                f"ty will not rename at {relative(path, root)}:{line + 1}:{character + 1}, {exc}"
            )

        workspace_edit = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": position,
                "newName": new_name,
            },
        )
        edits, warnings = collect_edits(workspace_edit)

        problems: list[str] = []
        if not dry_run:
            client.close_documents()
            _, problems = apply_edits(edits)

        return format_report(
            edits, root, old_name, new_name, dry_run, warnings, problems
        )
    finally:
        client.shutdown()


def validate_identifier(name: str) -> None:
    if not name.isidentifier():
        raise LspError(f"{name!r} is not a valid Python identifier")
    import keyword

    if keyword.iskeyword(name):
        raise LspError(f"{name!r} is a Python keyword and cannot be used as a name")


def tool_rename_symbol(arguments: dict[str, Any]) -> str:
    symbol = (arguments.get("symbol") or "").strip()
    new_name = (arguments.get("new_name") or "").strip()
    if not symbol or not new_name:
        raise LspError("both `symbol` and `new_name` are required")
    validate_identifier(new_name)
    dry_run = arguments.get("dry_run", True) is not False
    root = resolve_root(arguments.get("project_root"))

    client = LspClient(root)
    try:
        client.initialize()
        match = find_symbol(client, symbol)
    finally:
        client.shutdown()

    location = match.get("location", {})
    path = uri_to_path(location.get("uri", ""))
    start = location.get("range", {}).get("start", {})
    line = start.get("line", 0)
    character = locate_identifier(path, line, symbol, start.get("character", 0))

    return do_rename(root, path, line, character, new_name, symbol, dry_run)


def tool_rename_at(arguments: dict[str, Any]) -> str:
    file_arg = (arguments.get("file") or "").strip()
    new_name = (arguments.get("new_name") or "").strip()
    if not file_arg or not new_name:
        raise LspError("`file`, `line`, `character`, and `new_name` are required")
    validate_identifier(new_name)

    raw_line = arguments.get("line")
    raw_character = arguments.get("character")
    if raw_line is None or raw_character is None:
        raise LspError("`line` and `character` are required (both 1-based)")
    try:
        line = int(raw_line)
        character = int(raw_character)
    except (TypeError, ValueError):
        raise LspError("`line` and `character` must be integers (both 1-based)")
    if line < 1 or character < 1:
        raise LspError("`line` and `character` are 1-based, so both must be >= 1")

    dry_run = arguments.get("dry_run", True) is not False
    root = resolve_root(arguments.get("project_root"))

    path = Path(file_arg).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_file():
        raise LspError(f"no such file: {file_arg}")

    return do_rename(
        root,
        path,
        line - 1,
        character - 1,
        new_name,
        f"symbol at {file_arg}:{line}",
        dry_run,
    )


TOOLS = [
    {
        "name": "rename_symbol",
        "description": (
            "Rename a Python symbol and every reference to it, project-wide, using ty's "
            "semantic understanding. Resolves the symbol by name; if the name is ambiguous "
            "or unknown it returns the candidates instead of guessing. Defaults to a dry "
            "run that reports the edits without writing. Use this instead of grep plus "
            "manual edits, it will not touch matching text in strings, comments, or "
            "docstrings. Cannot see dynamic access (getattr, string-keyed dispatch), "
            "migrations, or templates; check those separately."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Exact current name, e.g. UserService",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name; must be a valid identifier",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Report edits without writing. Defaults to true; pass false to apply.",
                    "default": True,
                },
                "project_root": {
                    "type": "string",
                    "description": "Project root. Defaults to CLAUDE_PROJECT_DIR or the current directory.",
                },
            },
            "required": ["symbol", "new_name"],
        },
    },
    {
        "name": "rename_at",
        "description": (
            "Rename the Python symbol at an exact position and every reference to it, "
            "project-wide. Use when a bare name is ambiguous, a method or field shared by "
            "several classes, or a local. line and character are 1-based, matching editor "
            "display and grep output. Defaults to a dry run."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to the file, absolute or relative to the project root",
                },
                "line": {
                    "type": "integer",
                    "description": "1-based line number of the symbol",
                },
                "character": {
                    "type": "integer",
                    "description": "1-based column of the symbol's first character",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name; must be a valid identifier",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Report edits without writing. Defaults to true; pass false to apply.",
                    "default": True,
                },
                "project_root": {
                    "type": "string",
                    "description": "Project root. Defaults to CLAUDE_PROJECT_DIR or the current directory.",
                },
            },
            "required": ["file", "line", "character", "new_name"],
        },
    },
]

HANDLERS = {"rename_symbol": tool_rename_symbol, "rename_at": tool_rename_at}


class McpServer:
    """MCP over stdio: newline-delimited JSON-RPC, unlike LSP's Content-Length framing."""

    def __init__(self) -> None:
        self.protocol_version = DEFAULT_PROTOCOL_VERSION

    def send(self, message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    def reply(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def fail(self, request_id: Any, code: int, message: str) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            requested = params.get("protocolVersion")
            if isinstance(requested, str) and requested:
                self.protocol_version = requested
            self.reply(
                request_id,
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
            return

        if method in ("notifications/initialized", "initialized"):
            return

        if method == "ping":
            self.reply(request_id, {})
            return

        if method == "tools/list":
            self.reply(request_id, {"tools": TOOLS})
            return

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            handler = HANDLERS.get(name)
            if handler is None:
                self.reply(
                    request_id,
                    {
                        "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                        "isError": True,
                    },
                )
                return
            try:
                text = handler(arguments)
                self.reply(request_id, {"content": [{"type": "text", "text": text}]})
            except LspError as exc:
                self.reply(
                    request_id,
                    {"content": [{"type": "text", "text": str(exc)}], "isError": True},
                )
            except Exception as exc:
                log(f"unhandled error in {name}: {exc!r}")
                self.reply(
                    request_id,
                    {
                        "content": [{"type": "text", "text": f"{name} failed: {exc}"}],
                        "isError": True,
                    },
                )
            return

        if request_id is not None:
            self.fail(request_id, -32601, f"method not found: {method}")

    def serve(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                log(f"ignoring malformed frame: {line[:120]}")
                continue
            try:
                self.handle(message)
            except Exception as exc:
                log(f"handler crashed: {exc!r}")
                if message.get("id") is not None:
                    self.fail(message["id"], -32603, str(exc))


if __name__ == "__main__":
    McpServer().serve()
