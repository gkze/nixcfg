"""Darwin prepare slices stay disjoint and still hash every plan name once."""

from pathlib import Path
from typing import cast

import pytest

from lib.tests._run_updates_helpers import make_run_plan
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update.candidate import Candidate, Preparation, ResolvedVersion, git
from lib.update.ci import candidate as pipeline
from lib.update.cli import _apply_prepare_shard, _RunPlanError
from lib.update.planner import (
    aggregate_source_members,
    prepare_plan_prerequisites,
)
from lib.update.prepare_shard import (
    PrepareShardCoverage,
    coverage_from_json,
    hosted_prepare_shard,
    partition_prepare_targets,
    require_exact_prepare_coverage,
)
from lib.update.refs import FlakeInputRef
from lib.update.updaters import ensure_updaters_loaded

_PLAN = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")


def _coverage(
    shard: int,
    count: int = 2,
    plan: tuple[str, ...] = ("alpha", "beta"),
    hashed: tuple[str, ...] | None = None,
) -> PrepareShardCoverage:
    if hashed is None:
        hashed = partition_prepare_targets(plan, shard=shard, count=count)
    return PrepareShardCoverage(plan=plan, hashed=hashed, shard=shard, count=count)


def _file_candidate(
    root: Path,
    relative: str,
    content: str,
    *,
    systems: tuple[str, ...],
) -> Candidate:
    base = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
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
        sources=(Path(relative).stem,),
        systems=systems,
        resolutions={},
        prepared=True,
        patch=patch,
    )


def _files_candidate(
    root: Path,
    files: dict[str, str],
    *,
    systems: tuple[str, ...],
) -> Candidate:
    """Return one candidate that writes every *files* entry over HEAD."""
    first, *rest = files.items()
    for relative, content in rest:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        git(root, "add", "--", relative)
    return _file_candidate(root, first[0], first[1], systems=systems)


def test_partition_assigns_each_plan_name_once() -> None:
    """Independent names pack largest-first, and each shard stays in plan order."""
    assert partition_prepare_targets(_PLAN, shard=0, count=5) == ("alpha", "zeta")
    assert partition_prepare_targets(_PLAN, shard=1, count=5) == ("beta",)
    assert partition_prepare_targets((), shard=0, count=5) == ()
    assert partition_prepare_targets(("m", "a"), shard=0, count=2) == ("a",)
    assert partition_prepare_targets(("m", "a"), shard=1, count=2) == ("m",)
    seen: list[str] = []
    for shard in range(5):
        seen.extend(partition_prepare_targets(_PLAN, shard=shard, count=5))
    assert sorted(seen) == sorted(_PLAN)
    assert len(seen) == len(set(seen)) == len(_PLAN)


def test_partition_keeps_a_prerequisite_component_on_one_shard() -> None:
    """A parent outside the plan does not join, and the chain is not index-split."""
    plan = ("c", "a", "b", "d")
    edges = {"b": ("a",), "c": ("b",), "d": ("missing",)}
    assert partition_prepare_targets(plan, shard=0, count=2, prerequisites=edges) == (
        "c",
        "a",
        "b",
    )
    assert partition_prepare_targets(plan, shard=1, count=2, prerequisites=edges) == (
        "d",
    )
    assert partition_prepare_targets(
        ("b", "a"), shard=0, count=1, prerequisites={"b": ("a",)}
    ) == ("b", "a")
    whole = {
        name: shard
        for shard in range(5)
        for name in partition_prepare_targets(
            ("electron-runtimes", "goose-cli", "goose-desktop"),
            shard=shard,
            count=5,
            prerequisites={
                "goose-desktop": ("goose-cli",),
                "electron-runtimes": ("goose-desktop",),
            },
        )
    }
    assert whole["goose-cli"] == whole["goose-desktop"] == whole["electron-runtimes"]


def test_partition_rejects_a_slot_that_cannot_hash() -> None:
    """Bools, non-integers, empty counts, and duplicate names fail closed."""
    bad_bool = True
    with pytest.raises(TypeError, match="integers"):
        partition_prepare_targets((), shard=cast("int", bad_bool), count=5)
    with pytest.raises(TypeError, match="integers"):
        partition_prepare_targets((), shard=0, count=cast("int", bad_bool))
    with pytest.raises(ValueError, match="positive"):
        partition_prepare_targets((), shard=0, count=0)
    with pytest.raises(ValueError, match="outside"):
        partition_prepare_targets((), shard=5, count=5)
    with pytest.raises(ValueError, match="unique"):
        partition_prepare_targets(("alpha", "alpha"), shard=0, count=1)


