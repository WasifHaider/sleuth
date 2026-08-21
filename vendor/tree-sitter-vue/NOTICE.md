# Vendored dependency: tree-sitter-vue

Source: https://github.com/tree-sitter-grammars/tree-sitter-vue
Commit: cloned `main` branch, 2026-08-20 (see git log of this SLEUTH repo for the
exact date this directory was added/updated).
License: MIT (see `LICENSE` in this directory — original copyright retained).

## Why vendored instead of a normal pip/git dependency

This grammar has no PyPI release, and its published `pyproject.toml` fails to
build on modern setuptools (>=70) due to two strict-validation errors:

1. `project.urls.Funding = ""` — an empty string is not a valid URL.
2. `project.urls.Homepage = "tree-sitter-grammars/tree-sitter-vue"` — a bare
   repo slug, not a full URL.

Both were confirmed to break a fresh `pip install` from the raw upstream git
URL, not just a local quirk. Fixed here by:

- Deleting the `Funding = ""` line entirely.
- Rewriting `Homepage` to the full `https://github.com/...` URL.

No other changes were made — `grammar.js`, `src/`, `queries/`, and the Python
bindings under `bindings/python/` are unmodified upstream content.

## Build requirement

Needs core `tree-sitter>=0.25` (this grammar's compiled parser targets ABI 15;
tree-sitter's Python bindings only accept parsers within a supported ABI
range for whichever core version is installed — confirmed 0.25.2 accepts it,
0.23.2/0.24.0 do not). All of SLEUTH's other tree-sitter grammars (python,
javascript, typescript, css) were confirmed still compatible at this same
core version before it was bumped repo-wide in `requirements.txt`.

## Updating this vendor copy

If a newer upstream commit is needed later: re-clone the repo fresh, re-apply
the same two `pyproject.toml` edits described above, replace this directory's
contents (except this NOTICE.md), and re-verify `pip install ./vendor/tree-sitter-vue`
still builds before committing.
