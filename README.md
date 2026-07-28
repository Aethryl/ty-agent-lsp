# ty-agent-lsp

A Claude Code plugin that gives the agent real Python code intelligence, backed by
[ty](https://github.com/astral-sh/ty), Astral's Rust type checker and language server.

Three things, one install:

1. **Semantic navigation and diagnostics** via Claude Code's native LSP integration ,
   find-references, go-to-definition, document/workspace symbols, hover, implementations,
   call hierarchy, and type errors pushed into context after each edit.
2. **Workspace-wide semantic rename**, `rename_symbol` and `rename_at`, exposed as MCP
   tools. Claude Code's built-in LSP tool has no rename operation; this fills that gap.
3. **A skill** that tells the agent to use all of the above instead of reaching for grep.

## Why

Left to itself, the agent finds code by grepping and then reading whole files. Across 125
real sessions on a 203k-LOC Python codebase (8,252 tool calls), **63% of all tool-output
tokens went to `Read` and `grep`**, 972k tokens reading files, 535k tokens grepping.

That work is both expensive and imprecise. Grep matches text, so it hits identifiers inside
docstrings, comments and string literals, and it cannot tell two same-named symbols apart.

Measured on that same codebase, with `ty` already on PATH:

| Question | With ty | With grep / Read |
|---|---|---|
| Where is `UserService` used? | 237 references across 38 files, **0.5s** | 163 raw source lines ≈ 4,300 tokens of source to re-read |
| What's in a 2,595-line router? | 87-symbol outline, **740 tokens** | 23,305 tokens to read it whole, **31×** |
| What's in a 1,595-line service? | 55-symbol outline, **516 tokens** | 16,288 tokens, **31×** |
| Rename a class everywhere | **237 edits across 38 files, 0.5s** | grep, then edit 38 files by hand |

Numbers are from one real repository; yours will differ. The shape won't.

The token saving is the reliable win: an outline costs a fraction of the file, and a
reference list costs a fraction of the grep output it replaces.

**Precision pays off exactly where names are shared**, and there the gap is not marginal:

| Rename | ty | grep would show |
|---|---|---|
| `ReadUserDTO.phone_number` → `phone` | **2 edits, 2 files** | 252 lines across 102 files |
| `OrderService.get` → `get_by_id` | **2 edits, 2 files** | 1,846 lines |

`phone_number` is a field on many different models, DTOs and schemas; `get` is a method on
52 different classes. ty renames the one you pointed at and leaves the 51 others alone.
Asked for a bare `get`, it refuses outright and lists the candidates rather than guessing.

On a *distinctive* class name grep is often nearly right, just far more expensive, on this
codebase `BaseError` gives 26 references against 31 grep lines, only 2 of which are
docstring prose. So the honest summary is: always cheaper, and dramatically more accurate
on common names.

Note the counts aren't directly comparable, grep counts matching *lines*, the LSP counts
*occurrences*, and Claude Code's LSP tool passes `includeDeclaration: true`.

## Requirements

- **Claude Code v2.1.205 or newer.** Earlier versions accept the LSP config schema but
  silently skip servers that set `restartOnCrash` or `shutdownTimeout`, with the reason
  visible only under `claude --debug`.
- **`ty` on your PATH.** This plugin configures a language server; it does not ship one.

  ```bash
  uv tool install ty     # or: pip install ty
  ty --version
  ```

- **Python 3.10+** for the rename server. It is pure standard library, no pip install.

## Install

```
/plugin marketplace add Aethryl/ty-agent-lsp
/plugin install ty-agent-lsp@ty-agent-lsp
/reload-plugins
```

To try it from a local clone instead:

```
/plugin marketplace add ./ty-agent-lsp
/plugin install ty-agent-lsp@ty-agent-lsp
/reload-plugins
```

Confirm it came up in `/plugin`, the entry should list one LSP server and one MCP server,
with nothing in the Errors tab. If a server was skipped, `claude --debug` says why.

### Only run one Python language server

When two enabled LSP servers claim `.py`, **the first one registered wins and the other
never starts.** If you already have `pyright-lsp` installed, disable it:

```json
{
  "enabledPlugins": {
    "pyright-lsp@claude-plugins-official": false,
    "ty-agent-lsp@ty-agent-lsp": true
  }
}
```

## Which ty gets used

A global `ty` drifts from what a project pins, and then the agent sees diagnostics the
project's own hooks and CI don't have, which defeats the main reason to run ty. So the
plugin resolves the binary per project, in this order:

1. `$VIRTUAL_ENV`
2. `<project>/.venv`, then `<project>/venv`
3. the Poetry environment, when `pyproject.toml` declares Poetry (Poetry keeps venvs
   outside the project, so it has to be asked, this costs ~0.4s once per server start)
4. `ty` on `PATH`

Both the language server and the rename tools go through the same resolver, so they can
never disagree about which binary is in play. `claude --debug` shows the choice
(`[ty-resolve] using ty from poetry: …`).

**The consequence worth knowing:** available operations track the resolved version, not the
newest release. On a project pinned to ty 0.0.62 you get navigation, diagnostics and rename,
but `goToImplementation` is absent, ty added `implementationProvider` in 0.0.64. That is
the intended trade: diagnostics that agree with your pipeline matter more than one extra
navigation op, and it resolves itself when the project bumps ty.

## Why ty rather than pyright

If your project already uses ty, this matters: the diagnostics the agent sees are then
**identical to what your pre-commit hooks and CI enforce**. Point the agent at a different
checker and it will chase errors your pipeline doesn't have, and miss ones it does, ty's
`[tool.ty]` rules (`deprecated = "error"`, for instance) have no pyright equivalent.

ty is also fast enough to be invisible: lazy startup, and sub-second find-references
across 2,000 files.

If your project uses pyright as its source of truth, install `pyright-lsp` instead. You
would lose rename, which pyright's server supports but Claude Code does not expose.

## The rename tools

Both default to a dry run. Nothing is written until you pass `dry_run: false`.

**`rename_symbol`**, by name:

```
rename_symbol(symbol="UserService", new_name="UserAccountService")

DRY RUN, nothing written: UserService -> UserAccountService
237 edits across 38 files

   101  src/users/routers/legacy_users_router.py
    20  src/orders/routers/orders_router.py
     8  tests/users/services/test_update.py
     ...
```

Unknown or misspelled names come back with suggestions rather than a silent no-op:

```
no symbol named 'OrdersService' found in the project.
Did you mean: OrderService, Orders, Order, OrderRepository?
```

**`rename_at`**, by 1-based position, for names that are ambiguous on their own:

```
rename_at(file="src/users/services/user_service.py", line=129, character=7,
          new_name="UserAccountService")
```

Both accept an optional `project_root`; it defaults to `CLAUDE_PROJECT_DIR`, then the
working directory.

### What rename gets right

Verified against a fixture built for the purpose:

- Identifiers in **docstrings, comments and string literals are left alone**.
- **CRLF files stay CRLF**; a file with no trailing newline doesn't gain one.
- **Non-ASCII lines** are handled, LSP columns are UTF-16 code units, converted properly,
  so a line containing `café, naïve ünïcode ✨` edits at the right offset.
- **Multiple occurrences on one line** all land correctly (edits are applied bottom-up so
  earlier ones never shift later offsets).

## A note on diagnostics

ty supports both LSP diagnostic models and picks based on what the client advertises. If a
client declares pull support (`textDocument.diagnostic`), ty answers
`textDocument/diagnostic` and sends **no** `publishDiagnostics` at all, correct 3.17
behaviour, avoiding duplicates. If the client declares push only, ty pushes.

Claude Code is a push client: it registers a `textDocument/publishDiagnostics` handler and
does not advertise pull support. So diagnostics arrive on edit, as intended. Worth knowing
because it's the failure mode to check first if diagnostics ever go quiet, a client that
starts advertising pull would see ty fall silent on push while still being perfectly healthy.

### It tells you what it could not rename

ty renames what it can resolve. The same name in an `__all__` list, a `getattr` lookup, a
registry key or a migration is invisible to it, and **`ty check` still passes, because
none of those are type errors.** So every rename reports what it left behind:

```
26 edits across 10 files
...
5 place(s) still mention 'BaseError' and were NOT renamed, ty cannot resolve these:
  src/locations/exceptions/location_exceptions.py:18 (text)    subclasses BaseError directly. Router…
  mypkg/errors/__init__.py:18            (string)   "BaseError",
  ...

The (string) entries are the dangerous ones. A name in an `__all__` list, a getattr
lookup or a registry key will keep typechecking cleanly and fail at runtime.
```

That report appears on the **dry run too**, positions the rename covers are excluded, so
it reads the same before and after applying. You see the risk before you commit to it.

This is not a hypothetical. Renaming `BaseError` in the test repo rewrote 26 references
across 10 files, `ty check` reported **"All checks passed!"**, and the package was broken:
`__init__.py` still listed `"BaseError"` in `__all__` as a string, so
`from mypkg.errors import *` raised `AttributeError`. The typecheck could not see
it; a linter and this scan both could.

### What no tool can do

ty sees static Python. It does not see `getattr`/`setattr`, string-keyed dispatch, table
and column names in Alembic migrations, Jinja templates, or identifiers spelled out in
config. The residual scan covers `.py`/`.pyi` only, so **after any rename also grep your
non-Python files**, templates, SQL, YAML. And run a linter, not just a typechecker.

ty is also pre-1.0 (tested against 0.0.62). Rename correctness is not guaranteed, which is
why dry-run is the default. Typecheck and review the diff before committing.

## Layout

```
ty-agent-lsp/
├── .claude-plugin/marketplace.json
└── plugins/ty-agent-lsp/
    ├── .claude-plugin/plugin.json       # lspServers: ty
    ├── .mcp.json                        # the rename server
    ├── server/ty_refactor.py            # stdlib-only MCP server
    └── skills/ty-navigation/SKILL.md    # LSP-before-grep guidance
```

`server/ty_refactor.py` bridges two transports: Claude Code speaks MCP (newline-delimited
JSON-RPC), ty speaks LSP (Content-Length-framed JSON-RPC).

## Prior art

[`ilepn/ty-lsp-claude-code`](https://github.com/ilepn/ty-lsp-claude-code) (MIT) is a
minimal ty LSP declaration for Claude Code, written in response to
[astral-sh/ty#2230](https://github.com/astral-sh/ty/issues/2230). It covers the LSP
declaration only, no rename, no skill, no marketplace, and invokes ty as
`uvx ty@latest server`, which resolves a possibly different version than the one your
project pins. This plugin uses the `ty` on your PATH so the agent's diagnostics match your
CI, and adds the rename and guidance layers.

## License

MIT
