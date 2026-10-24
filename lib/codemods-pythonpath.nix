# Minimal PYTHONPATH source for build-time packaging patch scripts.
#
# Package derivations run repo patch scripts (packages/*/patch_*.py,
# overlays/*/patch_*.py) and import shared helpers from `lib.codemods`.
# Interpolating the repo root (`${../..}`) for PYTHONPATH couples those
# derivations to every file in the repository, which rebuilds heavyweight
# packages (zed-editor-nightly, gitbutler, goose-cli, emdash, superset) on
# every commit even when nothing relevant changed.
#
# This helper exposes only the exact import closure the patch scripts use.
# Every build-time patch script shares this one closure, so the count-and-raise
# rule (lib.codemods.text) and declarative patch sets
# (lib.codemods.source_patch) have exactly one implementation. If a patch
# script grows a new `lib.codemods.*` import, the build fails loudly with an
# ImportError; extend the fileset below to match.
#
# The modules rely on Python 3.14 deferred annotation evaluation. Run patch
# scripts with the package set's `python3`, not an older pinned interpreter.
{ lib }:
lib.fileset.toSource {
  root = ../.;
  fileset = lib.fileset.unions [
    ./__init__.py
    ./codemods/__init__.py
    ./codemods/errors.py
    ./codemods/source_patch.py
    ./codemods/text.py
  ];
}
