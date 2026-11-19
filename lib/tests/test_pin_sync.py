"""A moved flake input is rewound when the source files that name it stayed."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.nix.models.sources import SourceEntry
from lib.update import crate2nix as update_crate2nix
from lib.update.ci.pin_sync import (
    _inside_repo,
    _owned_relative_paths,
    _restore_declarations,
    _restore_lock,
    owned_paths_by_input,
    rewind_unpackaged_electron,
    rewind_unpaired_inputs,
)
from lib.update.ci.superset_hold import FileBlob
from lib.update.flake_inputs import input_declaration_text
from lib.update.paths import get_repo_root
from lib.update.updaters import Updater

_BASE = "a" * 40
_PIN = "b" * 40
_SOURCES = "overlays/goose-cli/sources.json"
_GRAPH = "packages/superset/bun.nix"


def _lock(*names: str, rev: str = _BASE) -> str:
    nodes: dict[str, object] = {
        "root": {"inputs": {name: name for name in names}},
    }
    for name in names:
        nodes[name] = {
            "locked": {
                "lastModified": 1,
                "narHash": "sha256-aaaaaaaa",
                "owner": "o",
                "repo": name,
                "rev": rev if name == "goose" else _BASE,
                "type": "github",
            },
            "original": {"owner": "o", "ref": "v1", "repo": name, "type": "github"},
        }
    return json.dumps(
        {"nodes": nodes, "root": "root", "version": 7}, indent=2, sort_keys=True
    )


def _flake(goose: str, codex: str = "v1") -> str:
    return f"""{{
  inputs = {{
    goose = {{
      type = "github";
      owner = "aaif-goose";
      repo = "goose";
      ref = "{goose}";
      flake = false;
    }};
    codex = {{
      url = "{codex}";
      flake = false;
    }};
  }};
  outputs = {{ self, ... }}: {{ }};
}}
"""


def _entry(commit: str = _BASE) -> str:
    return json.dumps(SourceEntry(hashes=[], version="1.55.0", commit=commit).to_dict())


def _blob(content: str) -> FileBlob:
    return FileBlob("100644", content.encode())


def _tree(**files: str) -> dict[str, FileBlob]:
    return {path: _blob(content) for path, content in files.items()}


def _declaration(blob: FileBlob, name: str) -> str | None:
    """Return one input attrset from a flake blob."""
    return input_declaration_text(blob.content.decode(), name)


def _revs(blob: FileBlob) -> dict[str, str]:
    payload = json.loads(blob.content)
    return {
        name: node["locked"]["rev"]
        for name, node in payload["nodes"].items()
        if name != "root"
    }


def test_rewind_restores_one_input_and_leaves_the_other() -> None:
    """Update 44258507999: goose's pin moved and its source entry did not."""
    base = _tree(**{
        "flake.nix": _flake("v1.55.0"),
        "flake.lock": _lock("goose", "codex"),
        _SOURCES: _entry(),
    })
    merged = _tree(**{
        "flake.nix": _flake("v1.60.1", "v2"),
        "flake.lock": _lock("goose", "codex", rev=_PIN),
        _SOURCES: _entry(),
    })

    restored, changed = rewind_unpaired_inputs(
        base, merged, {"goose": frozenset({_SOURCES})}
    )

    assert changed
    assert _declaration(restored["flake.nix"], "goose") == _declaration(
        base["flake.nix"], "goose"
    )
    assert _declaration(restored["flake.nix"], "codex") == _declaration(
        merged["flake.nix"], "codex"
    )
    assert _revs(restored["flake.lock"]) == {"goose": _BASE, "codex": _BASE}
    assert restored[_SOURCES] == merged[_SOURCES]


def test_rewind_splices_a_declaration_whose_lock_node_already_matches() -> None:
    """A ref-only edit still goes back when the source entry did not move."""
    base = _tree(**{
        "flake.nix": _flake("v1.55.0"),
        "flake.lock": _lock("goose"),
        "sources.json": _entry(),
    })
    merged = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": _lock("goose"),
        "sources.json": _entry(),
    })

    restored, changed = rewind_unpaired_inputs(
        base, merged, {"goose": frozenset({"sources.json"})}
    )

    assert changed
    assert _declaration(restored["flake.nix"], "goose") == _declaration(
        base["flake.nix"], "goose"
    )
    assert _declaration(restored["flake.nix"], "codex") == _declaration(
        base["flake.nix"], "codex"
    )
    assert restored["flake.lock"] == merged["flake.lock"]


