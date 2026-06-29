# Packaging Source Modification Policy

This repository packages upstream source trees. Any change to an unpacked upstream
source tree should use the smallest mechanism that is still reviewable and fails
loudly when upstream changes shape.

## Decision Ladder

1. Use a checked-in patch file for a small, stable upstream hunk.
1. Use a structured parser codemod for source or data formats when the edit needs syntax.
1. Use parser-backed helpers for data formats such as JSON, TOML, YAML, and plist.
1. Use a Python codemod for computed rewrites, cross-file state, copied assets, or package
   orchestration.
1. Use anchored text replacement only as a last resort, and only through exact-count helpers.

## Layout

Package-specific codemods live beside the package or overlay they patch:

```text
packages/example/codemods/
  rewrite_upstream.py
```

Reusable mechanics live under `lib/codemods/`. Package-local scripts should stay thin and
describe what they are changing; shared helpers should own how changes are validated.
`lib/tests/test_packaging_source_modification_policy.py` keeps a baseline of
existing ad hoc package and overlay rewrites so new ones fail in pytest until
they use these mechanisms or are explicitly accepted as migration debt.

## Source Code

Source-code codemods should use the smallest parser or exact-count helper that preserves
the upstream-shape check. Do not add a new parser dependency for one stable hunk; use
`lib.codemods.text` when an anchored snippet is enough:

```sh
python - <<'PY'
from pathlib import Path

from lib.codemods.text import regex_replace_file_exactly

regex_replace_file_exactly(
    Path("src/main.rs"),
    pattern=r"config\.include\((?P<path>[^)]+)\);",
    replacement=r"config.include_path(\g<path>);",
    expected_count=1,
)
PY
```

## Structured Data

Do not use ast-grep or raw string replacement for JSON, TOML, YAML, plist, or lock-like data when a
parser is available. Use a parser-backed helper instead, preserving stable formatting and asserting
the semantic shape being changed.

## Text Fallbacks

If a format has no practical parser and a patch file is not appropriate, use helpers from
`lib.codemods.text`. The expected replacement count must be explicit. A missing or duplicate anchor
is an upstream-shape failure, not a silent no-op.