def test_hosted_prepare_shard_is_the_public_pool_or_unset() -> None:
    """Actions passes canonical integers, and the count stays five."""
    assert hosted_prepare_shard("", "") is None
    assert hosted_prepare_shard("0", "5") == (0, 5)
    assert hosted_prepare_shard("4", "5") == (4, 5)
    with pytest.raises(ValueError, match="both be set"):
        hosted_prepare_shard("0", "")
    with pytest.raises(ValueError, match="both be set"):
        hosted_prepare_shard("", "5")
    with pytest.raises(ValueError, match="integers"):
        hosted_prepare_shard("é", "5")
    with pytest.raises(ValueError, match="integers"):
        hosted_prepare_shard("x", "5")
    with pytest.raises(ValueError, match="integers"):
        hosted_prepare_shard("0", "x")
    with pytest.raises(ValueError, match="integers"):
        hosted_prepare_shard("01", "5")
    with pytest.raises(ValueError, match="public macOS pool"):
        hosted_prepare_shard("0", "4")
    with pytest.raises(ValueError, match="outside"):
        hosted_prepare_shard("5", "5")


def test_coverage_sidecar_rejects_anything_but_the_pure_slice() -> None:
    """The sidecar is the partition, not a free-form list of hashed names."""
    record = _coverage(0)
    restored = coverage_from_json(record.to_json())
    assert restored == record
    with pytest.raises(ValueError, match="JSON"):
        coverage_from_json("{")
    with pytest.raises(ValueError, match="plan, hashed"):
        coverage_from_json("[]")
    with pytest.raises(ValueError, match="plan, hashed"):
        coverage_from_json('{"plan": [], "hashed": [], "shard": 0}')
    payload = record.to_json().replace('"shard": 0', '"shard": true', 1)
    with pytest.raises(TypeError, match="integers"):
        coverage_from_json(payload)
    payload = record.to_json().replace('"count": 2', '"count": true', 1)
    with pytest.raises(TypeError, match="integers"):
        coverage_from_json(payload)
    payload = record.to_json().replace('"shard": 0', '"shard": "0"', 1)
    with pytest.raises(TypeError, match="integers"):
        coverage_from_json(payload)
    payload = record.to_json().replace('"count": 2', '"count": "2"', 1)
    with pytest.raises(TypeError, match="integers"):
        coverage_from_json(payload)
    with pytest.raises(ValueError, match="plan must be a list"):
        coverage_from_json('{"plan": "alpha", "hashed": [], "shard": 0, "count": 1}')
    with pytest.raises(ValueError, match="hashed must be a list"):
        coverage_from_json('{"plan": ["alpha"], "hashed": [1], "shard": 0, "count": 1}')
    with pytest.raises(ValueError, match="different slice"):
        coverage_from_json(
            '{"plan": ["alpha", "beta"], "hashed": ["beta"], "shard": 0, "count": 2}'
        )
    with pytest.raises(ValueError, match="unique"):
        coverage_from_json(
            '{"plan": ["alpha", "alpha"], "hashed": ["alpha"], "shard": 0, "count": 1}'
        )