def test_rewind_leaves_a_pin_whose_evidence_is_not_a_source_entry() -> None:
    """Plain-text sidecars are not evidence, so an unrelated pin bump stays."""
    base = _tree(**{
        "flake.nix": _flake("v1.55.0"),
        "flake.lock": _lock("goose"),
        _SOURCES: "opencode baseline\n",
    })
    merged = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": _lock("goose", rev=_PIN),
        _SOURCES: "opencode baseline\n",
    })

    restored, changed = rewind_unpaired_inputs(
        base, merged, {"goose": frozenset({_SOURCES})}
    )

    assert not changed
    assert restored == merged


@pytest.mark.parametrize(
    "changed",
    [_SOURCES, _GRAPH],
)
def test_rewind_leaves_a_pin_when_an_owned_file_moved(changed: str) -> None:
    """One changed source file or generated graph keeps the candidate pin."""
    files = {_SOURCES: _entry(), _GRAPH: "graph\n"}
    base = _tree(**{
        "flake.nix": _flake("v1.55.0"),
        "flake.lock": _lock("goose"),
        **files,
    })
    files[changed] = _entry(_PIN) if changed == _SOURCES else "graph v2\n"
    merged = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": _lock("goose", rev=_PIN),
        **files,
    })

    restored, changed_pin = rewind_unpaired_inputs(
        base, merged, {"goose": frozenset({_SOURCES, _GRAPH})}
    )

    assert not changed_pin
    assert restored == merged


def test_rewind_leaves_an_input_that_did_not_move() -> None:
    """Identical pins are not rewritten just because a source entry parses."""
    tree = _tree(**{
        "flake.nix": _flake("v1.55.0"),
        "flake.lock": _lock("goose"),
        _SOURCES: _entry(),
    })

    restored, changed = rewind_unpaired_inputs(
        tree, tree, {"goose": frozenset({_SOURCES})}
    )

    assert not changed
    assert restored is tree


def test_rewind_skips_an_input_with_no_owned_files_or_one_sided_file() -> None:
    """An empty owner set, or a file only one tree has, is not a baseline pin."""
    base = _tree(**{"flake.nix": _flake("v1.55.0"), "flake.lock": _lock("goose")})
    merged = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": _lock("goose", rev=_PIN),
        _SOURCES: _entry(),
    })

    assert rewind_unpaired_inputs(base, merged, {"goose": frozenset()}) == (
        merged,
        False,
    )
    assert rewind_unpaired_inputs(base, merged, {"goose": frozenset({_SOURCES})}) == (
        merged,
        False,
    )


def test_rewind_skips_a_declaration_that_is_not_an_attrset() -> None:
    """A URL binding cannot be spliced, so the lock stays with it."""
    url = '{ inputs.goose.url = "github:aaif-goose/goose/v1.60.1"; }\n'
    base_url = '{ inputs.goose.url = "github:aaif-goose/goose/v1.55.0"; }\n'
    base = _tree(**{
        "flake.nix": base_url,
        "flake.lock": _lock("goose"),
        _SOURCES: _entry(),
    })
    merged = _tree(**{
        "flake.nix": url,
        "flake.lock": _lock("goose", rev=_PIN),
        _SOURCES: _entry(),
    })

    restored, changed = rewind_unpaired_inputs(
        base, merged, {"goose": frozenset({_SOURCES})}
    )

    assert not changed
    assert restored["flake.lock"].content == merged["flake.lock"].content


def test_rewind_skips_an_unreadable_lock_and_a_lock_that_cannot_roll_back() -> None:
    """A declaration is not spliced onto a lock this rewind cannot put back."""
    base = _tree(**{
        "flake.nix": _flake("v1.55.0"),
        "flake.lock": _lock("goose"),
        _SOURCES: _entry(),
    })
    broken = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": "{",
        _SOURCES: _entry(),
    })
    assert rewind_unpaired_inputs(base, broken, {"goose": frozenset({_SOURCES})}) == (
        broken,
        False,
    )

    missing = json.dumps({
        "nodes": {
            "codex": {
                "locked": {
                    "lastModified": 1,
                    "narHash": "sha256-aaaaaaaa",
                    "owner": "o",
                    "repo": "codex",
                    "rev": _PIN,
                    "type": "github",
                },
                "original": {"owner": "o", "repo": "codex", "type": "github"},
            }
        },
        "root": "root",
        "version": 7,
    })
    current = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": missing,
        _SOURCES: _entry(),
    })
    restored, changed = rewind_unpaired_inputs(
        base, current, {"goose": frozenset({_SOURCES})}
    )
    assert not changed
    assert restored == current


