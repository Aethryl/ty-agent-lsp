# ty-agent-lsp

Python code intelligence for Claude Code via [ty](https://github.com/astral-sh/ty).

- **Navigation + diagnostics**: find-references, go-to-definition, document and workspace
  symbols, hover, implementations, call hierarchy, and type errors after each edit.
- **Semantic rename**: `rename_symbol` and `rename_at`, the operation Claude Code's
  built-in LSP tool omits. Dry-run by default.
- **A skill** that routes navigation questions to the language server instead of grep.

## Requirements

- Claude Code v2.1.205+
- `ty` on PATH: `uv tool install ty` (or `pip install ty`)
- Python 3.10+ for the rename server (standard library only)

## Notes

Only one LSP server can claim `.py`, so if `pyright-lsp` is enabled, disable it or this one
never starts.

If your project already typechecks with ty, the diagnostics the agent sees match your CI
exactly. That is the main reason to pick this over `pyright-lsp`.

ty is pre-1.0. Rename defaults to a dry run; typecheck and review the diff before
committing, and grep once for the old name to catch dynamic references no type checker can
see.

Full documentation, benchmarks and caveats: https://github.com/Aethryl/ty-agent-lsp