def test_exact_coverage_requires_every_shard_of_one_plan() -> None:
    """Missing, duplicate, or drifted slices cannot authorize the fold."""
    records = [_coverage(0), _coverage(1)]
    assert require_exact_prepare_coverage(records) == ("alpha", "beta")
    with pytest.raises(ValueError, match="empty"):
        require_exact_prepare_coverage(())
    with pytest.raises(ValueError, match="counts must match"):
        require_exact_prepare_coverage((
            records[0],
            _coverage(0, count=1, plan=("alpha",)),
        ))
    with pytest.raises(ValueError, match="plans must match"):
        require_exact_prepare_coverage((
            records[0],
            _coverage(1, plan=("alpha", "other")),
        ))
    with pytest.raises(ValueError, match="duplicated"):
        require_exact_prepare_coverage((records[0], records[0]))
    with pytest.raises(ValueError, match="more than once"):
        require_exact_prepare_coverage((
            records[0],
            PrepareShardCoverage(
                plan=("alpha", "beta"),
                hashed=("alpha",),
                shard=1,
                count=2,
            ),
        ))
    with pytest.raises(ValueError, match="missing a shard"):
        require_exact_prepare_coverage((records[0],))
    duplicated = (
        PrepareShardCoverage(
            plan=("alpha", "beta"),
            hashed=("alpha", "alpha"),
            shard=0,
            count=2,
        ),
        PrepareShardCoverage(
            plan=("alpha", "beta"),
            hashed=(),
            shard=1,
            count=2,
        ),
    )
    with pytest.raises(ValueError, match="more than once"):
        require_exact_prepare_coverage(duplicated)
    chain = ("electron-runtimes", "goose-cli", "goose-desktop")
    edges = {
        "goose-desktop": ("goose-cli",),
        "electron-runtimes": ("goose-desktop", "outside"),
    }
    split = tuple(
        PrepareShardCoverage(
            plan=chain,
            hashed=tuple(
                name for index, name in enumerate(chain) if index % 5 == shard
            ),
            shard=shard,
            count=5,
        )
        for shard in range(5)
    )
    with pytest.raises(
        ValueError, match="crosses shards: electron-runtimes -> goose-desktop"
    ):
        require_exact_prepare_coverage(split, prerequisites=edges)
    kept = tuple(
        PrepareShardCoverage(
            plan=chain,
            hashed=partition_prepare_targets(
                chain, shard=shard, count=5, prerequisites=edges
            ),
            shard=shard,
            count=5,
        )
        for shard in range(5)
    )
    assert require_exact_prepare_coverage(kept, prerequisites=edges) == chain
    partial = (
        PrepareShardCoverage(
            plan=("alpha", "beta"), hashed=("alpha",), shard=0, count=2
        ),
        PrepareShardCoverage(plan=("alpha", "beta"), hashed=(), shard=1, count=2),
    )
    with pytest.raises(ValueError, match="different slice"):
        require_exact_prepare_coverage(partial, prerequisites={"beta": ("alpha",)})
    only_consumer = (
        PrepareShardCoverage(
            plan=("alpha", "beta"), hashed=("beta",), shard=0, count=2
        ),
        PrepareShardCoverage(plan=("alpha", "beta"), hashed=(), shard=1, count=2),
    )
    with pytest.raises(ValueError, match="different slice"):
        require_exact_prepare_coverage(
            only_consumer, prerequisites={"beta": ("alpha",)}
        )


def test_preparation_accepts_a_shard_only_as_a_complete_pair() -> None:
    """Pins stay unsharded, and a half-specified shard never reaches hashing."""
    Preparation(
        system="aarch64-darwin",
        targets=("example",),
        prepare_shard=0,
        prepare_shard_count=5,
    )
    with pytest.raises(ValueError, match="both be set"):
        Preparation(system="aarch64-darwin", targets=(), prepare_shard=0)
    with pytest.raises(ValueError, match="both be set"):
        Preparation(system="aarch64-darwin", targets=(), prepare_shard_count=5)
    with pytest.raises(ValueError, match="cannot be sharded"):
        Preparation(
            system="x86_64-linux",
            targets=(),
            pins_only=True,
            prepare_shard=0,
            prepare_shard_count=5,
        )
    bad_bool = True
    with pytest.raises(TypeError, match="integers"):
        Preparation(
            system="aarch64-darwin",
            targets=(),
            prepare_shard=cast("int", bad_bool),
            prepare_shard_count=5,
        )
    with pytest.raises(ValueError, match="outside"):
        Preparation(
            system="aarch64-darwin",
            targets=(),
            prepare_shard=5,
            prepare_shard_count=5,
        )