def test_rewind_skips_a_follows_edge_reported_as_already_matching() -> None:
    """A list-valued edge is not a match when the named node still differs."""

    def follows(rev: str) -> str:
        return json.dumps({
            "nodes": {
                "root": {"inputs": {"goose": ["codex"], "codex": "codex"}},
                "codex": {
                    "locked": {
                        "lastModified": 1,
                        "narHash": "sha256-aaaaaaaa",
                        "owner": "o",
                        "repo": "codex",
                        "rev": rev,
                        "type": "github",
                    },
                    "original": {"owner": "o", "repo": "codex", "type": "github"},
                },
                "goose": {
                    "locked": {
                        "lastModified": 1,
                        "narHash": "sha256-aaaaaaaa",
                        "owner": "o",
                        "repo": "goose",
                        "rev": rev,
                        "type": "github",
                    },
                    "original": {"owner": "o", "repo": "goose", "type": "github"},
                },
            },
            "root": "root",
            "version": 7,
        })

    base = _tree(**{
        "flake.nix": _flake("v1.55.0"),
        "flake.lock": follows(_BASE),
        _SOURCES: _entry(),
    })
    merged = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": follows(_PIN),
        _SOURCES: _entry(),
    })

    restored, changed = rewind_unpaired_inputs(
        base, merged, {"goose": frozenset({_SOURCES})}
    )

    assert not changed
    assert restored == merged


def test_rewind_treats_duplicate_and_undecodable_declarations_as_unspliceable() -> None:
    """A flake that does not yield one attrset is left untouched."""
    duplicate = _flake("v1.55.0") + _flake("v1.60.1")
    base = _tree(**{
        "flake.nix": duplicate,
        "flake.lock": _lock("goose"),
        _SOURCES: _entry(),
    })
    merged = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": _lock("goose", rev=_PIN),
        _SOURCES: _entry(),
    })
    assert rewind_unpaired_inputs(base, merged, {"goose": frozenset({_SOURCES})}) == (
        merged,
        False,
    )

    binary = dict(merged)
    binary["flake.nix"] = FileBlob("100644", b"\xff")
    assert rewind_unpaired_inputs(base, binary, {"goose": frozenset({_SOURCES})}) == (
        binary,
        False,
    )


def test_restore_helpers_refuse_a_tree_with_no_flake_files() -> None:
    """The splice helpers fail closed when the indexed tree has no flake."""
    assert _restore_declarations({}, {}, ("goose",)) is False
    assert _restore_lock({}, {}, ("goose",)) is False


def test_lock_node_ignores_a_document_that_is_not_a_node_map() -> None:
    """A list, or a lock whose nodes are not an object, does not name an input."""
    sources = _blob(_entry())
    listed = {
        "flake.nix": _blob(_flake("v1.60.1")),
        "flake.lock": _blob("[]"),
        _SOURCES: sources,
    }
    base = {
        "flake.nix": _blob(_flake("v1.55.0")),
        "flake.lock": _blob(json.dumps({"nodes": [], "root": "root", "version": 7})),
        _SOURCES: sources,
    }

    assert rewind_unpaired_inputs(base, listed, {"goose": frozenset({_SOURCES})}) == (
        listed,
        False,
    )


def test_owned_paths_name_goose_sources_and_skip_paths_outside_the_repo(
    tmp_path: Path,
) -> None:
    """Goose's source entry is owned by the goose input, and escapes are not."""
    assert _SOURCES in owned_paths_by_input(get_repo_root())["goose"]

    class _Escape(Updater):
        input_name = "goose"
        generated_artifact_files = ("../../../../../../tmp/outside.txt",)

    updater = tmp_path / "overlays" / "goose-cli" / "updater.py"
    updater.parent.mkdir(parents=True)
    relative = _owned_relative_paths(
        tmp_path,
        "goose-cli",
        _Escape,
        source_paths={},
        updater_paths={"goose-cli": updater},
    )

    assert _SOURCES in relative
    assert all(not path.startswith("..") for path in relative)
    assert _inside_repo(tmp_path, Path("/tmp/outside.txt")) is None
    assert owned_paths_by_input(tmp_path)["goose"]


