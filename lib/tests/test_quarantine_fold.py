"""Native candidate folds keep every platform's quarantine hold.

A prepare that quarantines a package restores that package's files and
rolls its flake inputs back per input. A ref declared as an attribute set
is restored in place; any other ref reverts both flake files. Against the
baseline ancestor that restore looks like "unchanged", so the fold must
apply each candidate's recorded hold to every candidate before the 3-way
merge.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update.candidate import Candidate, git
from lib.update.ci import candidate as pipeline
from lib.update.flake_inputs import input_declaration_text
from lib.update.prepare_shard import PrepareShardCoverage, partition_prepare_targets
from lib.update.quarantine import QuarantineEntry, QuarantineHold, QuarantinePhase

_BASE_REVS = {"opencode": "a" * 40, "other": "b" * 40, "tool": "c" * 40}
# What prepare-pins locked; every native candidate extends it.
_PIN_REVS = {"opencode": "d" * 40, "other": "e" * 40, "tool": "f" * 40}
_DARWIN = ("aarch64-darwin",)
_ARM = ("aarch64-linux",)
_X86 = ("x86_64-linux",)


def _lock(**revs: str) -> str:
    """Return a Nix-rendered flake.lock with one github node per input."""
    nodes: dict[str, object] = {
        "root": {"inputs": {name: name for name in sorted(revs)}},
    }
    for name, rev in revs.items():
        nodes[name] = {
            "locked": {
                "lastModified": 1,
                "narHash": f"sha256-{rev[:8]}",
                "owner": "o",
                "repo": name,
                "rev": rev,
                "type": "github",
            },
            "original": {"owner": "o", "repo": name, "type": "github"},
        }
    return (
        json.dumps(
            {"nodes": nodes, "root": "root", "version": 7}, indent=2, sort_keys=True
        )
        + "\n"
    )


def _pins(**held: str) -> str:
    """Return the pin lock with each named input restored to its baseline rev."""
    return _lock(**{**_PIN_REVS, **{name: _BASE_REVS[name] for name in held}})


def _locked_revs(root: Path) -> dict[str, str]:
    lock = json.loads((root / "flake.lock").read_text(encoding="utf-8"))
    return {
        name: node["locked"]["rev"]
        for name, node in lock["nodes"].items()
        if name != "root"
    }


def _entry(package: str) -> QuarantineEntry:
    return QuarantineEntry(
        package=package,
        phase=QuarantinePhase.UPDATER,
        error_excerpt="nix store prefetch-file failed",
        since=datetime(2026, 11, 9, tzinfo=UTC),
    )


def _candidate(
    root: Path,
    files: dict[str, str],
    *,
    systems: tuple[str, ...],
    quarantined: tuple[str, ...] = (),
    hold: QuarantineHold | None = None,
) -> Candidate:
    """Return a prepared candidate that writes *files* over the baseline."""
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
        targets=(),
        sources=(),
        systems=systems,
        resolutions={},
        prepared=True,
        patch=patch,
        quarantines=tuple(_entry(name) for name in quarantined),
        quarantine_hold=QuarantineHold() if hold is None else hold,
    )


_OPENCODE = "overlays/opencode/sources.json"
_THORIUM = "packages/thorium/sources.json"
_PANTS = "packages/pants-preview/sources.json"


def _baseline(root: Path) -> None:
    init_update_workspace_repo(
        root,
        tracked_files={
            "flake.nix": "inputs: pinned\n",
            "flake.lock": _lock(**_BASE_REVS),
            _OPENCODE: "opencode baseline\n",
            _THORIUM: "thorium baseline\n",
            _PANTS: "pants baseline\n",
        },
    )


def test_fold_holds_a_lock_input_quarantined_on_only_some_platforms(
    tmp_path: Path,
) -> None:
    """Update 42578831763: Darwin and arm quarantined opencode, x86 updated it.

    Darwin and arm rolled the opencode input back inside the pin lock; x86
    kept the pin and hashed opencode. Every lock differs from the baseline
    and the x86 lock differs from the other two, so the fold raised
    ``Conflicting candidate edits for flake.lock``.
    """
    root = tmp_path / "repo"
    _baseline(root)
    hold = QuarantineHold(paths=(_OPENCODE,), inputs=("opencode",))
    darwin = _candidate(
        root,
        {"flake.lock": _pins(opencode="held"), "darwin.txt": "darwin\n"},
        systems=_DARWIN,
        quarantined=("opencode",),
        hold=hold,
    )
    arm = _candidate(
        root,
        {"flake.lock": _pins(opencode="held"), "arm.txt": "arm\n"},
        systems=_ARM,
        quarantined=("opencode",),
        hold=hold,
    )
    x86 = _candidate(
        root,
        {
            "flake.lock": _pins(),
            _OPENCODE: "opencode x86 hash\n",
            "x86.txt": "x86\n",
        },
        systems=_X86,
    )

    merged = pipeline.fold_prepared_candidates(darwin, arm, x86, repo=root)

    merged.apply(root)
    assert _locked_revs(root) == {**_PIN_REVS, "opencode": _BASE_REVS["opencode"]}
    assert (root / _OPENCODE).read_text(encoding="utf-8") == "opencode baseline\n"
    for name in ("darwin", "arm", "x86"):
        assert (root / f"{name}.txt").read_text(encoding="utf-8") == f"{name}\n"
    assert {entry.package for entry in merged.quarantines} == {"opencode"}
    assert merged.quarantine_hold == hold


def test_fold_holds_files_a_one_sided_edit_would_have_promoted(
    tmp_path: Path,
) -> None:
    """A restored file equals the baseline ancestor, so the other edit won.

    In the same run Darwin quarantined thorium while arm and x86 carried its
    pin bump, and arm quarantined pants-preview while Darwin hashed it. Both
    folded without error and promoted a package with missing hashes.
    """
    root = tmp_path / "repo"
    _baseline(root)
    darwin = _candidate(
        root,
        {_PANTS: "pants darwin hash\n"},
        systems=_DARWIN,
        quarantined=("thorium",),
        hold=QuarantineHold(paths=(_THORIUM,)),
    )
    arm = _candidate(
        root,
        {_THORIUM: "thorium pin bump\n"},
        systems=_ARM,
        quarantined=("pants-preview",),
        hold=QuarantineHold(paths=(_PANTS,)),
    )
    x86 = _candidate(
        root,
        {_THORIUM: "thorium pin bump\n", "x86.txt": "x86\n"},
        systems=_X86,
    )

    merged = pipeline.fold_prepared_candidates(darwin, arm, x86, repo=root)

    merged.apply(root)
    assert (root / _THORIUM).read_text(encoding="utf-8") == "thorium baseline\n"
    assert (root / _PANTS).read_text(encoding="utf-8") == "pants baseline\n"
    assert (root / "x86.txt").read_text(encoding="utf-8") == "x86\n"
    assert {entry.package for entry in merged.quarantines} == {
        "pants-preview",
        "thorium",
    }
    assert merged.quarantine_hold == QuarantineHold(paths=(_PANTS, _THORIUM))


def test_fold_keeps_different_lock_rollbacks_from_each_platform(
    tmp_path: Path,
) -> None:
    """Each platform may roll back a different input; every rollback survives."""
    root = tmp_path / "repo"
    _baseline(root)
    darwin = _candidate(
        root,
        {"flake.lock": _pins(tool="held"), "darwin.txt": "darwin\n"},
        systems=_DARWIN,
        quarantined=("tool",),
        hold=QuarantineHold(inputs=("tool",)),
    )
    arm = _candidate(
        root,
        {"flake.lock": _pins(), "arm.txt": "arm\n"},
        systems=_ARM,
    )
    x86 = _candidate(
        root,
        {"flake.lock": _pins(opencode="held"), "x86.txt": "x86\n"},
        systems=_X86,
        quarantined=("opencode",),
        hold=QuarantineHold(inputs=("opencode",)),
    )

    merged = pipeline.fold_prepared_candidates(darwin, arm, x86, repo=root)

    merged.apply(root)
    assert _locked_revs(root) == {
        "opencode": _BASE_REVS["opencode"],
        "other": _PIN_REVS["other"],
        "tool": _BASE_REVS["tool"],
    }
    for name in ("darwin", "arm", "x86"):
        assert (root / f"{name}.txt").read_text(encoding="utf-8") == f"{name}\n"


def test_fold_keeps_one_identical_lock_edit_from_every_platform(
    tmp_path: Path,
) -> None:
    """Three candidates that all carry the same pin lock fold to that lock."""
    root = tmp_path / "repo"
    _baseline(root)
    candidates = [
        _candidate(
            root, {"flake.lock": _pins(), f"{name}.txt": f"{name}\n"}, systems=systems
        )
        for name, systems in (("darwin", _DARWIN), ("arm", _ARM), ("x86", _X86))
    ]

    merged = pipeline.fold_prepared_candidates(*candidates, repo=root)

    merged.apply(root)
    assert _locked_revs(root) == _PIN_REVS
    assert merged.quarantine_hold == QuarantineHold()


@pytest.mark.parametrize("darwin_holds_opencode", [False, True])
def test_fold_rejects_lock_edits_no_hold_explains(
    tmp_path: Path,
    *,
    darwin_holds_opencode: bool,
) -> None:
    """Disagreeing input revisions still fail closed, with or without a hold."""
    root = tmp_path / "repo"
    _baseline(root)
    held = {"opencode": "held"} if darwin_holds_opencode else {}
    darwin = _candidate(
        root,
        {"flake.lock": _pins(**held)},
        systems=_DARWIN,
        hold=QuarantineHold(inputs=tuple(held)),
    )
    arm = _candidate(
        root,
        {"flake.lock": _lock(**{**_PIN_REVS, "other": "1" * 40})},
        systems=_ARM,
    )
    x86 = _candidate(
        root,
        {"flake.lock": _lock(**{**_PIN_REVS, "other": "2" * 40})},
        systems=_X86,
    )

    with pytest.raises(ValueError, match="Conflicting candidate edits for flake.lock"):
        pipeline.fold_prepared_candidates(darwin, arm, x86, repo=root)


def _input_flake(*, desktop: str, codex: str) -> str:
    """Return a flake whose ref inputs are attribute sets, like the real one."""
    return f"""{{
  inputs = {{
    github-desktop = {{
      type = "git";
      url = "https://github.com/desktop/desktop.git";
      ref = "{desktop}";
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


def test_fold_restores_one_ref_and_keeps_unrelated_crate_artifacts(
    tmp_path: Path,
) -> None:
    """Update 43221231963: a ref quarantine must not detach other crate pins.

    github-desktop failed on one Darwin shard. The whole-file flake hold then
    put every platform back on the baseline lock, while zed-editor-nightly's
    crate2nix artifacts stayed generated for the pin. Restoring only the
    quarantined ref keeps the zed pin and those artifacts together.
    """
    root = tmp_path / "repo"
    base_revs = {"github-desktop": "a" * 40, "codex": "b" * 40, "zed": "c" * 40}
    pin_revs = {"github-desktop": "d" * 40, "codex": "e" * 40, "zed": "f" * 40}
    baseline_nix = _input_flake(desktop="refs/tags/release-3.6.6", codex="v1")
    pinned_nix = _input_flake(desktop="refs/tags/release-3.6.10", codex="v2")
    init_update_workspace_repo(
        root,
        tracked_files={
            "flake.nix": baseline_nix,
            "flake.lock": _lock(**base_revs),
        },
    )
    pinned = {
        "flake.nix": pinned_nix,
        "flake.lock": _lock(**pin_revs),
        "packages/zed-editor-nightly/sources.json": '{"version": "1.30.0"}\n',
        "packages/zed-editor-nightly/Cargo.nix": "zed 1.30.0\n",
    }
    hold = QuarantineHold(inputs=("github-desktop",))
    darwin = _candidate(
        root,
        pinned,
        systems=_DARWIN,
        quarantined=("github-desktop",),
        hold=hold,
    )
    arm = _candidate(root, pinned, systems=_ARM)
    x86 = _candidate(root, pinned, systems=_X86)

    merged = pipeline.fold_prepared_candidates(darwin, arm, x86, repo=root)

    merged.apply(root)
    flake = (root / "flake.nix").read_text(encoding="utf-8")
    assert input_declaration_text(flake, "github-desktop") == input_declaration_text(
        baseline_nix, "github-desktop"
    )
    assert input_declaration_text(flake, "codex") == input_declaration_text(
        pinned_nix, "codex"
    )
    assert _locked_revs(root) == {
        **pin_revs,
        "github-desktop": base_revs["github-desktop"],
    }
    assert (root / "packages/zed-editor-nightly/Cargo.nix").read_text(
        encoding="utf-8"
    ) == "zed 1.30.0\n"
    assert merged.quarantine_hold == hold


def test_fold_carries_a_whole_flake_revert_to_every_platform(
    tmp_path: Path,
) -> None:
    """A ref-input failure reverts both flake files; the other platforms follow."""
    root = tmp_path / "repo"
    _baseline(root)
    darwin = _candidate(
        root,
        {"darwin.txt": "darwin\n"},
        systems=_DARWIN,
        quarantined=("tool",),
        hold=QuarantineHold(paths=("flake.lock", "flake.nix")),
    )
    pinned = {"flake.nix": "inputs: bumped\n", "flake.lock": _pins()}
    arm = _candidate(root, {**pinned, "arm.txt": "arm\n"}, systems=_ARM)
    x86 = _candidate(root, {**pinned, "x86.txt": "x86\n"}, systems=_X86)

    merged = pipeline.fold_prepared_candidates(darwin, arm, x86, repo=root)

    merged.apply(root)
    assert (root / "flake.nix").read_text(encoding="utf-8") == "inputs: pinned\n"
    assert _locked_revs(root) == _BASE_REVS
    assert (root / "x86.txt").read_text(encoding="utf-8") == "x86\n"


def test_same_platform_fold_keeps_each_shards_lock_rollback(tmp_path: Path) -> None:
    """Two Darwin shards that roll back different inputs both edit the seed lock."""
    root = tmp_path / "repo"
    _baseline(root)
    seed = _candidate(root, {"flake.lock": _pins()}, systems=())
    shards = (
        _candidate(
            root,
            {"flake.lock": _pins(tool="held"), "alpha.txt": "alpha\n"},
            systems=_DARWIN,
            quarantined=("tool",),
            hold=QuarantineHold(inputs=("tool",)),
        ),
        _candidate(
            root,
            {"flake.lock": _pins(opencode="held"), "beta.txt": "beta\n"},
            systems=_DARWIN,
            quarantined=("opencode",),
            hold=QuarantineHold(inputs=("opencode",)),
        ),
    )
    plan = ("alpha", "beta")
    coverages = tuple(
        PrepareShardCoverage(
            plan=plan,
            hashed=partition_prepare_targets(plan, shard=shard, count=2),
            shard=shard,
            count=2,
        )
        for shard in (0, 1)
    )

    merged = pipeline.merge_same_platform_partials(
        shards, coverages, seed=seed, repo=root
    )

    merged.apply(root)
    assert _locked_revs(root) == {
        "opencode": _BASE_REVS["opencode"],
        "other": _PIN_REVS["other"],
        "tool": _BASE_REVS["tool"],
    }
    assert merged.quarantine_hold == QuarantineHold(inputs=("opencode", "tool"))


def _blob(content: str) -> pipeline._IndexBlob:
    return pipeline._IndexBlob("100644", content.encode())


def test_held_index_files_restores_created_paths_and_leaves_unexplained_locks() -> None:
    """A held new file is dropped; a lock the hold cannot change is left as-is."""
    base = {"flake.lock": _blob(_lock(**_BASE_REVS))}
    moved_other = _blob(_lock(**{**_BASE_REVS, "other": _PIN_REVS["other"]}))
    files = {"flake.lock": moved_other, "packages/new/sources.json": _blob("new\n")}

    held = pipeline._held_index_files(
        files,
        base,
        QuarantineHold(paths=("packages/new/sources.json",), inputs=("opencode",)),
    )

    assert held == {"flake.lock": moved_other}
    assert pipeline._held_index_files(
        {"flake.lock": moved_other}, {}, QuarantineHold(inputs=("other",))
    ) == {"flake.lock": moved_other}


def test_held_declarations_restore_one_attrset_and_skip_unknown_inputs() -> None:
    """A hold splices the inputs the flake declares and leaves the others."""
    baseline = _input_flake(desktop="refs/tags/release-3.6.6", codex="v1")
    current = _input_flake(desktop="refs/tags/release-3.6.10", codex="v2")
    held = {"flake.nix": _blob(current)}
    base = {"flake.nix": _blob(baseline)}

    pipeline._restore_held_input_declarations(held, base, ("absent", "github-desktop"))

    text = held["flake.nix"].content.decode()
    assert input_declaration_text(text, "github-desktop") == input_declaration_text(
        baseline, "github-desktop"
    )
    assert input_declaration_text(text, "codex") == input_declaration_text(
        current, "codex"
    )


def test_held_declarations_leave_a_flake_they_cannot_splice() -> None:
    """An unreadable, symlinked, or non-attrset flake is not partially edited."""
    broken = pipeline._IndexBlob("100644", b"\xff")
    held = {"flake.nix": broken}
    pipeline._restore_held_input_declarations(
        held, {"flake.nix": _blob("{ }\n")}, ("github-desktop",)
    )
    assert held["flake.nix"] == broken

    link = pipeline._IndexBlob("120000", b"flake.nix")
    held = {"flake.nix": link}
    pipeline._restore_held_input_declarations(
        held, {"flake.nix": _blob("{ }\n")}, ("github-desktop",)
    )
    assert held["flake.nix"] == link

    bare = _blob('tool.ref = "v1";\n')
    held = {"flake.nix": bare}
    pipeline._restore_held_input_declarations(
        held, {"flake.nix": _blob("{ inputs = { }; }\n")}, ("github-desktop",)
    )
    assert held["flake.nix"] == bare


def test_candidate_without_a_recorded_hold_still_loads(tmp_path: Path) -> None:
    """Artifacts written before holds existed parse with an empty hold."""
    root = tmp_path / "repo"
    _baseline(root)
    payload = json.loads(
        _candidate(root, {"x86.txt": "x86\n"}, systems=_X86).model_dump_json()
    )
    del payload["quarantine_hold"]

    assert (
        Candidate.model_validate_json(json.dumps(payload)).quarantine_hold
        == QuarantineHold()
    )
