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

Three things, one install:

1. **Semantic navigation and diagnostics** via Claude Code's native LSP integration ,
   find-references, go-to-definition, document/workspace symbols, hover, implementations,
   call hierarchy, and type errors pushed into context after each edit.
2. **Workspace-wide semantic rename**, two MCP tools, `rename_symbol` and `rename_at`.
   Claude Code's built-in LSP tool has no rename operation; this fills that gap.
3. **A skill** that tells the agent to use all of the above instead of reaching for grep.

What that buys you, measured on a 203k-LOC Python codebase:

- **31× cheaper file comprehension**, an 87-symbol outline of a 2,595-line router costs
  740 tokens against 23,305 to read the file whole.
- **Exact references in under a second**, 237 uses of a class across 38 files, no
  docstring or comment false positives.
- **Renames that touch only the symbol you meant**, `ReadUserDTO.phone_number` → `phone`
  is 2 edits in 2 files; grep would put 252 lines across 102 files in front of the agent.
- Across 125 real sessions, **63% of all tool-output tokens went to `Read` and `grep`** ,
  that is the bill this shrinks. [Full numbers below.](#why-it-pays-off)

**Contents** · [Requirements](#requirements) · [Install](#install) · [Usage](#usage) ·
[Why it pays off](#why-it-pays-off) · [Which ty gets used](#which-ty-gets-used) ·
[Worktrees](#worktrees) · [ty vs. pyright](#why-ty-rather-than-pyright) ·
[Rename limits](#rename-what-it-guarantees-and-what-it-cannot) ·
[Diagnostics](#a-note-on-diagnostics)

## Requirements

- **Claude Code v2.1.205 or newer.** Earlier versions accept the LSP config schema but
  silently skip servers that set `restartOnCrash` or `shutdownTimeout`, with the reason
  visible only under `claude --debug`.
- **`ty` on your PATH.** This plugin configures a language server; it does not ship one.
- **Python 3.10+** for the rename server. It is pure standard library, no pip install.

```bash
uv tool install ty     # or: pip install ty
ty --version
```

## Install

```text
/plugin marketplace add Aethryl/ty-agent-lsp
/plugin install ty-agent-lsp@ty-agent-lsp
/reload-plugins
```

To try it from a local clone instead:

```text
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

## Usage

Navigation and diagnostics need nothing from you: once the plugin is enabled the agent gets
the LSP operations automatically, the bundled skill points it at them instead of grep, and
type errors arrive in its context after each edit.

Rename is explicit, through two MCP tools:

| Tool | Selects the symbol by | Use when |
| --- | --- | --- |
| `rename_symbol` | project-wide name (`symbol`) | the name is unique enough to identify on its own |
| `rename_at` | 1-based `file`, `line`, `character` | the name is ambiguous, or you already know the position |

Both take `new_name`, both default to a **dry run**, nothing is written until you pass
`dry_run: false`, and both accept an optional `project_root`, which defaults to
`CLAUDE_PROJECT_DIR` and then the working directory.

A dry run reports the total edit count and the affected files ranked by edits, so the blast
radius is visible before anything changes. An unknown or misspelled name is not a silent
no-op: `rename_symbol` reports that nothing matched and lists near-miss candidates. An
ambiguous name is refused the same way, with the candidates listed rather than guessed at.

Every rename, dry run included, also lists the occurrences ty *could not* resolve, which
is the part worth reading before you apply one. See
[Rename: what it guarantees and what it cannot](#rename-what-it-guarantees-and-what-it-cannot).

---

Everything below is detail: the measurements, how the `ty` binary is chosen, worktree
pitfalls, and the exact limits of rename.

## Why it pays off

Left to itself, the agent finds code by grepping and then reading whole files. Across 125
real sessions on a 203k-LOC Python codebase (8,252 tool calls), **63% of all tool-output
tokens went to `Read` and `grep`**, 972k tokens reading files, 535k tokens grepping.

That work is both expensive and imprecise. Grep matches text, so it hits identifiers inside
docstrings, comments and string literals, and it cannot tell two same-named symbols apart.

Measured on that same codebase, with `ty` already on PATH:

| Question | With ty | With grep / Read |
| --- | --- | --- |
| Where is `UserService` used? | 237 references across 38 files, **0.5s** | 163 raw source lines ≈ 4,300 tokens of source to re-read |
| What's in a 2,595-line router? | 87-symbol outline, **740 tokens** | 23,305 tokens to read it whole, **31×** |
| What's in a 1,595-line service? | 55-symbol outline, **516 tokens** | 16,288 tokens, **31×** |
| Rename a class everywhere | **237 edits across 38 files, 0.5s** | grep, then edit 38 files by hand |

Numbers are from one real repository; yours will differ. The shape won't.

The token saving is the reliable win: an outline costs a fraction of the file, and a
reference list costs a fraction of the grep output it replaces.

**Precision pays off exactly where names are shared**, and there the gap is not marginal:

| Rename | ty | grep would show |
| --- | --- | --- |
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

## Worktrees

Poetry names its virtualenv from a hash of the project path, so a git worktree has no link to
its main checkout's environment. Poetry then either finds nothing, or creates an empty venv
that a shell hook activates, leaving `VIRTUAL_ENV` set and useless. Either way ty resolves no
third-party imports and reports every one as an error. On a 203k-LOC project that is **1,724
phantom diagnostics**, and with `diagnostics: true` they land in the agent's context after
every edit, telling it working imports are broken.

So the plugin does not trust an environment merely because it found one. Each candidate must
contain at least one of the project's declared dependencies (read with `tomllib`); an env
holding only pip and setuptools is rejected, with the reason logged. If the project is a git
worktree, it falls back to the main checkout's `.venv` and Poetry environment, found via
`git rev-parse --git-common-dir`.

It also warns when a worktree's dependency tables differ from its main checkout, since the
shared environment is then wrong for it, that worktree wants its own `poetry install`.

Other tools do their own discovery, so for them symlink the environment into the worktree:

```bash
MAIN=$(dirname "$(git rev-parse --git-common-dir)")
ln -sfn "$(poetry -C "$MAIN" env info --path)" .venv
```

Poetry uses an existing `.venv` in preference to its path-keyed one, so this fixes
`poetry run`, your editor's language server, and this plugin in one step.

## Why ty rather than pyright

If your project already uses ty, this matters: the diagnostics the agent sees are then
**identical to what your pre-commit hooks and CI enforce**. Point the agent at a different
checker and it will chase errors your pipeline doesn't have, and miss ones it does, ty's
`[tool.ty]` rules (`deprecated = "error"`, for instance) have no pyright equivalent.

ty is also fast enough to be invisible: lazy startup, and sub-second find-references
across 2,000 files.

If your project uses pyright as its source of truth, install `pyright-lsp` instead. You
would lose rename, which pyright's server supports but Claude Code does not expose.

## Rename: what it guarantees and what it cannot

Verified against a fixture built for the purpose:

- Identifiers in **docstrings, comments and string literals are left alone**.
- **CRLF files stay CRLF**; a file with no trailing newline doesn't gain one.
- **Non-ASCII lines** are handled, LSP columns are UTF-16 code units, converted properly,
  so a line containing `café, naïve ünïcode ✨` edits at the right offset.
- **Multiple occurrences on one line** all land correctly (edits are applied bottom-up so
  earlier ones never shift later offsets).

### It tells you what it could not rename

ty renames what it can resolve. The same name in an `__all__` list, a `getattr` lookup, a
registry key or a migration is invisible to it, and **`ty check` still passes, because
none of those are type errors.** So every rename ends with a scan for occurrences it left
behind, each tagged by kind: `(text)` for prose such as a docstring mention, `(string)` for
a string literal. Positions the rename covers are excluded, so the report reads the same
before and after applying, the risk is visible on the dry run.

The `(string)` entries are the dangerous ones: a name in an `__all__` list, a `getattr`
lookup or a registry key keeps typechecking cleanly and fails at runtime.

This is not hypothetical. Renaming `BaseError` in the test repo rewrote 26 references
across 10 files, `ty check` reported *"All checks passed!"*, and the package was broken:
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

## A note on diagnostics

ty supports both LSP diagnostic models and picks based on what the client advertises. If a
client declares pull support (`textDocument.diagnostic`), ty answers
`textDocument/diagnostic` and sends **no** `publishDiagnostics` at all, correct 3.17
behaviour, avoiding duplicates. If the client declares push only, ty pushes.

Claude Code is a push client: it registers a `textDocument/publishDiagnostics` handler and
does not advertise pull support. So diagnostics arrive on edit, as intended. Worth knowing
because it's the failure mode to check first if diagnostics ever go quiet, a client that
starts advertising pull would see ty fall silent on push while still being perfectly healthy.

## Layout

```text
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