def test_rewind_discards_a_tree_when_the_declaration_restore_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused declaration splice does not continue into the lock."""
    monkeypatch.setattr(
        "lib.update.ci.pin_sync._restore_declarations", lambda *_args: False
    )
    base = _tree(**{
        "flake.nix": _flake("v1.55.0"),
        "flake.lock": _lock("goose"),
        _SOURCES: _entry(),
    })
    merged = _tree(**{
        "flake.nix": _flake("v1.60.1"),
        "flake.lock": _lock("goose", rev=_PIN),
        _SOURCES: _entry(),
    })

    assert rewind_unpaired_inputs(base, merged, {"goose": frozenset({_SOURCES})}) == (
        merged,
        False,
    )


def _aliased_lock(*, pin: str, decoy: str) -> str:
    """Return a lock whose root ``rust-overlay`` edge names ``rust-overlay_2``."""
    node = {
        "lastModified": 1,
        "narHash": "sha256-aaaaaaaa",
        "owner": "o",
        "type": "github",
    }
    return json.dumps(
        {
            "nodes": {
                "root": {"inputs": {"rust-overlay": "rust-overlay_2"}},
                "rust-overlay_2": {
                    "locked": {**node, "repo": "rust-overlay", "rev": pin},
                    "original": {
                        "owner": "o",
                        "ref": "master",
                        "repo": "rust-overlay",
                        "type": "github",
                    },
                },
                "rust-overlay": {
                    "locked": {**node, "repo": "decoy", "rev": decoy},
                    "original": {"owner": "o", "repo": "decoy", "type": "github"},
                },
            },
            "root": "root",
            "version": 7,
        },
        indent=2,
        sort_keys=True,
    )


def _overlay_flake() -> str:
    return """{
  inputs = {
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      ref = "master";
      flake = false;
    };
  };
  outputs = { self, ... }: { };
}
"""


def test_rewind_follows_a_disambiguated_lock_node() -> None:
    """A root edge to ``rust-overlay_2`` is the pin, not the decoy node."""
    sources = "overlays/zed-editor-nightly/sources.json"
    entry = _entry()
    base = _tree(**{
        "flake.nix": _overlay_flake(),
        "flake.lock": _aliased_lock(pin=_BASE, decoy=_BASE),
        sources: entry,
    })
    moved = _tree(**{
        "flake.nix": _overlay_flake(),
        "flake.lock": _aliased_lock(pin=_PIN, decoy=_BASE),
        sources: entry,
    })

    restored, changed = rewind_unpaired_inputs(
        base, moved, {"rust-overlay": frozenset({sources})}
    )

    assert changed
    payload = json.loads(restored["flake.lock"].content)
    assert payload["nodes"]["root"]["inputs"]["rust-overlay"] == "rust-overlay_2"
    assert payload["nodes"]["rust-overlay_2"]["locked"]["rev"] == _BASE
    assert "rust-overlay" not in payload["nodes"]
    assert _declaration(restored["flake.nix"], "rust-overlay") == _declaration(
        base["flake.nix"], "rust-overlay"
    )


def test_rewind_ignores_a_node_the_root_edge_does_not_name() -> None:
    """A same-named node that the root edge does not use is not the pin."""
    sources = "overlays/zed-editor-nightly/sources.json"
    entry = _entry()
    base = _tree(**{
        "flake.nix": _overlay_flake(),
        "flake.lock": _aliased_lock(pin=_BASE, decoy=_BASE),
        sources: entry,
    })
    moved = _tree(**{
        "flake.nix": _overlay_flake(),
        "flake.lock": _aliased_lock(pin=_BASE, decoy=_PIN),
        sources: entry,
    })

    restored, changed = rewind_unpaired_inputs(
        base, moved, {"rust-overlay": frozenset({sources})}
    )

    assert not changed
    assert restored is moved


def test_rewind_reads_a_missing_or_unreadable_lock_node() -> None:
    """Comparing pins still inspects a lock that is absent or not a node map."""
    sources = _blob(_entry())
    nix = _blob(_flake("v1.55.0"))
    readable = _blob(_lock("goose"))
    base = {"flake.nix": nix, _SOURCES: sources, "flake.lock": readable}
    missing_flake = {"flake.lock": _blob(_lock("goose", rev=_PIN)), _SOURCES: sources}
    assert rewind_unpaired_inputs(
        base, missing_flake, {"goose": frozenset({_SOURCES})}
    ) == (missing_flake, False)

    missing_root = json.dumps({
        "nodes": {
            "codex": {
                "locked": {
                    "lastModified": 1,
                    "narHash": "sha256-aaaaaaaa",
                    "owner": "o",
                    "repo": "codex",
                    "rev": _PIN,
                    "type": "github",
                },
                "original": {"owner": "o", "repo": "codex", "type": "github"},
            }
        },
        "root": "root",
        "version": 7,
    }).encode()
    for content in (
        b"\xff",
        b"[]",
        b'{"nodes": [], "root": "root", "version": 7}',
        missing_root,
    ):
        merged = {
            "flake.nix": nix,
            _SOURCES: sources,
            "flake.lock": FileBlob("100644", content),
        }
        assert rewind_unpaired_inputs(
            {"flake.nix": nix, _SOURCES: sources},
            merged,
            {"goose": frozenset({_SOURCES})},
        ) == (merged, False)


def test_owned_paths_drop_files_outside_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source or crate path that leaves the repo is not evidence of a pin."""

    class _Escape(Updater):
        input_name = "goose"
        generated_artifact_files = ()

    monkeypatch.setitem(
        update_crate2nix.TARGETS,
        "escape-cli",
        SimpleNamespace(
            artifact_paths=(Path("/tmp/outside-crate.nix"), tmp_path / "inside.nix")
        ),
    )
    relative = _owned_relative_paths(
        tmp_path,
        "escape-cli",
        _Escape,
        source_paths={"escape-cli": Path("/tmp/outside-sources.json")},
        updater_paths={"escape-cli": tmp_path / "not-an-updater.txt"},
    )

    assert relative == frozenset({"inside.nix"})


