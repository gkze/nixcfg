# Minimal PYTHONPATH source for build-time packaging patch scripts.
#
# Several package derivations run repo patch scripts (packages/*/patch_*.py,
# overlays/*/patch_*.py) with plain python3 and import shared helpers from
# `lib.codemods`. Interpolating the repo root (`${../..}`) for PYTHONPATH
# couples those derivations to every file in the repository, which rebuilds
# heavyweight packages (zed-editor-nightly, gitbutler, goose-cli, emdash,
# superset) on every commit even when nothing relevant changed.
#
# This helper exposes only the exact import closure the patch scripts use.
# If a patch script grows a new `lib.codemods.*` import, the build fails
# loudly with an ImportError; extend the fileset below to match.
{ lib }:
lib.fileset.toSource {
  root = ../.;
  fileset = lib.fileset.unions [
    ./__init__.py
    ./codemods/__init__.py
    ./codemods/errors.py
    ./codemods/text.py
  ];
}