def test_apply_prepare_shard_filters_execution_and_keeps_targets() -> None:
    """The requested target tuple is not replaced by the hashed slice."""
    ref = FlakeInputRef(
        name="nixpkgs",
        owner="NixOS",
        repo="nixpkgs",
        ref="nixos-25.05",
        input_type="github",
    )
    plan = make_run_plan(source_names=("alpha", "beta"), ref_inputs=(ref,))
    first = Preparation(
        system="aarch64-darwin",
        targets=("alpha", "beta", "nixpkgs"),
        prepare_shard=0,
        prepare_shard_count=2,
    )
    narrowed = _apply_prepare_shard(plan, first)
    assert narrowed is not None
    assert first.targets == ("alpha", "beta", "nixpkgs")
    assert first.prepare_plan == ("nixpkgs", "alpha", "beta")
    assert first.prepare_hashed == ("nixpkgs", "alpha")
    assert narrowed.order == ["nixpkgs", "alpha"]
    assert narrowed.resolved.source_names == ["alpha"]
    assert [item.name for item in narrowed.resolved.ref_inputs] == ["nixpkgs"]
    assert narrowed.resolved.do_sources is True
    assert narrowed.resolved.do_refs is True
    second = Preparation(
        system="aarch64-darwin",
        targets=("alpha", "beta", "nixpkgs"),
        prepare_shard=1,
        prepare_shard_count=2,
    )
    rest = _apply_prepare_shard(plan, second)
    assert rest is not None
    assert rest.order == ["beta"]
    assert rest.resolved.do_refs is False
    assert rest.resolved.do_sources is True
    ref_only = make_run_plan(ref_inputs=(ref,))
    refs = Preparation(
        system="aarch64-darwin",
        targets=("nixpkgs",),
        prepare_shard=0,
        prepare_shard_count=1,
    )
    refs_plan = _apply_prepare_shard(ref_only, refs)
    assert refs_plan is not None
    assert refs_plan.resolved.do_sources is False
    assert refs_plan.resolved.do_refs is True
    empty = Preparation(
        system="aarch64-darwin",
        targets=("alpha",),
        prepare_shard=1,
        prepare_shard_count=2,
    )
    assert _apply_prepare_shard(make_run_plan(source_names=("alpha",)), empty) is None
    assert empty.prepare_plan == ("alpha",)
    assert empty.prepare_hashed == ()


def test_apply_prepare_shard_claims_no_coverage_without_a_plan() -> None:
    """A selection error is not an empty successful slice."""
    sharded = Preparation(
        system="aarch64-darwin",
        targets=(),
        prepare_shard=0,
        prepare_shard_count=5,
    )
    error = _RunPlanError(
        message="missing",
        unknown_targets=("nope",),
        available_targets=(),
    )
    assert _apply_prepare_shard(error, sharded) is error
    assert sharded.prepare_plan == ()
    assert sharded.prepare_hashed == ()
    bare = Preparation(
        system="aarch64-darwin",
        targets=(),
        prepare_shard=0,
        prepare_shard_count=5,
    )
    assert _apply_prepare_shard(None, bare) is None
    assert bare.prepare_plan == ()
    assert bare.prepare_hashed == ()
    plan = make_run_plan(source_names=("alpha",))
    plain = Preparation(system="aarch64-darwin", targets=())
    assert _apply_prepare_shard(plan, plain) is plan
    assert plain.prepare_plan == ()
    assert _apply_prepare_shard(plan, None) is plan