_SUPERSET = "packages/superset/sources.json"
_BUN = "packages/superset/bun.nix"
_EXTRA = "packages/superset/extra.nix"
_INVENTORY = "packages/electron-runtimes/versions.json"
_CODEX = "packages/codex-cli/sources.json"


def _electron_entry(version: str, electron: str | None, commit: str) -> str:
    payload: dict[str, object] = {
        "hashes": [],
        "version": version,
        "commit": commit,
    }
    if electron is not None:
        payload["electronVersion"] = electron
    return json.dumps(payload)


def _inventory(*versions: str) -> str:
    return json.dumps({"schemaVersion": 1, "versions": list(versions)})


def _attr_flake(ref: str) -> str:
    return f"""{{
  inputs = {{
    superset = {{
      type = "github";
      owner = "superset-sh";
      repo = "superset";
      ref = "{ref}";
      flake = false;
    }};
    codex = {{
      type = "github";
      owner = "openai";
      repo = "codex";
      ref = "v1";
      flake = false;
    }};
  }};
  outputs = {{ self, ... }}: {{ }};
}}
"""


def _named_lock(revs: dict[str, str]) -> str:
    nodes: dict[str, object] = {
        "root": {"inputs": {name: name for name in revs}},
    }
    for name, rev in revs.items():
        nodes[name] = {
            "locked": {
                "lastModified": 1,
                "narHash": "sha256-aaaaaaaa",
                "owner": "o",
                "repo": name,
                "rev": rev,
                "type": "github",
            },
            "original": {"owner": "o", "ref": "v1", "repo": name, "type": "github"},
        }
    return json.dumps(
        {"nodes": nodes, "root": "root", "version": 7}, indent=2, sort_keys=True
    )


