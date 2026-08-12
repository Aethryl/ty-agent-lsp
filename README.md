<h1 align="center">ty-agent-lsp</h1>

<p align="center">
  Python code intelligence for Claude Code, backed by
  <a href="https://github.com/astral-sh/ty">ty</a>, Astral's Rust type checker and language server.
</p>

<p align="center">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg">
  <img alt="Claude Code v2.1.205+" src="https://img.shields.io/badge/Claude%20Code-v2.1.205%2B-d97757.svg">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab.svg">
</p>

The agent navigates Python by symbol instead of by grep: exact references, cheap file
outlines, type errors after each edit, and a real workspace-wide rename.

## Install

```text
/plugin marketplace add Aethryl/ty-agent-lsp
/plugin install ty-agent-lsp@ty-agent-lsp
/reload-plugins
```

Requirements:

| | |
| --- | --- |
| Claude Code | v2.1.205+ (older versions silently skip the server) |
| `ty` | on PATH — `uv tool install ty` |
| Python | 3.10+, stdlib only, nothing to install |

Check `/plugin`: one LSP server, one MCP server, empty Errors tab. `claude --debug` says why
if a server was skipped.

**Only one Python LSP may be enabled** — the first to claim `.py` wins, the other never
starts. Disable `pyright-lsp` if you have it:

```json
{ "enabledPlugins": { "pyright-lsp@claude-plugins-official": false } }
```

## What you get

- **Navigation + diagnostics** — automatic. References, definitions, symbols, hover,
  implementations, call hierarchy; type errors pushed into context after each edit.
- **Rename** — two MCP tools; Claude Code's built-in LSP tool has none.
- **A skill** telling the agent to use all of it instead of grep.

## Rename

| Tool | Picks the symbol by |
| --- | --- |
| `rename_symbol` | project-wide name |
| `rename_at` | 1-based `file`, `line`, `character` |

- Both take `new_name`; both **dry-run by default** — pass `dry_run: false` to write.
- Dry run prints edit count + affected files, so the blast radius is visible first.
- Unknown or ambiguous name → refused, with near-miss candidates listed. Never guessed.
- Every run lists occurrences ty **could not** resolve, tagged `(text)` or `(string)`.

## Why it pays off

Across 125 real sessions on a 203k-LOC codebase, **63% of tool-output tokens went to `Read`
and `grep`**. Measured on that codebase:

| Question | With ty | With grep / Read |
| --- | --- | --- |
| Where is `UserService` used? | 237 refs, 38 files, 0.5s | 163 lines ≈ 4,300 tokens |
| Outline a 2,595-line router | 87 symbols, **740 tokens** | 23,305 tokens (**31×**) |
| `ReadUserDTO.phone_number` → `phone` | **2 edits, 2 files** | 252 lines, 102 files |
| `OrderService.get` → `get_by_id` | **2 edits, 2 files** | 1,846 lines |

Cheaper always; dramatically more accurate on common names (`get` is a method on 52
classes). Counts aren't directly comparable — grep counts lines, LSP counts occurrences.

## Which ty gets used

Resolved per project so diagnostics match your CI, in order:

1. `$VIRTUAL_ENV` → 2. `<project>/.venv`, `venv` → 3. Poetry env → 4. `PATH`

Both servers share the resolver, so they can't disagree. `claude --debug` shows the pick.
Available operations track the *resolved* version — on ty 0.0.62 `goToImplementation` is
simply absent.

## Worktrees

Poetry keys its venv on the project path, so a worktree finds nothing or an empty venv → ty
resolves no imports → 1,724 phantom errors in the agent's context. So:

- A candidate env must actually contain a declared dependency, else it's rejected (logged).
- In a worktree, falls back to the main checkout's `.venv` / Poetry env.
- Warns when a worktree's dependency tables differ from main — it needs its own install.

Other tools do their own discovery, so symlink the env in once:

```bash
MAIN=$(dirname "$(git rev-parse --git-common-dir)")
ln -sfn "$(poetry -C "$MAIN" env info --path)" .venv
```

## Limits

Verified: docstrings, comments and string literals untouched; CRLF stays CRLF; UTF-16
columns converted correctly; multiple hits per line all land.

Not solvable by any tool — ty sees static Python only:

- `getattr`/`setattr`, string-keyed dispatch, `__all__` entries, registry keys, Alembic
  migrations, Jinja, config files.
- Real case: renaming `BaseError` passed `ty check` cleanly and still broke the package —
  `__all__` held the old name as a string. Hence the residual scan; `(string)` hits are the
  dangerous ones.
- The scan covers `.py`/`.pyi` only. **Grep your templates, SQL and YAML after a rename**,
  and run a linter, not just a typechecker.

ty is pre-1.0 (tested against 0.0.62). Review the diff before committing.

## Notes

- **Why ty, not pyright** — if your project already uses ty, the agent sees exactly what
  pre-commit and CI enforce, `[tool.ty]` rules included. If pyright is your source of truth,
  install `pyright-lsp` instead and lose rename.
- **Diagnostics** — ty pushes `publishDiagnostics` because Claude Code is a push client. If
  diagnostics ever go quiet, check that first: a client advertising pull support makes ty go
  silent on push while staying perfectly healthy.

## Layout

```text
.claude-plugin/marketplace.json
plugins/ty-agent-lsp/
├── .claude-plugin/plugin.json       # lspServers: ty
├── .mcp.json                        # rename server
├── server/ty_refactor.py            # stdlib-only MCP↔LSP bridge
└── skills/ty-navigation/SKILL.md    # LSP-before-grep guidance
```

## Prior art

[`ilepn/ty-lsp-claude-code`](https://github.com/ilepn/ty-lsp-claude-code) (MIT) — LSP
declaration only, no rename/skill/marketplace, and runs `uvx ty@latest`, which may not be
the version your project pins.

## License

MIT