def test_same_platform_partials_fold_into_the_three_system_merge(
    tmp_path: Path,
) -> None:
    """Disjoint Darwin slices become one system, then fold with Linux."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"keep.txt": "keep\n"})
    left = _file_candidate(root, "alpha.txt", "alpha\n", systems=("aarch64-darwin",))
    right = _file_candidate(
        root, "beta.txt", "beta\n", systems=("aarch64-darwin",)
    ).model_copy(update={"validate_all_packages": True})
    coverages = (_coverage(1), _coverage(0))
    merged = pipeline.merge_same_platform_partials(
        (left, right), coverages, seed=None, repo=root
    )
    assert merged.systems == ("aarch64-darwin",)
    assert merged.targets == ("example",)
    assert merged.sources == ("alpha", "beta")
    assert merged.validate_all_packages is True
    assert merged.prepared is True
    linux = _file_candidate(root, "linux.txt", "linux\n", systems=("aarch64-linux",))
    folded = pipeline.merge_prepared_candidates(merged, linux, repo=root)
    folded.apply(root)
    assert (root / "alpha.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (root / "beta.txt").read_text(encoding="utf-8") == "beta\n"
    assert (root / "linux.txt").read_text(encoding="utf-8") == "linux\n"
    assert (root / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert set(folded.systems) == {"aarch64-darwin", "aarch64-linux"}


def test_same_platform_partials_reject_an_incomplete_or_conflicting_fold(
    tmp_path: Path,
) -> None:
    """Coverage, targets, systems, and file bytes all fail closed."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"keep.txt": "keep\n"})
    left = _file_candidate(root, "alpha.txt", "alpha\n", systems=("aarch64-darwin",))
    right = _file_candidate(root, "beta.txt", "beta\n", systems=("aarch64-darwin",))
    coverages = (_coverage(0), _coverage(1))
    with pytest.raises(ValueError, match="sidecar"):
        pipeline.merge_same_platform_partials((left,), coverages, seed=None, repo=root)
    with pytest.raises(ValueError, match="every shard"):
        pipeline.merge_same_platform_partials((), (), seed=None, repo=root)
    with pytest.raises(ValueError, match="exactly one"):
        pipeline.merge_same_platform_partials(
            (left.model_copy(update={"systems": ()}),),
            (_coverage(0, count=1, plan=("alpha",)),),
            seed=None,
            repo=root,
        )
    with pytest.raises(ValueError, match="exactly one"):
        pipeline.merge_same_platform_partials(
            (left.model_copy(update={"systems": ("aarch64-darwin", "aarch64-linux")}),),
            (_coverage(0, count=1, plan=("alpha",)),),
            seed=None,
            repo=root,
        )
    with pytest.raises(ValueError, match="failed preparation"):
        pipeline.merge_same_platform_partials(
            (left.model_copy(update={"prepared": False}), right),
            coverages,
            seed=None,
            repo=root,
        )
    with pytest.raises(ValueError, match="baseline"):
        pipeline.merge_same_platform_partials(
            (left, right.model_copy(update={"base_tree": "b" * 40})),
            coverages,
            seed=None,
            repo=root,
        )
    with pytest.raises(ValueError, match="target selection"):
        pipeline.merge_same_platform_partials(
            (left, right.model_copy(update={"targets": ("other",)})),
            coverages,
            seed=None,
            repo=root,
        )
    with pytest.raises(ValueError, match="share one system"):
        pipeline.merge_same_platform_partials(
            (left, right.model_copy(update={"systems": ("aarch64-linux",)})),
            coverages,
            seed=None,
            repo=root,
        )
    with pytest.raises(ValueError, match="missing a shard"):
        pipeline.merge_same_platform_partials(
            (left,), (_coverage(0),), seed=None, repo=root
        )
    with pytest.raises(ValueError, match="more than once"):
        pipeline.merge_same_platform_partials(
            (left, right),
            (
                PrepareShardCoverage(
                    plan=("alpha", "beta"),
                    hashed=("beta",),
                    shard=0,
                    count=2,
                ),
                _coverage(1),
            ),
            seed=None,
            repo=root,
        )
    shared = ResolvedVersion(version="1")
    conflicted = right.model_copy(
        update={"resolutions": {"example": ResolvedVersion(version="2")}}
    )
    with pytest.raises(ValueError, match="Conflicting resolution"):
        pipeline.merge_same_platform_partials(
            (left.model_copy(update={"resolutions": {"example": shared}}), conflicted),
            coverages,
            seed=None,
            repo=root,
        )
    overlapped = pipeline.merge_same_platform_partials(
        (
            left.model_copy(update={"resolutions": {"example": shared}}),
            right.model_copy(
                update={
                    "resolutions": {
                        "example": shared,
                        "beta": ResolvedVersion(version="3"),
                    }
                }
            ),
        ),
        coverages,
        seed=None,
        repo=root,
    )
    assert overlapped.resolutions["example"].version == "1"
    assert overlapped.resolutions["beta"].version == "3"
    clash_left = _file_candidate(
        root, "same.txt", "left\n", systems=("aarch64-darwin",)
    )
    clash_right = _file_candidate(
        root, "same.txt", "right\n", systems=("aarch64-darwin",)
    )
    with pytest.raises(ValueError, match="Conflicting candidate edits"):
        pipeline.merge_same_platform_partials(
            (clash_left, clash_right), coverages, seed=None, repo=root
        )


def test_real_goose_chain_stays_on_one_prepare_shard() -> None:
    """The live companion and aggregate edges are one component, not index slices."""
    updaters = ensure_updaters_loaded()
    desktop = updaters["goose-desktop"]
    assert desktop.companion_of == "goose-cli"
    assert desktop.aggregate_into == ("electron-runtimes",)
    plan = tuple(sorted(updaters))
    edges = prepare_plan_prerequisites(plan, updaters)
    owners = {
        name: shard
        for shard in range(5)
        for name in partition_prepare_targets(
            plan, shard=shard, count=5, prerequisites=edges
        )
    }
    assert owners["goose-cli"] == owners["goose-desktop"] == owners["electron-runtimes"]
    for member in aggregate_source_members(updaters, "electron-runtimes"):
        assert owners[member] == owners["electron-runtimes"]
    index = {name: position % 5 for position, name in enumerate(plan)}
    assert index["goose-cli"] != index["goose-desktop"]