def test_rewind_restores_a_consumer_ahead_of_the_electron_inventory() -> None:
    """Update 44479330250: Superset 2.0.10 names Electron 44.10.0, inventory does not.

    The consumer's source files and its flake input return to the baseline.
    Codex, whose Electron is still packaged, keeps its candidate files. A
    file the candidate added under the consumer is removed with the rest.
    """
    inventory = _inventory("41.10.3", "44.9.3")
    base_sources = _electron_entry("1.30.2", "41.10.3", _BASE)
    pin_sources = _electron_entry("2.0.10", "44.10.0", _PIN)
    codex_base = _electron_entry("1.0.0", "44.9.3", _BASE)
    codex_pin = _electron_entry("1.1.0", "44.9.3", _PIN)
    owned = {
        "superset": frozenset({_SUPERSET, _BUN, _EXTRA, "sources.json"}),
        "codex": frozenset({_CODEX}),
    }
    base = _tree(**{
        "flake.nix": _attr_flake("v1.30.2"),
        "flake.lock": _named_lock({"superset": _BASE, "codex": _BASE}),
        _SUPERSET: base_sources,
        _BUN: "bun-base\n",
        "sources.json": _entry(),
        _INVENTORY: inventory,
        _CODEX: codex_base,
    })
    merged = _tree(**{
        "flake.nix": _attr_flake("v2.0.10"),
        "flake.lock": _named_lock({"superset": _PIN, "codex": _PIN}),
        _SUPERSET: pin_sources,
        _BUN: "bun-pin\n",
        _EXTRA: "added\n",
        "sources.json": _entry(),
        _INVENTORY: inventory,
        _CODEX: codex_pin,
    })

    restored, names = rewind_unpackaged_electron(base, merged, owned)

    assert names == ("superset",)
    assert restored[_SUPERSET].content == base[_SUPERSET].content
    assert restored[_BUN].content == base[_BUN].content
    assert _EXTRA not in restored
    assert restored["sources.json"].content == base["sources.json"].content
    assert restored[_CODEX].content == merged[_CODEX].content
    assert restored[_INVENTORY].content == merged[_INVENTORY].content
    assert input_declaration_text(
        restored["flake.nix"].content.decode(), "superset"
    ) == input_declaration_text(base["flake.nix"].content.decode(), "superset")
    assert input_declaration_text(
        restored["flake.nix"].content.decode(), "codex"
    ) == input_declaration_text(merged["flake.nix"].content.decode(), "codex")
    assert (
        json.loads(restored["flake.lock"].content)["nodes"]["superset"]["locked"]["rev"]
        == _BASE
    )
    assert (
        json.loads(restored["flake.lock"].content)["nodes"]["codex"]["locked"]["rev"]
        == _PIN
    )


def test_rewind_leaves_electron_consumers_the_inventory_can_still_build() -> None:
    """A packaged runtime, an unchanged entry, and a bad inventory all stay."""
    sources = _electron_entry("2.0.10", "44.10.0", _PIN)
    owned = {"superset": frozenset({_SUPERSET, _BUN})}
    base = _tree(**{
        "flake.nix": _attr_flake("v1.30.2"),
        "flake.lock": _named_lock({"superset": _BASE, "codex": _BASE}),
        _SUPERSET: sources,
        _BUN: "bun\n",
        _INVENTORY: _inventory("44.10.0"),
    })
    merged = _tree(**{
        "flake.nix": _attr_flake("v2.0.10"),
        "flake.lock": _named_lock({"superset": _PIN, "codex": _BASE}),
        _SUPERSET: sources,
        _BUN: "bun-pin\n",
        _INVENTORY: _inventory("44.10.0"),
    })

    restored, names = rewind_unpackaged_electron(base, merged, owned)

    assert names == ()
    assert restored is merged

    unchanged = _tree(**{
        "flake.nix": _attr_flake("v2.0.10"),
        "flake.lock": _named_lock({"superset": _PIN, "codex": _BASE}),
        _SUPERSET: sources,
        _INVENTORY: _inventory("41.10.3"),
    })
    assert rewind_unpackaged_electron(unchanged, unchanged, owned) == (unchanged, ())

    bare = dict(merged)
    bare[_SUPERSET] = _blob(_electron_entry("2.0.10", None, _PIN))
    bare[_INVENTORY] = _blob(_inventory())
    assert rewind_unpackaged_electron(base, bare, owned) == (bare, ())

    for content in (
        b"\xff",
        b"[",
        b"[]",
        b"null",
        b'{"versions": [1]}',
        b'{"versions": "44"}',
    ):
        broken = dict(merged)
        broken[_INVENTORY] = FileBlob("100644", content)
        broken[_SUPERSET] = _blob(sources)
        assert rewind_unpackaged_electron(base, broken, owned)[1] == ()

    missing = dict(merged)
    del missing[_INVENTORY]
    assert rewind_unpackaged_electron(base, missing, owned) == (missing, ())

    malformed = dict(merged)
    malformed[_SUPERSET] = _blob("{")
    malformed[_INVENTORY] = _blob(_inventory())
    assert rewind_unpackaged_electron(base, malformed, owned) == (malformed, ())

    absent = dict(merged)
    del absent[_SUPERSET]
    assert rewind_unpackaged_electron(
        base, absent, {"superset": frozenset({_SUPERSET})}
    ) == (absent, ())


