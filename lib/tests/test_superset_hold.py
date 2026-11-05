"""Superset pin hold: an unmaterialized bump must not survive candidate merge."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update.candidate import Candidate, ResolvedVersion, git
from lib.update.ci import candidate as pipeline
from lib.update.ci.superset_hold import (
    HOLD_EXCERPT,
    FileBlob,
    hold_unmaterialized_superset_pin,
)
from lib.update.quarantine import QuarantineEntry, QuarantinePhase

_HASH = {
    "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "hashType": "sha256",
    "platform": "x86_64-linux",
}
_OLD_REV = "a" * 40
_NEW_REV = "b" * 40
_OTHER_REV = "c" * 40
_OTHER_REV_BUMPED = "d" * 40


def _text(content: str) -> FileBlob:
    return FileBlob("100644", content.encode())


def _flake(*, superset_url: str, other_url: str) -> str:
    return (
        "{\n"
        "  inputs = {\n"
        "    other = {\n"
        f'      url = "{other_url}";\n'
        "      flake = false;\n"
        "    };\n"
        "    superset = {\n"
        f'      url = "{superset_url}";\n'
        "      flake = false;\n"
        "    };\n"
        "  };\n"
        "\n"
        "  outputs = { self, ... }: { };\n"
        "}\n"
    )


def _lock(
    *,
    superset_rev: str,
    other_rev: str = _OTHER_REV,
    superset_edge: str = "superset",
    root_inputs: dict[str, object] | None = None,
    root: object = "root",
) -> str:
    nodes: dict[str, object] = {
        "other": _github_node(other_rev, repo="other", ref="v1"),
        "superset": _github_node(
            superset_rev,
            repo="superset",
            ref="desktop-v1.30.2",
            flake=False,
        ),
        "root": {"inputs": root_inputs}
        if root_inputs is not None
        else {"inputs": {"other": "other", "superset": superset_edge}},
    }
    if superset_edge != "superset":
        nodes[superset_edge] = _github_node(
            _NEW_REV,
            repo="superset",
            ref="desktop-v2.0.5",
            flake=False,
        )
    return (
        json.dumps(
            {"nodes": nodes, "root": root, "version": 7},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _github_node(
    rev: str,
    *,
    repo: str,
    ref: str,
    flake: bool | None = None,
) -> dict[str, object]:
    node: dict[str, object] = {
        "locked": {
            "lastModified": 1,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "owner": "owner",
            "repo": repo,
            "rev": rev,
            "type": "github",
        },
        "original": {
            "owner": "owner",
            "ref": ref,
            "repo": repo,
            "type": "github",
        },
    }
    if flake is not None:
        node["flake"] = flake
    return node


def _sources(*, version: str, commit: str, hashes: object) -> str:
    return (
        json.dumps(
            {"commit": commit, "hashes": hashes, "version": version},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _tree(
    *,
    superset_url: str = "github:superset-sh/superset/desktop-v1.30.2",
    other_url: str = "github:o/other/v1",
    superset_rev: str = _OLD_REV,
    other_rev: str = _OTHER_REV,
    version: str = "1.30.2",
    commit: str = _OLD_REV,
    hashes: object | None = None,
    bun_nix: str = "old workspace graph\n",
    bun_lock: str | None = "old lock\n",
    lock: str | None = None,
    flake: str | None = None,
    superset_edge: str = "superset",
    root_inputs: dict[str, object] | None = None,
    root: object = "root",
) -> dict[str, FileBlob]:
    files = {
        "packages/superset/sources.json": _text(
            _sources(
                version=version,
                commit=commit,
                hashes=[_HASH] if hashes is None else hashes,
            )
        ),
        "packages/superset/bun.nix": _text(bun_nix),
        "flake.nix": _text(
            flake
            if flake is not None
            else _flake(superset_url=superset_url, other_url=other_url)
        ),
        "flake.lock": _text(
            lock
            if lock is not None
            else _lock(
                superset_rev=superset_rev,
                other_rev=other_rev,
                superset_edge=superset_edge,
                root_inputs=root_inputs,
                root=root,
            )
        ),
    }
    if bun_lock is not None:
        files["packages/superset/bun.lock"] = _text(bun_lock)
    return files


def test_hold_ignores_trees_without_superset_sources() -> None:
    """Ordinary package merges do not consult the Superset pin."""
    base = {"keep.txt": _text("keep\n")}
    merged = {"keep.txt": _text("changed\n")}

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert restored is merged
    assert held is False


def test_hold_restores_an_unmaterialized_pin_and_keeps_sibling_inputs() -> None:
    """Empty hashes plus a moved input return only Superset to the baseline."""
    base = _tree()
    merged = _tree(
        superset_url="github:superset-sh/superset/desktop-v2.0.5",
        other_url="github:o/other/v2",
        superset_rev=_NEW_REV,
        other_rev=_OTHER_REV_BUMPED,
        version="2.0.5",
        commit=_NEW_REV,
        hashes=[],
        bun_lock="new lock\n",
    )

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is True
    assert (
        restored["packages/superset/sources.json"]
        == base["packages/superset/sources.json"]
    )
    assert restored["packages/superset/bun.nix"] == base["packages/superset/bun.nix"]
    assert restored["packages/superset/bun.lock"] == base["packages/superset/bun.lock"]
    assert (
        'url = "github:superset-sh/superset/desktop-v1.30.2"'
        in restored["flake.nix"].content.decode()
    )
    assert 'url = "github:o/other/v2"' in restored["flake.nix"].content.decode()
    lock = json.loads(restored["flake.lock"].content)
    assert lock["nodes"]["superset"]["locked"]["rev"] == _OLD_REV
    assert lock["nodes"]["other"]["locked"]["rev"] == _OTHER_REV_BUMPED


def test_hold_keeps_a_materialized_bump_with_a_regenerated_graph() -> None:
    """Hashes and a new bun.nix are the promotable form of a Superset bump."""
    base = _tree()
    merged = _tree(
        superset_url="github:superset-sh/superset/desktop-v2.0.5",
        superset_rev=_NEW_REV,
        version="2.0.5",
        commit=_NEW_REV,
        bun_nix="workspace graph without apps/admin\n",
    )

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is False
    assert restored == merged


def test_hold_restores_a_moved_input_whose_graph_was_not_regenerated() -> None:
    """A flake input that moved without bun.nix is the eval-breaking pair."""
    base = _tree()
    merged = _tree(superset_url="github:superset-sh/superset/desktop-v2.0.5")

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is True
    assert restored["flake.nix"] == base["flake.nix"]
    assert restored["flake.lock"] is merged["flake.lock"]
    assert (
        restored["packages/superset/sources.json"]
        == merged["packages/superset/sources.json"]
    )


def test_hold_treats_empty_and_invalid_hash_payloads_as_unmaterialized() -> None:
    """A pin with no real hash list is not a finished native materialization."""
    base = _tree()
    empty_mapping = _tree(version="2.0.5", commit=_NEW_REV, hashes={})
    invalid = _tree(version="2.0.5", commit=_NEW_REV, hashes="sha256-nope")
    not_object = _tree()
    not_object["packages/superset/sources.json"] = _text("[]\n")
    broken = _tree()
    broken["packages/superset/sources.json"] = _text("{")

    assert hold_unmaterialized_superset_pin(base, empty_mapping)[1] is True
    assert hold_unmaterialized_superset_pin(base, invalid)[1] is True
    assert hold_unmaterialized_superset_pin(base, not_object)[1] is True
    assert hold_unmaterialized_superset_pin(base, broken)[1] is True


def test_hold_rejects_a_lock_that_cannot_name_superset() -> None:
    """Fail closed when the baseline lock cannot supply the last-good node."""
    base = _tree()
    base["flake.lock"] = _text('{"nodes": {}, "root": "root", "version": 7}\n')
    merged = _tree(version="2.0.5", commit=_NEW_REV, hashes=[])

    with pytest.raises(ValueError, match="no superset node"):
        hold_unmaterialized_superset_pin(base, merged)


def test_hold_rejects_a_malformed_lock() -> None:
    """A lock that is not an object cannot hide a moved input."""
    base = _tree()
    merged = _tree()
    merged["flake.lock"] = _text("[]\n")

    with pytest.raises(TypeError, match="could not read flake.lock"):
        hold_unmaterialized_superset_pin(base, merged)


def test_hold_rejects_a_flake_that_lost_the_superset_input() -> None:
    """Restoring the pin requires the declaration the baseline still has."""
    base = _tree()
    merged = _tree(
        version="2.0.5", commit=_NEW_REV, hashes=[], flake="{ inputs = { }; }\n"
    )

    with pytest.raises(ValueError, match="Could not find flake input attrset"):
        hold_unmaterialized_superset_pin(base, merged)


def test_hold_without_flake_files_restores_package_bytes() -> None:
    """Package files still return to baseline when the candidate has no flake."""
    base = _tree(bun_lock=None)
    del base["flake.nix"]
    del base["flake.lock"]
    merged = _tree(version="2.0.5", commit=_NEW_REV, hashes=[], bun_lock="partial\n")
    del merged["flake.nix"]
    del merged["flake.lock"]

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is True
    assert "packages/superset/bun.lock" not in restored
    assert (
        restored["packages/superset/sources.json"]
        == base["packages/superset/sources.json"]
    )


def test_hold_restores_a_string_edge_and_leaves_a_baseline_follow() -> None:
    """A baseline node name is written back; a follow path is not copied over."""
    base = _tree()
    redirected = _tree(
        version="2.0.5",
        commit=_NEW_REV,
        hashes=[],
        superset_edge="superset-bumped",
    )
    follow_base = _tree()
    follow_base["flake.lock"] = _lock_with_edge(follow_base, ["nixpkgs", "superset"])
    follow = _tree(
        version="2.0.5", commit=_NEW_REV, hashes=[], superset_edge="superset-bumped"
    )

    redirected_result, redirected_held = hold_unmaterialized_superset_pin(
        base, redirected
    )
    follow_result, follow_held = hold_unmaterialized_superset_pin(follow_base, follow)

    assert redirected_held is True
    redirected_lock = json.loads(redirected_result["flake.lock"].content)
    assert redirected_lock["nodes"]["root"]["inputs"]["superset"] == "superset"
    assert redirected_lock["nodes"]["superset"]["locked"]["rev"] == _OLD_REV
    assert follow_held is True
    follow_node = json.loads(follow_result["flake.lock"].content)
    assert follow_node["nodes"]["root"]["inputs"]["superset"] == "superset-bumped"


def test_hold_skips_a_lock_root_that_has_no_input_map() -> None:
    """The node restore still applies when the root cannot name an edge."""
    base = _tree(root_inputs=None, root=1)
    # root=1 makes _lock() set "root": 1 while the node key stays "root".
    merged = _tree(
        version="2.0.5", commit=_NEW_REV, hashes=[], root=1, root_inputs=None
    )
    merged_lock = json.loads(merged["flake.lock"].content)
    merged_lock["nodes"]["superset"]["locked"]["rev"] = _NEW_REV
    merged["flake.lock"] = _text(
        json.dumps(merged_lock, indent=2, sort_keys=True) + "\n"
    )

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is True
    assert (
        json.loads(restored["flake.lock"].content)["nodes"]["superset"]["locked"]["rev"]
        == _OLD_REV
    )


def _lock_with_edge(tree: dict[str, FileBlob], edge: object) -> FileBlob:
    lock = json.loads(tree["flake.lock"].content)
    lock["nodes"]["root"]["inputs"]["superset"] = edge
    return _text(json.dumps(lock, indent=2, sort_keys=True) + "\n")


def test_hold_ignores_a_tree_that_already_matches() -> None:
    """An unchanged materialized pin is not a hold."""
    base = _tree()
    merged = _tree()

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is False
    assert restored is merged


def test_hold_ignores_one_sided_sources() -> None:
    """The hold applies only when both trees carry sources.json."""
    base = _tree()
    other = {"keep.txt": _text("keep\n")}

    assert hold_unmaterialized_superset_pin(base, other)[1] is False
    assert hold_unmaterialized_superset_pin(other, base)[1] is False


def test_hold_restores_a_lock_node_when_the_attrset_is_unchanged() -> None:
    """A moved lock node with the old bun.nix is the same unevaluable pair."""
    base = _tree()
    merged = _tree(superset_rev=_NEW_REV)

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is True
    assert (
        json.loads(restored["flake.lock"].content)["nodes"]["superset"]["locked"]["rev"]
        == _OLD_REV
    )


def test_hold_keeps_materialized_sources_when_the_input_did_not_move() -> None:
    """A hash dict is materialized even when bun.nix is still the baseline graph."""
    base = _tree()
    merged = _tree(version="2.0.5", commit=_NEW_REV, hashes={"x86_64-linux": _HASH})

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is False
    assert restored is merged


def test_equal_sources_without_flake_files_are_not_a_moved_input() -> None:
    """Missing flake files are not a movement when both sides lack them."""
    base = _tree()
    merged = _tree()
    for tree in (base, merged):
        del tree["flake.nix"]
        del tree["flake.lock"]

    restored, held = hold_unmaterialized_superset_pin(base, merged)

    assert held is False
    assert restored is merged


def test_hold_skips_flake_restore_when_one_side_has_no_flake() -> None:
    """A side without flake files cannot splice an input or a lock node."""
    base = _tree(bun_lock=None)
    merged = _tree(version="2.0.5", commit=_NEW_REV, hashes=[])
    del merged["flake.nix"]
    del merged["flake.lock"]
    only_current = _tree(
        superset_url="github:superset-sh/superset/desktop-v2.0.5",
        version="2.0.5",
        commit=_NEW_REV,
        hashes=[],
    )
    only_base = _tree(bun_lock=None)
    del only_base["flake.nix"]
    del only_base["flake.lock"]

    missing_current, held_current = hold_unmaterialized_superset_pin(base, merged)
    missing_base, held_base = hold_unmaterialized_superset_pin(only_base, only_current)

    assert held_current is True
    assert "flake.nix" not in missing_current
    assert held_base is True
    assert "desktop-v2.0.5" in missing_base["flake.nix"].content.decode()
    assert (
        missing_base["packages/superset/sources.json"]
        == only_base["packages/superset/sources.json"]
    )


def test_hold_rejects_a_lock_whose_nodes_are_not_a_map() -> None:
    """A lock nodes value that is not an object cannot hide a moved input."""
    base = _tree()
    merged = _tree()
    merged["flake.lock"] = _text('{"nodes": [], "root": "root", "version": 7}\n')

    with pytest.raises(TypeError, match="could not read flake.lock nodes"):
        hold_unmaterialized_superset_pin(base, merged)


def test_hold_skips_a_root_that_cannot_name_inputs() -> None:
    """A non-map root or a non-map inputs value leaves the edge untouched."""
    base = _tree()
    scalar_root = _tree(version="2.0.5", commit=_NEW_REV, hashes=[])
    scalar_lock = json.loads(scalar_root["flake.lock"].content)
    scalar_lock["nodes"]["root"] = "scalar"
    scalar_root["flake.lock"] = _text(
        json.dumps(scalar_lock, indent=2, sort_keys=True) + "\n"
    )
    bad_inputs = _tree(version="2.0.5", commit=_NEW_REV, hashes=[])
    bad_lock = json.loads(bad_inputs["flake.lock"].content)
    bad_lock["nodes"]["root"] = {"inputs": "nope"}
    bad_inputs["flake.lock"] = _text(
        json.dumps(bad_lock, indent=2, sort_keys=True) + "\n"
    )

    scalar_result, scalar_held = hold_unmaterialized_superset_pin(base, scalar_root)
    inputs_result, inputs_held = hold_unmaterialized_superset_pin(base, bad_inputs)

    assert scalar_held is True
    assert json.loads(scalar_result["flake.lock"].content)["nodes"]["root"] == "scalar"
    assert inputs_held is True
    assert (
        json.loads(inputs_result["flake.lock"].content)["nodes"]["root"]["inputs"]
        == "nope"
    )
    assert (
        json.loads(scalar_result["flake.lock"].content)["nodes"]["superset"]["locked"][
            "rev"
        ]
        == _OLD_REV
    )


def test_merge_holds_the_pin_a_reverted_platform_cannot_outrank(tmp_path: Path) -> None:
    """A platform that only inherited the pin loses to the baseline hold."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files=_tracked_baseline())
    pin = _candidate(
        root,
        {
            "flake.nix": _flake(
                superset_url="github:superset-sh/superset/desktop-v2.0.5",
                other_url="github:o/other/v2",
            ),
            "flake.lock": _lock(superset_rev=_NEW_REV, other_rev=_OTHER_REV_BUMPED),
            "packages/superset/sources.json": _sources(
                version="2.0.5",
                commit=_NEW_REV,
                hashes=[],
            ),
        },
        systems=("aarch64-darwin", "aarch64-linux"),
        resolutions={"superset": ResolvedVersion(version="2.0.5")},
    )
    other = _candidate(
        root,
        {"arm.txt": "arm\n"},
        systems=("aarch64-darwin", "x86_64-linux"),
    )

    merged = pipeline.merge_prepared_candidates(pin, other, repo=root)

    merged.apply(root)
    assert "desktop-v1.30.2" in (root / "flake.nix").read_text(encoding="utf-8")
    assert "github:o/other/v2" in (root / "flake.nix").read_text(encoding="utf-8")
    assert json.loads((root / "packages/superset/sources.json").read_text())[
        "version"
    ] == ("1.30.2")
    assert (root / "arm.txt").read_text(encoding="utf-8") == "arm\n"
    assert "superset" not in merged.resolutions
    assert any(
        entry.package == "superset" and HOLD_EXCERPT in entry.error_excerpt
        for entry in merged.quarantines
    )