def test_same_platform_fold_rejects_a_split_goose_chain(tmp_path: Path) -> None:
    """Index slices of the real chain are not a legal same-platform merge."""
    plan = ("electron-runtimes", "goose-cli", "goose-desktop")
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"keep.txt": "keep\n"})
    candidates = tuple(
        _file_candidate(
            root, f"shard-{shard}.txt", f"{shard}\n", systems=("aarch64-darwin",)
        ).model_copy(update={"targets": plan})
        for shard in range(5)
    )
    coverages = tuple(
        PrepareShardCoverage(
            plan=plan,
            hashed=tuple(name for index, name in enumerate(plan) if index % 5 == shard),
            shard=shard,
            count=5,
        )
        for shard in range(5)
    )
    with pytest.raises(
        ValueError, match="crosses shards: electron-runtimes -> goose-desktop"
    ):
        pipeline.merge_same_platform_partials(
            candidates, coverages, seed=None, repo=root
        )


def test_load_prepare_shard_dir_requires_every_sidecar(tmp_path: Path) -> None:
    """The fold reads only candidate.json next to prepare-coverage.json."""
    missing = tmp_path / "missing"
    missing.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        pipeline.load_prepare_shard_dir(missing)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no candidates"):
        pipeline.load_prepare_shard_dir(empty)
    bare = tmp_path / "bare" / "0"
    bare.mkdir(parents=True)
    (bare / "candidate.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no coverage"):
        pipeline.load_prepare_shard_dir(bare.parent)


def test_same_platform_fold_keeps_an_owner_restore_of_a_pin_blank(
    tmp_path: Path,
) -> None:
    """41157698790: the owner's baseline-equal hash beats a non-owner's pin blank.

    The pin blanks ``keep.txt``. The owner shard writes it back to the
    baseline bytes. The non-owner shard never touches it and keeps the
    blank. Only the pin is a correct ancestor for that fold.
    """
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"keep.txt": "keep\n"})
    darwin = ("aarch64-darwin",)
    pin = _files_candidate(root, {"keep.txt": "blank\n"}, systems=())
    owner = _files_candidate(root, {"alpha.txt": "alpha\n"}, systems=darwin)
    other = _files_candidate(
        root, {"beta.txt": "beta\n", "keep.txt": "blank\n"}, systems=darwin
    )
    coverages = (_coverage(0), _coverage(1))
    folded = pipeline.merge_same_platform_partials(
        (owner, other), coverages, seed=pin, repo=root
    )
    folded.apply(root)
    assert (root / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert (root / "alpha.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (root / "beta.txt").read_text(encoding="utf-8") == "beta\n"
    git(root, "reset", "--hard", "HEAD")
    git(root, "clean", "-fdq")
    lost = pipeline.merge_same_platform_partials(
        (owner, other), coverages, seed=None, repo=root
    )
    lost.apply(root)
    assert (root / "keep.txt").read_text(encoding="utf-8") == "blank\n"
    git(root, "reset", "--hard", "HEAD")
    git(root, "clean", "-fdq")
    for bad in (
        pin.model_copy(update={"prepared": False}),
        pin.model_copy(update={"base_tree": "b" * 40}),
    ):
        with pytest.raises(ValueError, match="one prepared seed"):
            pipeline.merge_same_platform_partials(
                (owner, other), coverages, seed=bad, repo=root
            )


def test_load_prepare_shard_seed_requires_one_seed_per_shard(
    tmp_path: Path,
) -> None:
    """Every shard records the same seed, or the fold has no ancestor."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={"keep.txt": "keep\n"})
    pin = _files_candidate(root, {"keep.txt": "blank\n"}, systems=())
    other = pin.model_copy(update={"targets": ("other",)})
    shards = tmp_path / "shards"
    with pytest.raises(ValueError, match="no candidates"):
        pipeline.load_prepare_shard_seed(shards)
    for index in range(2):
        directory = shards / str(index)
        directory.mkdir(parents=True)
        (directory / "candidate.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="shard 0 has no seed"):
        pipeline.load_prepare_shard_seed(shards)
    for index, seed in enumerate((pin, other)):
        (shards / str(index) / pipeline.PREPARE_SEED_NAME).write_text(
            seed.model_dump_json(), encoding="utf-8"
        )
    with pytest.raises(ValueError, match="same seed"):
        pipeline.load_prepare_shard_seed(shards)
    (shards / "1" / pipeline.PREPARE_SEED_NAME).write_text(
        pin.model_dump_json(), encoding="utf-8"
    )
    assert pipeline.load_prepare_shard_seed(shards) == pin
