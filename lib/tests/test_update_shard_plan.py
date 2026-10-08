"""Generated Darwin closure-shard plan is the only host inventory."""

import json
import subprocess
from pathlib import Path

import pytest

from lib.update.ci.shard_plan import (
    MAX_PARALLEL_DARWIN_ROOT_SHARDS,
    ClosureShard,
    ShardCosts,
    composed_root_name,
    costs_for_tree,
    darwin_roots,
    default_costs_path,
    eval_root_closure_manifest,
    github_actions_matrix,
    load_shard_costs,
    main,
    plan_darwin_closure_shards,
    plan_from_manifest_json,
    repo_root,
    write_github_actions_output,
)
from lib.update.derivation_validation import (
    RootClosureManifest,
    RootClosureManifestRoot,
)
from lib.update.paths import REPO_ROOT


def _manifest(
    *roots: tuple[str, str, str],
) -> RootClosureManifest:
    return RootClosureManifest.model_validate({
        "schemaVersion": 2,
        "requiredKinds": ["darwin", "home"],
        "requiredRoots": [],
        "roots": [
            {"kind": kind, "name": name, "system": system}
            for kind, name, system in roots
        ],
    })


def _darwin_inventory() -> RootClosureManifest:
    return _manifest(
        ("darwin", "argus", "aarch64-darwin"),
        ("darwin", "rocinante", "aarch64-darwin"),
        ("darwin", "zeus", "aarch64-darwin"),
        ("home", "george", "aarch64-darwin"),
    )


def test_current_repo_plan_is_provisional_two_wide() -> None:
    """Four Darwin roots pack into two shards until 4-wide vs 2-wide is measured."""
    shards = plan_darwin_closure_shards(_darwin_inventory())
    assert MAX_PARALLEL_DARWIN_ROOT_SHARDS == 2
    assert [tuple(shard.roots) for shard in shards] == [
        ("darwin-argus", "home-george"),
        ("darwin-rocinante", "darwin-zeus"),
    ]
    assert shards[0].installables == (
        "path:.#checks.aarch64-darwin.root-closure-darwin-argus",
        "path:.#checks.aarch64-darwin.root-closure-home-george",
    )
    assert shards[1].check_attrs == (
        "root-closure-darwin-rocinante",
        "root-closure-darwin-zeus",
    )


def test_planner_packs_extra_roots_by_measured_weight() -> None:
    """Relative weights from run 37657691147 decide packing, not host names."""
    extra = _manifest(
        ("darwin", "argus", "aarch64-darwin"),
        ("darwin", "rocinante", "aarch64-darwin"),
        ("darwin", "zeus", "aarch64-darwin"),
        ("home", "george", "aarch64-darwin"),
        ("home", "extra", "aarch64-darwin"),
    )
    shards = plan_darwin_closure_shards(extra, max_shards=2)
    packed = [tuple(shard.roots) for shard in shards]
    assert len(shards) == 2
    assert sum(len(shard.roots) for shard in shards) == 5
    assert {name for shard in shards for name in shard.roots} == {
        "darwin-argus",
        "darwin-rocinante",
        "darwin-zeus",
        "home-george",
        "home-extra",
    }
    assert packed == [
        ("darwin-argus", "home-george"),
        ("darwin-rocinante", "darwin-zeus", "home-extra"),
    ]


def test_empty_or_invalid_plans_fail_closed() -> None:
    """A missing Darwin inventory or empty matrix cannot skip validation."""
    linux_only = RootClosureManifest.model_construct(
        schema_version=2,
        required_kinds=("darwin", "home"),
        required_roots=(),
        roots=(
            RootClosureManifestRoot(kind="nixos", name="box", system="x86_64-linux"),
        ),
    )
    with pytest.raises(ValueError, match="empty"):
        plan_darwin_closure_shards(linux_only)
    with pytest.raises(ValueError, match="max_shards"):
        plan_darwin_closure_shards(_darwin_inventory(), max_shards=0)
    with pytest.raises(ValueError, match="empty"):
        github_actions_matrix(())


def test_github_matrix_and_output_are_generated_from_the_plan(
    tmp_path: Path,
) -> None:
    shards = plan_darwin_closure_shards(_darwin_inventory())
    matrix = github_actions_matrix(shards)
    assert matrix == {
        "include": [
            {
                "shard": "darwin-argus+home-george",
                "roots": "darwin-argus home-george",
                "system": "aarch64-darwin",
            },
            {
                "shard": "darwin-rocinante+darwin-zeus",
                "roots": "darwin-rocinante darwin-zeus",
                "system": "aarch64-darwin",
            },
        ]
    }
    output = tmp_path / "github" / "output"
    write_github_actions_output(shards, output)
    written = output.read_text()
    assert written.startswith("darwin_closure_shards=")
    parsed = json.loads(written.split("=", 1)[1])
    assert parsed == matrix