def test_merge_keeps_an_updater_quarantine_instead_of_adding_another(
    tmp_path: Path,
) -> None:
    """A native hash failure already names superset; the hold does not duplicate it."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files=_tracked_baseline())
    existing = QuarantineEntry(
        package="superset",
        phase=QuarantinePhase.UPDATER,
        error_excerpt="nix hash path failed after auto-GC",
        since=datetime(2026, 11, 5, tzinfo=UTC),
    )
    pin = _candidate(
        root,
        _unmaterialized_pin_files(),
        systems=("aarch64-darwin", "aarch64-linux"),
        quarantines=(existing,),
    )
    other = _candidate(
        root,
        {"arm.txt": "arm\n"},
        systems=("aarch64-darwin", "x86_64-linux"),
    )

    merged = pipeline.merge_prepared_candidates(pin, other, repo=root)

    superset = [entry for entry in merged.quarantines if entry.package == "superset"]
    assert len(superset) == 1
    assert superset[0].error_excerpt == "nix hash path failed after auto-GC"
    assert "desktop-v1.30.2" in merged_text(merged, root, "flake.nix")


def test_merge_records_the_hold_beside_another_package_quarantine(
    tmp_path: Path,
) -> None:
    """A sibling quarantine does not hide the Superset pin hold."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files=_tracked_baseline())
    pin = _candidate(
        root,
        _unmaterialized_pin_files(),
        systems=("aarch64-darwin", "aarch64-linux"),
        quarantines=(
            QuarantineEntry(
                package="unsloth",
                phase=QuarantinePhase.BUILD,
                error_excerpt="unsloth build failed",
                since=datetime(2026, 11, 5, tzinfo=UTC),
            ),
        ),
    )
    other = _candidate(
        root,
        {"arm.txt": "arm\n"},
        systems=("aarch64-darwin", "x86_64-linux"),
    )

    merged = pipeline.merge_prepared_candidates(pin, other, repo=root)

    assert {entry.package for entry in merged.quarantines} == {"superset", "unsloth"}
    assert any(HOLD_EXCERPT in entry.error_excerpt for entry in merged.quarantines)


