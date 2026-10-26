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

Needs Claude Code v2.1.205+, Python 3.10+, and `ty` on PATH (`uv tool install ty`).

```text
/plugin marketplace add Aethryl/ty-agent-lsp
/plugin install ty-agent-lsp@ty-agent-lsp
/reload-plugins
```

Only one Python LSP can be enabled — the first to claim `.py` wins. Disable `pyright-lsp`
if you have it.

## What you get

- **Navigation + diagnostics**, automatic: references, definitions, symbols, hover,
  implementations, call hierarchy, type errors pushed into context after each edit.
- **Rename**, via MCP — Claude Code's built-in LSP tool has none.
- **A skill** telling the agent to use all of it instead of grep. To enforce it, add
  [lsp-guard](https://github.com/Aethryl/lsp-guard).

`ty` is resolved per project (`$VIRTUAL_ENV` → `.venv` → Poetry → PATH), so the agent's
diagnostics match your CI. Worktrees fall back to the main checkout's environment.

## Rename

| Tool | Picks the symbol by |
| --- | --- |
| `rename_symbol` | project-wide name |
| `rename_at` | 1-based `file`, `line`, `character` |

- Both take `new_name`; both **dry-run by default** — pass `dry_run: false` to write.
- Dry run prints edit count and affected files, so the blast radius is visible first.
- Ambiguous or unknown name → refused, candidates listed. Never guessed.
- Every run lists what ty **could not** resolve, tagged `(text)` or `(string)`.

## Why it pays off

Across 125 real sessions on a 203k-LOC codebase, **63% of tool-output tokens went to `Read`
and `grep`**. On that same codebase:

| Question | With ty | With grep / Read |
| --- | --- | --- |
| Where is `UserService` used? | 237 refs, 38 files, 0.5s | 163 lines ≈ 4,300 tokens |
| Outline a 2,595-line router | 87 symbols, **740 tokens** | 23,305 tokens (**31×**) |
| `ReadUserDTO.phone_number` → `phone` | **2 edits, 2 files** | 252 lines, 102 files |
| `OrderService.get` → `get_by_id` | **2 edits, 2 files** | 1,846 lines |

## Limits

ty sees static Python only: `getattr`, string-keyed dispatch, `__all__` entries, registry
keys, migrations, templates and config are invisible to it — and a rename can break them
while `ty check` still passes. That's what the residual scan reports; `(string)` hits are
the dangerous ones. It covers `.py`/`.pyi`, so grep your templates, SQL and YAML too.

## License

MIT