def test_rewind_skips_an_unpackaged_electron_whose_declaration_cannot_splice() -> None:
    """A URL binding stays, and a lock that cannot roll back keeps the files."""
    url = '{ inputs.superset.url = "github:superset-sh/superset/v2.0.10"; }\n'
    base_url = '{ inputs.superset.url = "github:superset-sh/superset/v1.30.2"; }\n'
    sources = _electron_entry("2.0.10", "44.10.0", _PIN)
    base_sources = _electron_entry("1.30.2", "41.10.3", _BASE)
    owned = {"superset": frozenset({_SUPERSET})}
    base = _tree(**{
        "flake.nix": base_url,
        "flake.lock": _named_lock({"superset": _BASE}),
        _SUPERSET: base_sources,
        _INVENTORY: _inventory("41.10.3"),
    })
    merged = _tree(**{
        "flake.nix": url,
        "flake.lock": _named_lock({"superset": _PIN}),
        _SUPERSET: sources,
        _INVENTORY: _inventory("41.10.3"),
    })

    restored, names = rewind_unpackaged_electron(base, merged, owned)

    assert names == ()
    assert restored[_SUPERSET].content == merged[_SUPERSET].content

    unreadable = _tree(**{
        "flake.nix": _attr_flake("v2.0.10"),
        "flake.lock": _named_lock({"superset": _PIN, "codex": _BASE}),
        _SUPERSET: sources,
        _INVENTORY: _inventory("41.10.3"),
    })
    unreadable["flake.lock"] = FileBlob("100644", b"\xff")
    baseline = _tree(**{
        "flake.nix": _attr_flake("v1.30.2"),
        "flake.lock": _named_lock({"superset": _BASE, "codex": _BASE}),
        _SUPERSET: base_sources,
        _INVENTORY: _inventory("41.10.3"),
    })
    kept, kept_names = rewind_unpackaged_electron(baseline, unreadable, owned)
    assert kept_names == ()
    assert kept[_SUPERSET].content == sources.encode()


def test_rewind_discards_an_electron_restore_when_the_declaration_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused declaration splice does not leave the source files half-restored."""
    monkeypatch.setattr(
        "lib.update.ci.pin_sync._restore_declarations", lambda *_args: False
    )
    sources = _electron_entry("2.0.10", "44.10.0", _PIN)
    owned = {"superset": frozenset({_SUPERSET})}
    base = _tree(**{
        "flake.nix": _attr_flake("v1.30.2"),
        "flake.lock": _named_lock({"superset": _BASE, "codex": _BASE}),
        _SUPERSET: _electron_entry("1.30.2", "41.10.3", _BASE),
        _INVENTORY: _inventory("41.10.3"),
    })
    merged = _tree(**{
        "flake.nix": _attr_flake("v2.0.10"),
        "flake.lock": _named_lock({"superset": _PIN, "codex": _BASE}),
        _SUPERSET: sources,
        _INVENTORY: _inventory("41.10.3"),
    })

    restored, names = rewind_unpackaged_electron(base, merged, owned)

    assert names == ()
    assert restored is merged


def test_parsed_source_entry_rejects_malformed_payloads() -> None:
    """Evidence is a source entry, not any JSON document."""
    from lib.update.ci.pin_sync import _parsed_source_entry

    assert not _parsed_source_entry(None)
    assert not _parsed_source_entry(_blob("{"))
    assert not _parsed_source_entry(_blob("null"))
    assert not _parsed_source_entry(FileBlob("100644", b"\xff"))
    assert _parsed_source_entry(_blob(_entry()))