def test_merge_promotes_a_materialized_superset_bump(tmp_path: Path) -> None:
    """Hashes plus a regenerated bun.nix are the bump the hold must let through."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files=_tracked_baseline())
    pin = _candidate(
        root,
        {
            **_unmaterialized_pin_files(),
            "packages/superset/sources.json": _sources(
                version="2.0.5",
                commit=_NEW_REV,
                hashes=[_HASH],
            ),
            "packages/superset/bun.nix": "workspace graph without apps/admin\n",
        },
        systems=("aarch64-darwin", "aarch64-linux"),
        resolutions={"superset": ResolvedVersion(version="2.0.5")},
    )
    other = _candidate(
        root,
        {"arm.txt": "arm\n"},
        systems=("aarch64-darwin", "x86_64-linux"),
    )

    merged = pipeline.merge_prepared_candidates(pin, other, repo=root)

    merged.apply(root)
    assert "desktop-v2.0.5" in (root / "flake.nix").read_text(encoding="utf-8")
    assert (
        json.loads((root / "packages/superset/sources.json").read_text())["version"]
        == "2.0.5"
    )
    assert (root / "packages/superset/bun.nix").read_text(
        encoding="utf-8"
    ) == "workspace graph without apps/admin\n"
    assert merged.resolutions["superset"].version == "2.0.5"
    assert merged.quarantines == ()


def _tracked_baseline() -> dict[str, str]:
    return {
        "flake.nix": _flake(
            superset_url="github:superset-sh/superset/desktop-v1.30.2",
            other_url="github:o/other/v1",
        ),
        "flake.lock": _lock(superset_rev=_OLD_REV),
        "packages/superset/sources.json": _sources(
            version="1.30.2",
            commit=_OLD_REV,
            hashes=[_HASH],
        ),
        "packages/superset/bun.nix": "old workspace graph\n",
        "packages/superset/bun.lock": "old lock\n",
        "keep.txt": "keep\n",
    }


def _unmaterialized_pin_files() -> dict[str, str]:
    return {
        "flake.nix": _flake(
            superset_url="github:superset-sh/superset/desktop-v2.0.5",
            other_url="github:o/other/v2",
        ),
        "flake.lock": _lock(superset_rev=_NEW_REV, other_rev=_OTHER_REV_BUMPED),
        "packages/superset/sources.json": _sources(
            version="2.0.5",
            commit=_NEW_REV,
            hashes=[],
        ),
    }


def merged_text(merged: Candidate, root: Path, relative: str) -> str:
    """Apply one candidate and return a tracked file."""
    merged.apply(root)
    return (root / relative).read_text(encoding="utf-8")


def _candidate(
    root: Path,
    files: dict[str, str],
    *,
    systems: tuple[str, ...],
    resolutions: dict[str, ResolvedVersion] | None = None,
    quarantines: tuple[QuarantineEntry, ...] = (),
) -> Candidate:
    base = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        git(root, "add", "--", relative)
    tree = git(root, "write-tree").decode().strip()
    patch = git(
        root,
        "diff",
        "--cached",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
    )
    git(root, "reset", "--hard", "HEAD")
    return Candidate(
        base_tree=base,
        tree=tree,
        targets=("example",),
        sources=("example",),
        systems=systems,
        resolutions={} if resolutions is None else resolutions,
        prepared=True,
        patch=patch,
        quarantines=quarantines,
    )
