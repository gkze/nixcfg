"""Hold an unmaterialized Superset pin that would break flake evaluation.

The pins phase advances the version-like ``superset`` input and writes
``sources.json`` with ``hashes: []`` before ``bun.nix`` is regenerated.
Native jobs that do not materialize that pin keep it. A job that fails and
reverts to the baseline is invisible in the cross-platform 3-way merge, so
the pin wins. Plan-shards then evaluates the new tree with the previous
``bun.nix`` and aborts on the first workspace path upstream removed
(Update 42032332040, ``apps/admin`` at ``624219c``).

A promotable bump has materialized hashes. Those hashes mean the updater
regenerated the workspace graph and checked it against the locked tree,
including when that graph's bytes did not change. A flake input that moved
while ``sources.json`` and ``bun.nix`` stayed on the baseline is restored.
Other inputs in the same ``flake.nix`` and ``flake.lock`` stay.

The caller passes the tree after every native candidate has been folded.
The hold rewrites ``flake.nix`` and ``flake.lock``. Doing that before the
remaining candidate is folded makes those files disagree with a pin bump
the candidates already shared.
"""

import json
from typing import NamedTuple

from lib.update.flake_inputs import input_declaration_text, restore_input_declaration

_INPUT = "superset"
_SOURCES = "packages/superset/sources.json"
_BUN_NIX = "packages/superset/bun.nix"
_BUN_LOCK = "packages/superset/bun.lock"
_FLAKE_NIX = "flake.nix"
_FLAKE_LOCK = "flake.lock"
_PACKAGE_PATHS = (_SOURCES, _BUN_LOCK, _BUN_NIX)

HOLD_EXCERPT = (
    "Superset input moved without materialized hashes or a regenerated "
    "bun.nix. Held at the last pin whose workspace paths match the locked tree."
)


class FileBlob(NamedTuple):
    """One indexed file: Git mode plus blob bytes."""

    mode: str
    content: bytes


def hold_unmaterialized_superset_pin(
    base: dict[str, FileBlob],
    merged: dict[str, FileBlob],
) -> tuple[dict[str, FileBlob], bool]:
    """Return ``merged`` with Superset restored when its pin is not promotable.

    The second value is true when the returned map differs from ``merged``.
    """
    if _SOURCES not in base or _SOURCES not in merged or not _should_hold(base, merged):
        return merged, False
    restored = dict(merged)
    for path in _PACKAGE_PATHS:
        baseline = base.get(path)
        if baseline is None:
            restored.pop(path, None)
        else:
            restored[path] = baseline
    _restore_flake_nix(base, restored)
    _restore_flake_lock(base, restored)
    return restored, restored != merged


def _should_hold(base: dict[str, FileBlob], merged: dict[str, FileBlob]) -> bool:
    """Return whether the merged Superset pin must not be promoted.

    Materialized hashes are a completed regeneration. ``bun.nix`` can stay
    byte-identical when a release changes application code but not the
    workspace list. The stale graph is the baseline ``sources.json`` paired
    with a flake input that moved and a ``bun.nix`` that did not.
    """
    sources = merged[_SOURCES]
    baseline_sources = base[_SOURCES]
    if sources != baseline_sources:
        return not _hashes_materialized(sources.content)
    return _input_moved(base, merged) and merged.get(_BUN_NIX) == base.get(_BUN_NIX)


def _hashes_materialized(content: bytes) -> bool:
    """Return whether ``sources.json`` carries at least one real hash."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    hashes = payload.get("hashes")
    if isinstance(hashes, list | dict):
        return len(hashes) > 0
    return False


def _input_moved(base: dict[str, FileBlob], merged: dict[str, FileBlob]) -> bool:
    """Return whether the Superset flake input declaration or lock node moved."""
    return _attrset_text(merged.get(_FLAKE_NIX)) != _attrset_text(
        base.get(_FLAKE_NIX)
    ) or _lock_node(merged.get(_FLAKE_LOCK)) != _lock_node(base.get(_FLAKE_LOCK))


def _attrset_text(blob: FileBlob | None) -> str | None:
    """Return the Superset input attrset, or None when the file lacks one."""
    if blob is None:
        return None
    return input_declaration_text(blob.content.decode(), _INPUT)


def _lock_node(blob: FileBlob | None) -> object:
    """Return the Superset lock node, or None when the lock does not name it."""
    if blob is None:
        return None
    return _lock_nodes(_lock_document(blob.content)).get(_INPUT)


def _restore_flake_nix(
    base: dict[str, FileBlob],
    restored: dict[str, FileBlob],
) -> None:
    """Splice the baseline Superset input back into the merged flake."""
    current = restored.get(_FLAKE_NIX)
    baseline = base.get(_FLAKE_NIX)
    if current is None or baseline is None:
        return
    text = restore_input_declaration(
        current.content.decode(),
        baseline.content.decode(),
        _INPUT,
    )
    restored[_FLAKE_NIX] = _with_content(current, text.encode())


def _restore_flake_lock(
    base: dict[str, FileBlob],
    restored: dict[str, FileBlob],
) -> None:
    """Put the baseline Superset lock node back without rewriting other nodes."""
    current = restored.get(_FLAKE_LOCK)
    baseline = base.get(_FLAKE_LOCK)
    if current is None or baseline is None:
        return
    current_doc = _lock_document(current.content)
    baseline_doc = _lock_document(baseline.content)
    baseline_node = _lock_nodes(baseline_doc).get(_INPUT)
    if baseline_node is None:
        msg = "Superset hold baseline flake.lock has no superset node"
        raise ValueError(msg)
    _lock_nodes(current_doc)[_INPUT] = baseline_node
    _restore_root_edge(current_doc, baseline_doc)
    rendered = (json.dumps(current_doc, indent=2, sort_keys=True) + "\n").encode()
    restored[_FLAKE_LOCK] = _with_content(current, rendered)


def _lock_document(content: bytes) -> dict[str, object]:
    """Return a flake.lock object whose ``nodes`` map is present."""
    payload = json.loads(content)
    if not isinstance(payload, dict):
        msg = "Superset hold could not read flake.lock"
        raise TypeError(msg)
    _lock_nodes(payload)
    return payload


def _lock_nodes(document: dict[str, object]) -> dict[str, object]:
    """Return the lock's node map."""
    nodes = document.get("nodes")
    if not isinstance(nodes, dict):
        msg = "Superset hold could not read flake.lock nodes"
        raise TypeError(msg)
    return nodes


def _restore_root_edge(
    current: dict[str, object],
    baseline: dict[str, object],
) -> None:
    """Point the root edge for Superset at the baseline node name."""
    baseline_inputs = _root_inputs(baseline)
    current_inputs = _root_inputs(current)
    if not isinstance(baseline_inputs, dict) or not isinstance(current_inputs, dict):
        return
    edge = baseline_inputs.get(_INPUT)
    if isinstance(edge, str):
        current_inputs[_INPUT] = edge


def _root_inputs(document: dict[str, object]) -> object:
    """Return the root node's input map, or None when the lock has none.

    ``_lock_document`` has already required ``nodes`` to be an object.
    """
    root_name = document.get("root")
    if not isinstance(root_name, str):
        return None
    root = _lock_nodes(document).get(root_name)
    if not isinstance(root, dict):
        return None
    return root.get("inputs")


def _with_content(blob: FileBlob, content: bytes) -> FileBlob:
    """Return ``blob`` unchanged when ``content`` already matches."""
    if blob.content == content:
        return blob
    return FileBlob(blob.mode, content)