def test_plan_from_manifest_json_and_cli_round_trip(tmp_path: Path, capsys) -> None:
    payload = _darwin_inventory().model_dump(by_alias=True)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload))
    shards = plan_from_manifest_json(payload)
    assert [shard.shard for shard in shards] == [
        "darwin-argus+home-george",
        "darwin-rocinante+darwin-zeus",
    ]
    assert [shard.shard for shard in shards] == [
        shard.shard for shard in plan_from_manifest_json(path.read_bytes())
    ]
    github = tmp_path / "out"
    assert main(["--manifest", str(path), "--github-output", str(github)]) == 0
    assert "darwin_closure_shards=" in github.read_text()
    assert main(["--manifest", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["include"]
    with pytest.raises(ValueError, match="required"):
        main([])
    with pytest.raises(ValueError, match="choose one"):
        main(["--manifest", str(path), "--nix-eval"])


def test_eval_root_closure_manifest_uses_owned_installable(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[:4] == ["nix", "eval", "--json", "--no-write-lock-file"]
        assert args[4].endswith("#lib.rootClosureManifest")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=_darwin_inventory().model_dump_json(by_alias=True),
            stderr="",
        )

    manifest = eval_root_closure_manifest(Path("/tmp/candidate"), run=run)
    assert [composed_root_name(root) for root in darwin_roots(manifest)] == [
        "darwin-argus",
        "darwin-rocinante",
        "darwin-zeus",
        "home-george",
    ]
    failed = subprocess.CompletedProcess(["nix"], 1, stdout="", stderr="boom")

    def fail(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return failed

    with pytest.raises(ValueError, match="boom"):
        eval_root_closure_manifest(run=fail)

    def silent(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    with pytest.raises(ValueError, match="nix eval failed"):
        eval_root_closure_manifest(run=silent)
    monkey_eval = _darwin_inventory()

    def eval_ok(
        flake_root: Path | None = None, **_kwargs: object
    ) -> RootClosureManifest:
        return monkey_eval

    from lib.update.ci import shard_plan

    monkeypatch.setattr(shard_plan, "eval_root_closure_manifest", eval_ok)
    assert shard_plan.main(["--nix-eval"]) == 0
    assert json.loads(capsys.readouterr().out)["include"]


def test_committed_costs_and_repo_root_are_the_measured_baseline() -> None:
    costs = load_shard_costs()
    assert costs.measured_from == "37657691147"
    assert costs.shared_packages == ("zed-editor-nightly",)
    assert costs.root_weights["darwin-argus"] == 3
    assert default_costs_path() == Path(REPO_ROOT / "lib/update/ci/shard_costs.json")
    assert repo_root() == REPO_ROOT
    assert ShardCosts.model_validate_json(default_costs_path().read_bytes()) == costs


def test_costs_path_falls_back_to_checkout_when_unpackaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Update 37714392950: site-packages omitted shard_costs.json."""
    from lib.update.ci import shard_plan

    monkeypatch.setattr(shard_plan, "_INSTALLED_COSTS_PATH", tmp_path / "missing.json")
    assert shard_plan.default_costs_path() == shard_plan._REPO_COSTS_PATH
    assert shard_plan.load_shard_costs().measured_from == "37657691147"


def test_costs_path_fails_closed_when_no_authority_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lib.update.ci import shard_plan

    monkeypatch.setattr(shard_plan, "_INSTALLED_COSTS_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(shard_plan, "_REPO_COSTS_PATH", tmp_path / "also-missing.json")
    with pytest.raises(FileNotFoundError, match="shard_costs.json is missing"):
        shard_plan.default_costs_path()
    with pytest.raises(FileNotFoundError, match="shard cost table is missing"):
        shard_plan.load_shard_costs(tmp_path / "absent.json")


def test_costs_for_tree_prefers_snapshot_then_packaged(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "measuredFrom": "snapshot",
        "notes": "fixture",
        "sharedPackages": ["zed-editor-nightly"],
        "rootWeights": {"darwin-argus": 9},
    }
    table = tmp_path / "lib" / "update" / "ci" / "shard_costs.json"
    table.parent.mkdir(parents=True)
    table.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert costs_for_tree(tmp_path).measured_from == "snapshot"
    assert costs_for_tree(tmp_path / "empty").measured_from == "37657691147"


def test_packed_shard_skips_empty_bins() -> None:
    """A max_shards larger than the inventory stays one-root-per-shard."""
    one = _manifest(
        ("darwin", "argus", "aarch64-darwin"),
        ("home", "george", "aarch64-darwin"),
    )
    shards = plan_darwin_closure_shards(one, max_shards=8)
    assert [shard.roots for shard in shards] == [
        ("darwin-argus",),
        ("home-george",),
    ]
    assert ClosureShard(
        shard="darwin-argus+home-george",
        system="aarch64-darwin",
        roots=("darwin-argus", "home-george"),
    ).installables == (
        "path:.#checks.aarch64-darwin.root-closure-darwin-argus",
        "path:.#checks.aarch64-darwin.root-closure-home-george",
    )
