---
name: ty-navigation
description: How to navigate and refactor Python with the language server instead of grep. Use when locating where a symbol is defined or used, reading an unfamiliar or large Python file, tracing a call chain, or renaming any Python symbol. Also use when tempted to grep for an identifier, the LSP answers those questions correctly and far more cheaply.
---

# Navigating Python with ty

This project has a ty language server attached, so Python questions that used to need
grep have exact answers. Grep matches text; the language server understands the code. It
does not match identifiers inside strings, comments, or docstrings, and it does not
confuse two different symbols that happen to share a name.

## Route the question to the right tool

**Where is this symbol used?** → `findReferences`. Never grep for an identifier.

Measured on a 203k-LOC codebase: `findReferences` on a service class returned 199 real
references across 38 files in 0.6s. The equivalent `rg` returned 163 lines that had to be
read as raw source, roughly ten times the tokens, and it included docstring prose that
merely mentioned the name. On another class, grep reported 31 hits where only 16 were real
code references; the other 15 were words in docstrings.

**Where is this defined? What type is it?** → `goToDefinition`, `hover`,
`goToImplementation`. Not grep for `class X` or `def x`.

**What's in this file?** → `documentSymbol` before reading it. On a 2,595-line router,
the outline was 87 symbols with line numbers for ~740 tokens; reading the file whole cost
~23,300. That's a 31× difference for the information needed to decide *which* lines to
read. For any file over roughly 300 lines, outline first, then read only the ranges that
matter.

**What calls this, and what does it call?** → `prepareCallHierarchy`, `incomingCalls`,
`outgoingCalls`.

**Which file defines a symbol I only know by name?** → `workspaceSymbol`.

**Rename a symbol** → `rename_symbol`, below. Never grep-and-edit.

## Renaming

Claude Code's built-in LSP tool has no rename operation, so this plugin adds two:

- `rename_symbol(symbol, new_name)`, resolves by name. If the name is unknown or
  ambiguous it returns candidates rather than guessing.
- `rename_at(file, line, character, new_name)`, 1-based position, for when a bare name is
  ambiguous (a method or attribute shared across classes, or a local).

Both **default to a dry run** that reports the edit count per file without writing. Read
the dry run, confirm the blast radius is what you expected, then re-run with
`dry_run: false`.

A rename of one class produced 237 edits across 38 files in 0.5s, correct by
construction, and it left the class name mentioned in docstrings and string literals
alone.

After applying a rename:

1. Run the project's typecheck (`ty check`, or whatever the repo uses).
2. Review `git diff`.
3. Grep for what the language server cannot see (below).

## Where grep is still the right tool

The language server only sees static Python. Reach for grep when the question is about:

- **Dynamic access**, `getattr`/`setattr`, string-keyed dispatch tables, dependency
  injection by name, anything where the identifier only exists as a string at runtime.
- **Non-Python files**, SQL, Alembic migrations that name tables or columns as strings,
  Jinja/HTML templates, YAML/TOML config, JSON fixtures.
- **String and comment content**, user-facing copy, enum *values* (as opposed to member
  names), log messages, TODOs.
- **Files outside the project root** the server has indexed.

After any rename, grep for the old name once to catch exactly these cases. A clean
typecheck does not prove a dynamic reference survived.

## Diagnostics

ty pushes diagnostics into context after edits, so a type error in a file you just changed
surfaces on its own. Don't shell out to a full typecheck just to see whether an edit was
sound, read the diagnostic. Run the project's own typecheck command before handing work
over, or when you need whole-project confirmation.
