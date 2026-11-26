"""The PR closure gate selects, realizes, and publishes changed drvs."""

import ast
import builtins
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from lib.system_policy import supported_systems
from lib.update.derivation_validation import (
    DerivationValidationRequest,
    _batch_validation_args,
)

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "closure_gate",
    ROOT / ".github/closure-gate/gate.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["closure_gate"] = gate
_SPEC.loader.exec_module(gate)

_BASE = "a" * 40
_HEAD = "b" * 40
_SYSTEMS = ("aarch64-darwin", "aarch64-linux", "x86_64-linux")
_RUNNERS = {
    "aarch64-darwin": "macos-15",
    "aarch64-linux": "ubuntu-24.04-arm",
    "x86_64-linux": "ubuntu-24.04",
}


def _drv(label: str) -> str:
    digest = f"{label:0<32}"[:32]
    return f"/nix/store/{digest}-{label}.drv"


def _out(label: str) -> str:
    digest = f"{label:0<32}"[:32]
    return f"/nix/store/{digest}-{label}"


def _changed(kind: str, name: str, drv: str, system: str = "aarch64-darwin") -> object:
    return gate.ChangedOutput(
        kind=kind,
        name=name,
        attr=f"{'packages' if kind == 'package' else 'checks'}.{system}.{name}",
        drv=drv,
    )


class _Runner:
    def __init__(self) -> None:
        self.drvs: dict[str, str] = {}
        self.outputs: dict[str, tuple[str, ...]] = {}
        self.present: set[str] = set()
        self.presence_value = "absent"
        self.built: tuple[str, ...] | None = None
        self.builds: list[tuple[str, ...]] = []
        self.settled: list[tuple[str, ...]] = []
        self.build_result: tuple[str, ...] = ()
        self.build_results: list[tuple[str, ...]] | None = None
        self.pushed: tuple[str, ...] | None = None
        self.pushes: list[tuple[str, ...]] = []
        self.push_error: Exception | None = None

    def instantiate(self, attr: str) -> str:
        return self.drvs[attr]

    def outputs_of(self, drv: str) -> tuple[str, ...]:
        return self.outputs[drv]

    def presence(self, path: str) -> str:
        if path in self.present:
            return "present"
        return self.presence_value

    def build(self, installables: tuple[str, ...]) -> tuple[str, ...]:
        self.built = installables
        self.builds.append(installables)
        if self.build_results is not None:
            return self.build_results.pop(0)
        return self.build_result

    def settle(self, paths: tuple[str, ...]) -> None:
        self.settled.append(paths)

    def push(self, paths: tuple[str, ...]) -> None:
        if self.push_error is not None:
            raise self.push_error
        self.pushes.append(paths)
        self.pushed = paths


def test_gate_module_parses_as_python_312() -> None:
    """The runner invokes this file with Python 3.12 before the devshell."""
    source = (ROOT / ".github/closure-gate/gate.py").read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 12))


def test_diff_reports_only_added_or_changed_drvs() -> None:
    """Equal drvs and removals are not built; a new or different drv is."""
    unchanged = _drv("same")
    before = _drv("before")
    after = _drv("after")
    base = {
        ("package", "kept"): unchanged,
        ("package", "edited"): before,
        ("package", "removed"): _drv("removed"),
        ("root", "kept"): unchanged,
    }
    head = {
        ("package", "kept"): unchanged,
        ("package", "edited"): after,
        ("package", "added"): _drv("added"),
        ("root", "kept"): unchanged,
        ("root", "edited"): _drv("root"),
    }
    changed = gate.diff_inventories(base, head, system="aarch64-darwin")
    assert [(item.kind, item.name) for item in changed] == [
        ("package", "added"),
        ("package", "edited"),
        ("root", "edited"),
    ]
    assert changed[1].drv == after
    assert changed[1].attr == "packages.aarch64-darwin.edited"


def test_package_and_root_with_the_same_name_are_independent() -> None:
    """A package name is not a root-closure check, even when the strings match."""
    shared = _drv("shared")
    changed = gate.diff_inventories(
        {("package", "same"): shared, ("root", "same"): shared},
        {("package", "same"): shared, ("root", "same"): _drv("root")},
        system="x86_64-linux",
    )
    assert len(changed) == 1
    assert changed[0].kind == "root"
    assert changed[0].attr == "checks.x86_64-linux.same"


@pytest.mark.parametrize(
    "system",
    ["aarch64-darwin", "aarch64-linux", "x86_64-linux"],
)
def test_empty_diff_is_empty_for_every_policy_system(system: str) -> None:
    """A CI-only change that alters no drv is an empty plan on every system."""
    drv = _drv("stable")
    inventory = {("package", "tool"): drv, ("root", "root-closure-darwin-argus"): drv}
    assert gate.diff_inventories(inventory, inventory, system=system) == ()


def test_unstable_drv_fails_closed_instead_of_rebuilding() -> None:
    """A drv that changes between two evals of one tree is not a pull-request diff."""
    drv = _drv("tool")
    inventory = {("package", "tool"): drv}
    gate.stable_inventory(inventory, inventory)
    with pytest.raises(gate.GateError, match="not a pure function"):
        gate.stable_inventory(inventory, {("package", "tool"): _drv("other")})
    with pytest.raises(gate.GateError, match="identity changed"):
        gate.stable_inventory(inventory, {})


def test_diff_rejects_an_unsupported_system() -> None:
    """The matrix cannot invent a system the policy does not build."""
    with pytest.raises(gate.GateError, match="unsupported system"):
        gate.diff_inventories({}, {}, system="not-a-system")


def test_parse_inventory_rejects_malformed_documents() -> None:
    """A plan that is not a drv map must fail closed."""
    with pytest.raises(gate.GateError, match="not an object"):
        gate.parse_inventory([])
    with pytest.raises(gate.GateError, match="packages"):
        gate.parse_inventory({"roots": {}})
    with pytest.raises(gate.GateError, match="non-string"):
        gate.parse_inventory({"packages": {1: _drv("x")}, "roots": {}})
    with pytest.raises(gate.GateError, match="store path"):
        gate.parse_inventory({"packages": {"tool": "not-a-drv"}, "roots": {}})
    with pytest.raises(gate.GateError, match="wrong kind"):
        gate.parse_inventory({"packages": {"tool": _out("tool")}, "roots": {}})


def test_plan_roundtrip_keeps_changed_drvs_only() -> None:
    """The artifact is the changed set, and loading it rejects a foreign shape."""
    plan = gate.diff_plan(
        system="aarch64-darwin",
        merge_base=_BASE,
        head=_HEAD,
        eval_seconds=1.5,
        base_inventory={"packages": {"tool": _drv("old")}, "roots": {}},
        head_inventory={
            "packages": {"tool": _drv("new"), "extra": _drv("extra")},
            "roots": {},
        },
    )
    loaded = gate.load_plan(plan.to_json())
    assert loaded == plan
    document = json.loads(plan.to_json())
    document["extra"] = True
    with pytest.raises(gate.GateError, match="unexpected shape"):
        gate.load_plan(json.dumps(document))
    document = json.loads(plan.to_json())
    document["changed"][0]["note"] = "no"
    with pytest.raises(gate.GateError, match="unexpected shape"):
        gate.load_plan(json.dumps(document))
    document = json.loads(plan.to_json())
    document["changed"][0]["attr"] = "packages.aarch64-linux.tool"
    with pytest.raises(gate.GateError, match="does not match"):
        gate.load_plan(json.dumps(document))
    document = json.loads(plan.to_json())
    document["changed"].append(document["changed"][0])
    with pytest.raises(gate.GateError, match="repeats"):
        gate.load_plan(json.dumps(document))
    document = json.loads(plan.to_json())
    document["evalSeconds"] = True
    with pytest.raises(gate.GateError, match="missing"):
        gate.load_plan(json.dumps(document))
    with pytest.raises(gate.GateError, match="Git SHAs"):
        gate.diff_plan(
            system="aarch64-darwin",
            merge_base="not-a-sha",
            head=_HEAD,
            eval_seconds=0,
            base_inventory={"packages": {}, "roots": {}},
            head_inventory={"packages": {}, "roots": {}},
        )


def test_build_args_match_validation_plus_the_output_receipt() -> None:
    """The gate must not grow a second build command."""
    installables = (
        ".#packages.aarch64-darwin.tool",
        ".#checks.aarch64-darwin.root-closure-darwin-argus",
    )
    requests = tuple(
        DerivationValidationRequest(
            source="closure-gate", installable=item, mode="build"
        )
        for item in installables
    )
    expected = _batch_validation_args(requests, flake_root=None)
    expected = [arg for arg in expected if arg != "--keep-going"]
    first = expected.index("path:.#packages.aarch64-darwin.tool")
    expected = [
        *expected[:first],
        "--max-jobs",
        "1",
        "--print-build-logs",
        "--print-out-paths",
        *expected[first:],
    ]
    assert gate.build_args(installables) == expected
    with pytest.raises(gate.GateError, match="at least one"):
        gate.build_args(())


def test_plan_eval_disables_ifd_and_instantiate_does_not() -> None:
    """The cheap diff must not realize outputs; the build drv must be the real one."""
    inventory = gate.inventory_eval_args(
        Path("inventory.nix"), Path("/tree"), "aarch64-darwin"
    )
    assert inventory[1:6] == [
        "eval",
        "--impure",
        "--json",
        "--no-update-lock-file",
        "--option",
    ]
    assert "allow-import-from-derivation" in inventory
    assert inventory[inventory.index("allow-import-from-derivation") + 1] == "false"
    expr = inventory[-1]
    assert expr.startswith('import "inventory.nix"')
    assert 'flakePath = "/tree"' in expr
    assert 'system = "aarch64-darwin"' in expr
    quoted = gate.inventory_eval_args(
        Path('inven"tory.nix'), Path("/tree/${x}"), "x86_64-linux"
    )[-1]
    assert r'import "inven\"tory.nix"' in quoted
    assert r'flakePath = "/tree/\${x}"' in quoted
    instantiate = gate.instantiate_args("packages.aarch64-darwin.tool")
    assert "allow-import-from-derivation" not in instantiate
    assert instantiate[-1] == ".#packages.aarch64-darwin.tool.drvPath"


def test_output_paths_come_from_the_instantiated_drv() -> None:
    """The receipt is derivation show of the instantiated drv, not an IFD-disabled outPath."""
    drv = _drv("tool")
    first = _out("out")
    second = _out("dev")
    payload = {drv: {"outputs": {"out": {"path": first}, "dev": {"path": second}}}}
    assert gate.output_paths(payload, drv) == (second, first)
    with pytest.raises(gate.GateError, match="no entry"):
        gate.output_paths({}, drv)
    with pytest.raises(gate.GateError, match="no outputs"):
        gate.output_paths({drv: {"outputs": {}}}, drv)


def test_output_paths_accept_derivation_show_v4() -> None:
    """Nix 2.35 keys the drv basename and omits the store directory."""
    drv = _drv("tool")
    first = _out("out")
    second = _out("dev")
    payload = {
        "version": 4,
        "derivations": {
            drv.removeprefix("/nix/store/"): {
                "outputs": {
                    "out": {"path": first.removeprefix("/nix/store/")},
                    "dev": {"path": second.removeprefix("/nix/store/")},
                }
            }
        },
    }
    assert gate.output_paths(payload, drv) == (second, first)
    with pytest.raises(gate.GateError, match="no entry"):
        gate.output_paths(payload, _drv("other"))


def test_narinfo_queries_gkze_by_digest() -> None:
    """Presence is a gkze narinfo, and only 200 or 404 decide it."""
    path = _out("tool")
    digest = path.removeprefix("/nix/store/").split("-", 1)[0]
    assert gate.narinfo_url(path) == f"https://gkze.cachix.org/{digest}.narinfo"
    assert gate.classify_narinfo(200) == "present"
    assert gate.classify_narinfo(404) == "absent"
    assert gate.classify_narinfo(403) == "unknown"
    slept: list[float] = []
    statuses = iter([403, 200])

    def status(_url: str, _timeout: float) -> int:
        return next(statuses)

    assert gate.gkze_presence(path, status, slept.append) == "present"
    assert slept == [1.0]
    slept.clear()
    failures = iter([500, 500, 500])
    assert (
        gate.gkze_presence(path, lambda _url, _timeout: next(failures), slept.append)
        == "unknown"
    )
    assert slept == [1.0, 2.0]

    def broken(_url: str, _timeout: float) -> int:
        raise OSError("timed out")

    assert gate.gkze_presence(path, broken, lambda _seconds: None) == "unknown"


def test_realize_builds_one_installable_and_settles_before_the_next() -> None:
    """44713152415 died inside one combined Darwin build.

    Each output is realized and settled before the next starts, so two
    vendor-tree teardowns are not on the store volume together.
    """
    first = _changed("package", "comet", _drv("comet"))
    second = _changed("root", "root-closure-darwin-argus", _drv("argus"))
    runner = _Runner()
    runner.drvs[first.attr] = first.drv
    runner.drvs[second.attr] = second.drv
    runner.build_result = (_out("built"),)
    gate.realize_changed((first, second), runner)
    assert runner.builds == [(first.installable(),), (second.installable(),)]
    assert runner.settled == [(_out("built"),), (_out("built"),)]


def test_realize_pushes_one_installable_before_building_the_next() -> None:
    """44744612180 lost the VM before the single end-of-job push.

    A finished output has to be on gkze before the next build starts, or
    the retry rebuilds everything the dead runner already produced.
    """
    first = _changed("package", "comet", _drv("comet"))
    second = _changed("package", "spotify", _drv("spotify"))
    runner = _Runner()
    runner.drvs[first.attr] = first.drv
    runner.drvs[second.attr] = second.drv
    runner.build_results = [(_out("comet"),), (_out("spotify"),)]
    result = gate.realize_changed((first, second), runner)
    assert runner.pushes == [(_out("comet"),), (_out("spotify"),)]
    assert runner.builds == [(first.installable(),), (second.installable(),)]
    assert result.pushed == (_out("comet"), _out("spotify"))
    assert result.installables == (first.installable(), second.installable())


def test_select_slice_keeps_linux_whole_and_splits_darwin() -> None:
    """A Darwin VM realizes packages, or one root, not the whole plan."""
    packages = _changed("package", "comet", _drv("comet"))
    argus = _changed("root", "root-closure-darwin-argus", _drv("argus"))
    zeus = _changed("root", "root-closure-darwin-zeus", _drv("zeus"))
    changed = (packages, argus, zeus)
    assert gate.select_slice(changed, "") == changed
    assert gate.select_slice(changed, "packages") == (packages,)
    assert gate.select_slice(changed, "root-closure-darwin-argus") == (argus,)
    assert gate.select_slice(changed, "root-closure-darwin-rocinante") == ()
    with pytest.raises(gate.GateError, match="unknown closure-gate slice"):
        gate.select_slice(changed, "all")


def test_unchanged_slice_records_an_empty_success(tmp_path: Path, monkeypatch) -> None:
    """A root whose drv did not change does not build and does not fail."""
    plan = gate.Plan(
        system="aarch64-darwin",
        merge_base="a" * 40,
        head="b" * 40,
        eval_seconds=1.0,
        changed=(_changed("package", "comet", _drv("comet")),),
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.to_json(), encoding="utf-8")
    monkeypatch.setenv("NIXCFG_CLOSURE_GATE_PLAN", str(plan_path))
    monkeypatch.setenv("NIXCFG_CLOSURE_GATE_COMMAND", "realize")
    monkeypatch.setenv("NIXCFG_CLOSURE_GATE_SLICE", "root-closure-darwin-zeus")
    monkeypatch.setenv("SYSTEM", "aarch64-darwin")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    gate.main()
    result = json.loads(
        (tmp_path / "update-artifacts" / "closure-gate-result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["ok"] is True
    assert result["installables"] == []


def test_aggregate_joins_darwin_slice_results(tmp_path: Path) -> None:
    """The gate's one result is the union of the slice artifacts."""
    system = tmp_path / "aarch64-darwin"
    for name, installable in (
        (
            "closure-gate-aarch64-darwin-gate-packages",
            ".#packages.aarch64-darwin.comet",
        ),
        (
            "closure-gate-aarch64-darwin-gate-root-closure-darwin-argus",
            ".#checks.aarch64-darwin.root-closure-darwin-argus",
        ),
    ):
        directory = system / name
        directory.mkdir(parents=True)
        (directory / "closure-gate-result.json").write_text(
            json.dumps({
                "system": "aarch64-darwin",
                "ok": True,
                "pushed": [],
                "cached": [_out(name)],
                "installables": [installable],
            }),
            encoding="utf-8",
        )
    fault = system / "closure-gate-aarch64-darwin-gate-packages-device-fault"
    fault.mkdir()
    (fault / "closure-gate-result.json").write_text(
        json.dumps({
            "system": "aarch64-darwin",
            "ok": True,
            "pushed": [_out("fault")],
            "cached": [],
            "installables": [".#packages.aarch64-darwin.comet"],
        }),
        encoding="utf-8",
    )
    loaded = gate.load_system_result(tmp_path, "aarch64-darwin")
    assert loaded is not None
    assert loaded["installables"] == [
        ".#packages.aarch64-darwin.comet",
        ".#checks.aarch64-darwin.root-closure-darwin-argus",
    ]
    assert _out("fault") not in loaded["pushed"]


def test_clear_nix_build_directories_removes_only_nix_trees(tmp_path: Path) -> None:
    """Leftover build directories are removed before the next installable."""
    assert gate.clear_nix_build_directories(tmp_path / "missing") == 0
    root = tmp_path / "builds"
    root.mkdir()
    tree = root / "nix-1"
    tree.mkdir()
    (tree / "file").write_text("x", encoding="utf-8")
    link = root / "nix-link"
    link.symlink_to(tree)
    (root / "keep").mkdir()
    assert gate.clear_nix_build_directories(root) == 2
    assert not tree.exists()
    assert not link.exists()
    assert (root / "keep").is_dir()


def test_realize_skips_outputs_already_on_gkze() -> None:
    """A substituted path is not pushed again."""
    item = _changed("package", "tool", _drv("tool"))
    runner = _Runner()
    runner.drvs[item.attr] = item.drv
    runner.present.add(_out("tool"))
    runner.build_result = (_out("tool"),)
    result = gate.realize_changed((item,), runner)
    assert runner.built == (item.installable(),)
    assert runner.pushed is None
    assert result.pushed == ()
    assert result.cached == (_out("tool"),)
    assert result.installables == (".#packages.aarch64-darwin.tool",)


def test_realize_builds_a_missing_output_and_does_not_repush_a_hit() -> None:
    """The build receipt is pushed, except paths already on gkze."""
    item = _changed("root", "root-closure-darwin-argus", _drv("root"))
    present = _out("present")
    missing = _out("missing")
    runner = _Runner()
    runner.drvs[item.attr] = item.drv
    runner.present.add(present)
    runner.build_result = (present, missing)
    result = gate.realize_changed((item,), runner)
    assert runner.built == (".#checks.aarch64-darwin.root-closure-darwin-argus",)
    assert runner.pushed == (missing,)
    assert result.pushed == (missing,)
    assert result.cached == (present,)


def test_unknown_narinfo_is_not_a_cache_hit() -> None:
    """A 403 or a timeout must not skip the push."""
    item = _changed("package", "tool", _drv("tool"))
    runner = _Runner()
    runner.drvs[item.attr] = item.drv
    runner.presence_value = "unknown"
    runner.build_result = (_out("tool"),)
    result = gate.realize_changed((item,), runner)
    assert runner.built == (item.installable(),)
    assert result.pushed == (_out("tool"),)


def test_realize_builds_the_instantiated_drv_when_ifd_changes_it() -> None:
    """A Darwin system drv differs once import-from-derivation is allowed.

    The plan stays disabled so the diff is cheap. The build is the
    instantiated attribute, and the receipt is that drv's outputs.
    """
    item = _changed("root", "root-closure-darwin-argus", _drv("plan"))
    built_drv = _drv("built")
    output = _out("system")
    runner = _Runner()
    runner.drvs[item.attr] = built_drv
    runner.build_result = (output,)
    result = gate.realize_changed((item,), runner)
    assert runner.built == (item.installable(),)
    assert result.pushed == (output,)


def test_realize_fails_closed_on_a_bad_build() -> None:
    """A build that prints nothing, or a failed push, is red."""
    item = _changed("package", "tool", _drv("tool"))
    runner = _Runner()
    runner.drvs[item.attr] = item.drv
    runner.build_result = ()
    with pytest.raises(gate.GateError, match="returned 0 paths"):
        gate.realize_changed((item,), runner)
    assert runner.pushed is None
    runner.build_result = (_out("tool"),)
    runner.push_error = gate.GateError("upload failed")
    with pytest.raises(gate.GateError, match="upload failed"):
        gate.realize_changed((item,), runner)


def test_required_systems_follow_the_policy_file() -> None:
    """Darwin stays in the required set because the policy lists it."""
    text = (ROOT / "lib/system-policy.json").read_text(encoding="utf-8")
    assert gate.required_systems(text) == supported_systems()
    assert "aarch64-darwin" in gate.required_systems(text)
    with pytest.raises(gate.GateError, match="schemaVersion"):
        gate.required_systems('{"schemaVersion": 2, "systems": {"aarch64-darwin": {}}}')


def test_route_counts_refuse_a_partial_matrix() -> None:
    """A missing Darwin plan is not an empty success."""
    counts = dict.fromkeys(_SYSTEMS, 0)
    assert gate.route_counts(counts, _SYSTEMS) == counts
    partial = dict(counts)
    del partial["aarch64-darwin"]
    with pytest.raises(gate.GateError, match="aarch64-darwin"):
        gate.route_counts(partial, _SYSTEMS)


def test_gate_is_green_only_when_every_changed_system_recorded_a_push() -> None:
    """The aggregating check fails closed, including when Darwin was omitted."""
    required = _SYSTEMS
    zeros = dict.fromkeys(required, 0)
    skipped = dict.fromkeys(required, "skipped")
    assert (
        gate.gate_problems(
            plan="success",
            route="success",
            realize=skipped,
            changed=zeros,
            results={},
            required=required,
        )
        == ()
    )
    missing_darwin = {system: 0 for system in required if system != "aarch64-darwin"}
    problems = gate.gate_problems(
        plan="success",
        route="success",
        realize=skipped,
        changed=missing_darwin,
        results={},
        required=required,
    )
    assert any("aarch64-darwin" in problem for problem in problems)
    assert gate.gate_problems(
        plan="failure",
        route="success",
        realize=skipped,
        changed=zeros,
        results={},
        required=required,
    ) == ("plan failure",)
    changed = {**zeros, "aarch64-darwin": 1}
    assert any(
        "realize" in problem
        for problem in gate.gate_problems(
            plan="success",
            route="success",
            realize=skipped,
            changed=changed,
            results={},
            required=required,
        )
    )
    path = _out("tool")
    ok = {
        "ok": True,
        "installables": [".#packages.aarch64-darwin.tool"],
        "pushed": [path],
        "cached": [],
    }
    built = {**skipped, "aarch64-darwin": "success"}
    assert (
        gate.gate_problems(
            plan="success",
            route="success",
            realize=built,
            changed=changed,
            results={"aarch64-darwin": ok},
            required=required,
        )
        == ()
    )
    assert any(
        "did not record" in problem
        for problem in gate.gate_problems(
            plan="success",
            route="success",
            realize=built,
            changed=changed,
            results={},
            required=required,
        )
    )
    stray = {"ok": True, "installables": [], "pushed": [], "cached": []}
    assert any(
        "empty" in problem
        for problem in gate.gate_problems(
            plan="success",
            route="success",
            realize=skipped,
            changed=zeros,
            results={"x86_64-linux": stray},
            required=required,
        )
    )
    failed_empty = {**skipped, "x86_64-linux": "failure"}
    assert any(
        "empty" in problem
        for problem in gate.gate_problems(
            plan="success",
            route="success",
            realize=failed_empty,
            changed=zeros,
            results={},
            required=required,
        )
    )
    bad = {**ok, "pushed": [_drv("tool")]}
    assert any(
        "bad store path" in problem
        for problem in gate.gate_problems(
            plan="success",
            route="success",
            realize=built,
            changed=changed,
            results={"aarch64-darwin": bad},
            required=required,
        )
    )


def test_summary_records_pushed_store_paths() -> None:
    """The job summary is the evidence a reviewer can compare to gkze."""
    plan = gate.Plan(
        system="aarch64-darwin",
        merge_base=_BASE,
        head=_HEAD,
        eval_seconds=2,
        changed=(_changed("package", "tool", _drv("tool")),),
    )
    result = gate.RealizeResult(
        pushed=(_out("tool"),),
        cached=(),
        installables=(".#packages.aarch64-darwin.tool",),
    )
    summary = gate.render_summary(
        problems=(),
        plans=(plan,),
        results=(result,),
        systems=_SYSTEMS,
    )
    assert f"`{_out('tool')}`" in summary
    assert "Every changed output was realized and is on gkze.cachix.org." in summary


def _git(repo: Path, *args: str) -> str:
    git = shutil.which("git")
    assert git is not None
    result = subprocess.run(  # noqa: S603 -- fixed git argv against an owned fixture
        [git, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_merge_base_worktree_is_the_parent_tree(tmp_path: Path) -> None:
    """The plan evaluates the merge base, not the PR head, for the base drv map."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "gate@example.com")
    _git(repo, "config", "user.name", "gate")
    (repo / "file").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "file")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "file").write_text("head\n", encoding="utf-8")
    _git(repo, "commit", "-am", "head")
    head = _git(repo, "rev-parse", "HEAD")
    assert gate.resolve_merge_base(repo, base, head) == base
    dest = tmp_path / "base"
    gate.add_worktree(repo, dest, base)
    assert (dest / "file").read_text(encoding="utf-8") == "base\n"


def test_commands_fail_closed_and_an_empty_diff_aggregates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown commands and a missing plan are red; an empty diff is green."""
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setenv("NIXCFG_CLOSURE_GATE_COMMAND", "nope")
    with pytest.raises(SystemExit) as unknown:
        gate.main()
    assert unknown.value.code != 0
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("NIXCFG_CLOSURE_GATE_COMMAND", "route")
    with pytest.raises(SystemExit) as missing:
        gate.main()
    assert missing.value.code == 1
    assert "plan systems" in capsys.readouterr().out
    policy = (ROOT / "lib/system-policy.json").read_text(encoding="utf-8")
    counts = dict.fromkeys(gate.required_systems(policy), 0)
    monkeypatch.setenv("NIXCFG_CLOSURE_GATE_COMMAND", "aggregate")
    monkeypatch.setenv("PLAN_RESULT", "success")
    monkeypatch.setenv("ROUTE_RESULT", "success")
    monkeypatch.setenv("REALIZE_RESULTS", json.dumps(dict.fromkeys(counts, "skipped")))
    monkeypatch.setenv("CHANGED", json.dumps(counts))
    monkeypatch.setenv("CLOSURE_GATE_RESULTS", str(tmp_path / "results"))
    monkeypatch.setenv("CLOSURE_GATE_PLANS", str(tmp_path / "plans"))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    gate.main()
    assert "Every changed output" in summary.read_text(encoding="utf-8")


def test_duplicate_plan_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two artifacts for one system cannot overwrite each other's count."""
    plan = gate.diff_plan(
        system="aarch64-darwin",
        merge_base=_BASE,
        head=_HEAD,
        eval_seconds=0,
        base_inventory={"packages": {}, "roots": {}},
        head_inventory={"packages": {}, "roots": {}},
    )
    for name in ("one", "two"):
        directory = tmp_path / "plans" / name
        directory.mkdir(parents=True)
        (directory / "plan.json").write_text(plan.to_json(), encoding="utf-8")
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    with pytest.raises(gate.GateError, match="duplicate plan"):
        gate._command_route()


def test_pushed_paths_are_recorded_for_the_flush(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing flush receipt is how the daemon republishes a realized path."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    path = _out("tool")
    gate._remember((path,))
    from lib.update.ci.jobs import load_realized_root_paths

    assert load_realized_root_paths(tmp_path / "update-artifacts") == (path,)
    gate._remember(())
    assert load_realized_root_paths(tmp_path / "update-artifacts") == (path,)


def test_jobs_loader_does_not_import_the_ci_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner has no typer, and ``lib.update.ci`` imports it."""
    gate._JOBS.clear()
    for name in list(sys.modules):
        if name == "lib.update.ci" or name.startswith("lib.update.ci."):
            monkeypatch.delitem(sys.modules, name)
    real_import = builtins.__import__

    def guarded(
        name: str,
        global_ns: dict[str, object] | None = None,
        local_ns: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "lib.update.ci" or name.startswith("lib.update.ci."):
            msg = name
            raise ModuleNotFoundError(msg)
        return real_import(name, global_ns, local_ns, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    jobs = gate._load_jobs()
    assert callable(jobs.remember_realized_store_paths)
    assert callable(jobs.reclaim_hosted_store)
    assert jobs.__name__ == "_nixcfg_closure_gate_jobs"


def _workflow(name: str) -> dict[str, object]:
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_pull_request_workflow_is_same_repo_and_keeps_darwin() -> None:
    """Forks never receive the cache token, and Darwin is always planned."""
    text = (ROOT / ".github/workflows/pr-closure-gate.yml").read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    workflow = _workflow("pr-closure-gate.yml")
    assert set(workflow["on"]) == {"pull_request"}
    assert "synchronize" in workflow["on"]["pull_request"]["types"]
    jobs = workflow["jobs"]
    realize_names = {f"realize-{system}" for system in _SYSTEMS}
    darwin_retry = "realize-aarch64-darwin-retry"
    assert set(jobs) == {"plan", "route", "closure-gate", darwin_retry, *realize_names}
    for name, job in jobs.items():
        assert "head.repo.full_name == github.repository" in job["if"]
        if name in realize_names or name == darwin_retry:
            assert set(job["secrets"]) == {
                "CACHIX_AUTH_TOKEN",
                "UPDATE_SELF_HEAL_GITHUB_TOKEN",
            }
            assert job["with"]["stage"] == "closure-gate"
            assert job["with"]["scope"] == "gate"
            assert job["with"]["runner"] == _RUNNERS[job["with"]["system"]]
            assert "head.sha" in job["with"]["checkout_ref"]
            assert "matrix" not in job["if"]
        else:
            assert "secrets" not in job
    assert "secrets: inherit" not in text
    plan = jobs["plan"]["strategy"]["matrix"]["include"]
    systems = [row["system"] for row in plan]
    assert systems == list(supported_systems())
    assert {row["system"]: row["runner"] for row in plan} == _RUNNERS
    assert jobs["plan"]["strategy"]["fail-fast"] == "false"
    assert "closure-gate" in jobs
    plan_lane = jobs["plan"]["concurrency"]
    assert plan_lane["cancel-in-progress"] == "false"
    assert plan_lane["queue"] == "max"
    assert "matrix.system == 'aarch64-darwin'" in plan_lane["group"]
    assert "nixcfg-darwin-" in plan_lane["group"]
    assert "pr-closure-gate-plan-" in plan_lane["group"]
    darwin_lane = jobs["realize-aarch64-darwin"]["concurrency"]
    assert darwin_lane["cancel-in-progress"] == "false"
    assert darwin_lane["queue"] == "max"
    for lane in ("main-fix", "security", "heal", "routine"):
        assert lane in darwin_lane["group"]
        assert lane in plan_lane["group"]
    assert "concurrency" not in jobs["realize-aarch64-linux"]
    assert "concurrency" not in jobs["realize-x86_64-linux"]
    quality = (ROOT / ".github/workflows/pr-quality.yml").read_text(encoding="utf-8")
    assert "lib/tests/test_closure_gate.py" in quality


def test_faulted_darwin_realize_reruns_once_and_the_retry_is_the_gate_result() -> None:
    """44005867871: a faulted Nix Store read is not a closure result.

    substituteInPlace reported null bytes after disk2s7 I/O errors. The first
    job must not satisfy the gate. The fresh VM's result does when the first
    job recorded device_fault, and when the realize died before it could
    record one (44498744364 attempt 2). A finished slice that recorded
    device_fault=false does not rerun. That decision is per slice: run
    44888080350 recorded false only for home-george, and the one matrix
    output also skipped the fresh VM for the faulted slices and the lost
    rocinante runner.
    """
    jobs = _workflow("pr-closure-gate.yml")["jobs"]
    first = jobs["realize-aarch64-darwin"]
    retry = jobs["realize-aarch64-darwin-retry"]
    assert retry["needs"] == ["route", "realize-aarch64-darwin"]
    assert " ".join(retry["if"].split()) == (
        "always() && !cancelled() && "
        "github.event.pull_request.head.repo.full_name == github.repository && "
        "needs.route.result == 'success' && "
        "needs.realize-aarch64-darwin.result == 'failure'"
    )
    assert retry["concurrency"] == first["concurrency"]
    assert retry["strategy"] == first["strategy"]
    assert retry["uses"] == first["uses"]
    assert retry["secrets"] == first["secrets"]
    assert retry["with"] == {**first["with"], "device_fault_retry": "true"}
    darwin_roots = sorted(path.stem for path in (ROOT / "darwin").glob("*.nix"))
    home_roots = sorted(
        path.name
        for path in (ROOT / "home").iterdir()
        if (path / "default.nix").is_file()
    )
    assert first["strategy"]["matrix"]["slice"] == [
        "packages",
        *[f"root-closure-darwin-{name}" for name in darwin_roots],
        *[f"root-closure-home-{name}" for name in home_roots],
    ]
    assert first["with"]["closure_gate_slice"] == "${{ matrix.slice }}"
    assert first["with"]["shard"] == "${{ matrix.slice }}"
    assert "realize-aarch64-darwin-retry" in jobs["closure-gate"]["needs"]
    aggregate = next(
        step
        for step in jobs["closure-gate"]["steps"]
        if step.get("name") == "Require every changed closure on gkze"
    )
    realized = " ".join(aggregate["env"]["REALIZE_RESULTS"].split())
    assert (
        "needs.realize-aarch64-darwin.result == 'failure' && "
        "needs.realize-aarch64-darwin-retry.result != 'skipped' && "
        "needs.realize-aarch64-darwin-retry.result || "
        "needs.realize-aarch64-darwin.result"
    ) in realized
    download = next(
        step
        for step in jobs["closure-gate"]["steps"]
        if step.get("with", {}).get("pattern") == "closure-gate-aarch64-darwin-gate-*"
    )
    assert " ".join(download["if"].split()) == (
        "needs.realize-aarch64-darwin.result == 'success' || "
        "needs.realize-aarch64-darwin-retry.result == 'success'"
    )


def test_realize_reuses_the_update_native_runner() -> None:
    """The gate does not carry a second build or flush implementation."""
    workflow = _workflow("update-native.yml")
    checkout = workflow["jobs"]["native"]["steps"][0]["with"]["ref"]
    assert "inputs.checkout_ref" in checkout
    steps = workflow["jobs"]["native"]["steps"]
    candidate = next(step for step in steps if step.get("id") == "candidate")
    flush = next(step for step in steps if step.get("id") == "flush")
    realize = next(
        step
        for step in steps
        if step.get("name") == "Realize changed outputs and root closures"
    )
    assert candidate["if"] == "inputs.stage != 'closure-gate'"
    assert flush["if"] == "always()"
    retry_slice = next(step for step in steps if step.get("id") == "retry-slice")
    assert retry_slice["env"]["NIXCFG_CLOSURE_GATE_COMMAND"] == "prior-slice"
    assert "steps.retry-slice.outputs.skip != 'true'" in " ".join(realize["if"].split())
    upload = next(
        step
        for step in steps
        if step.get("name") == "Retain candidate and failure evidence"
    )
    assert "steps.retry-slice.outputs.skip != 'true'" in upload["if"]
    assert realize["env"]["NIXCFG_CLOSURE_GATE_COMMAND"] == "realize"
    assert "inputs.closure_gate_slice" in realize["env"]["NIXCFG_CLOSURE_GATE_SLICE"]
    update = _workflow("update.yml")
    budgets = [
        job["with"]["closure_budget_seconds"]
        for job in update["jobs"].values()
        if isinstance(job.get("with"), dict) and "closure_budget_seconds" in job["with"]
    ]
    assert budgets
    assert set(budgets) == {"18000"}
    assert gate.BUILD_BUDGET_SECONDS == 18000


def test_realize_failure_is_kept_for_the_uploaded_artifact(
    tmp_path, monkeypatch
) -> None:
    """A wedged action post can drop the job log after realize already failed."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(gate, "_executable", lambda name: name)

    def time_out(args, **_kwargs):
        raise subprocess.TimeoutExpired(args, 18000, stderr="error: signal 10\n")

    monkeypatch.setattr(gate.subprocess, "run", time_out)
    with pytest.raises(gate.GateError, match="signal 10"):
        gate._run(["nix", "build"], timeout=18000)
    assert (
        "signal 10" in (tmp_path / "update-artifacts" / "realize-error.txt").read_text()
    )

    def fail(args, **_kwargs):
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="error: hash mismatch\n"
        )

    monkeypatch.setattr(gate.subprocess, "run", fail)
    with pytest.raises(gate.GateError, match="hash mismatch"):
        gate._run(["nix", "build"], timeout=30)
    assert (
        "hash mismatch"
        in (tmp_path / "update-artifacts" / "realize-error.txt").read_text()
    )

    def fail_with_binary_stderr(args, **_kwargs):
        return subprocess.CompletedProcess(
            args, 1, stdout=b"", stderr=b"error: bad \xdd path\n"
        )

    monkeypatch.setattr(gate.subprocess, "run", fail_with_binary_stderr)
    with pytest.raises(gate.GateError, match="bad"):
        gate._run(["nix", "build"], timeout=30)
    assert "bad" in (tmp_path / "update-artifacts" / "realize-error.txt").read_text()

    monkeypatch.delenv("RUNNER_TEMP")

    def time_out_quietly(args, **_kwargs):
        raise subprocess.TimeoutExpired(args, 1)

    monkeypatch.setattr(gate.subprocess, "run", time_out_quietly)
    with pytest.raises(gate.GateError, match="nix timed out after 1s"):
        gate._run(["nix"], timeout=1)


def test_stream_build_emits_the_log_before_the_build_exits(monkeypatch, capsys) -> None:
    """A long nix build must show its log before the process returns."""
    monkeypatch.setattr(gate, "_BUILD_HEARTBEAT_SECONDS", 0.05)
    script = (
        "import sys, time\n"
        "sys.stderr.write('building goose-cli-v8-native\\n')\n"
        "sys.stderr.flush()\n"
        "sys.stdout.write('/nix/store/" + ("a" * 32) + "-out\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.2)\n"
    )
    stdout = gate._stream_build(
        ["python3", "-c", script],
        timeout=5,
        label=".#packages.x86_64-linux.goose-cli",
    )
    output = capsys.readouterr().out
    assert "closure-gate building .#packages.x86_64-linux.goose-cli\n" in output
    assert "building goose-cli-v8-native" in output
    assert "still running after" in output
    assert stdout.strip() == "/nix/store/" + ("a" * 32) + "-out"


def test_stream_build_emits_progress_and_keeps_a_timeout(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Killing a silent build still leaves the lines it already printed."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(gate, "_BUILD_HEARTBEAT_SECONDS", 0.05)
    script = (
        "import sys, time\n"
        "sys.stderr.write('still compiling\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(30)\n"
    )
    with pytest.raises(gate.GateError, match="goose-cli timed out after"):
        gate._stream_build(
            ["python3", "-c", script],
            timeout=0.35,
            label=".#packages.aarch64-darwin.goose-cli",
        )
    output = capsys.readouterr().out
    assert "closure-gate building .#packages.aarch64-darwin.goose-cli\n" in output
    assert "still compiling" in output
    assert "still running after" in output
    retained = (tmp_path / "update-artifacts" / "realize-error.txt").read_text()
    assert "still compiling" in retained


def test_stream_build_retains_a_failed_builder_log(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A configure failure is in the step log and the uploaded excerpt."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    script = (
        "import sys\nsys.stderr.write('Could not run pkg-config.\\n')\nsys.exit(1)\n"
    )
    with pytest.raises(gate.GateError, match="pkg-config"):
        gate._stream_build(["python3", "-c", script], timeout=5, label="v8")
    output = capsys.readouterr().out
    assert "Could not run pkg-config." in output
    retained = (tmp_path / "update-artifacts" / "realize-error.txt").read_text()
    assert "Could not run pkg-config." in retained


def test_stream_build_closes_the_pty_when_spawn_fails(monkeypatch) -> None:
    """A failed spawn must not leak the pty master."""

    def _fail_spawn(*_args: object, **_kwargs: object) -> None:
        msg = "spawn failed"
        raise OSError(msg)

    monkeypatch.setattr(gate.subprocess, "Popen", _fail_spawn)
    with pytest.raises(OSError, match="spawn failed"):
        gate._stream_build(["python3", "-c", "pass"], timeout=1, label="spawn")


def test_read_build_stdout_requires_a_captured_pipe() -> None:
    """The stdout reader fails closed when Popen did not capture stdout."""

    class _Process:
        stdout = None

    with pytest.raises(gate.GateError, match="not captured"):
        gate._read_build_stdout(_Process(), [])  # type: ignore[arg-type]


def test_stream_build_reports_a_missing_tool() -> None:
    """The build names a missing runner tool before it opens a pty."""
    with pytest.raises(gate.GateError, match="not on PATH"):
        gate._stream_build(["nixcfg-missing-build-tool"], timeout=1, label="missing")


def test_realize_build_streams_one_named_installable(monkeypatch) -> None:
    """The packages slice names the installable whose nix log is streaming."""
    seen: dict[str, object] = {}

    def fake_stream(args: list[str], *, timeout: float, label: str) -> str:
        seen["args"] = args
        seen["timeout"] = timeout
        seen["label"] = label
        return "/nix/store/" + ("b" * 32) + "-goose-cli\n"

    monkeypatch.setattr(gate, "_stream_build", fake_stream)
    paths = gate._ProcessRunner().build((".#packages.aarch64-darwin.goose-cli",))
    assert seen["label"] == ".#packages.aarch64-darwin.goose-cli"
    assert seen["timeout"] == gate.BUILD_BUDGET_SECONDS
    assert isinstance(seen["args"], list)
    assert "--print-build-logs" in seen["args"]
    assert paths == ("/nix/store/" + ("b" * 32) + "-goose-cli",)


def test_failure_excerpt_keeps_the_builder_before_the_cascade() -> None:
    """44888080350's home-george tail was only Cannot build lines."""
    detail = "\n".join([
        "error: builder for '/nix/store/abc.drv' failed with exit code 1",
        *(f"error: Cannot build '/nix/store/dep-{index}.drv'" for index in range(30)),
    ])
    excerpt = gate.failure_excerpt(detail)
    assert "builder for" in excerpt
    assert excerpt.index("builder for") < excerpt.index("Cannot build")


def test_finished_slice_attempt_ignores_a_fault_artifact(tmp_path: Path) -> None:
    """A device-fault artifact is not a finished slice. A plain one is."""
    assert gate.finished_slice_attempt(tmp_path / "missing") is False
    assert gate.finished_slice_attempt(tmp_path) is False
    (
        tmp_path
        / "closure-gate-aarch64-darwin-gate-root-closure-darwin-zeus-device-fault"
    ).mkdir()
    assert gate.finished_slice_attempt(tmp_path) is False
    plain = tmp_path / "closure-gate-aarch64-darwin-gate-root-closure-home-george"
    plain.mkdir()
    assert gate.finished_slice_attempt(tmp_path) is True
    evidence = tmp_path / "flattened"
    evidence.mkdir()
    (evidence / "cachix-daemon").mkdir()
    assert gate.finished_slice_attempt(evidence) is False
    (evidence / "device-fault.log").write_text("io error\n", encoding="utf-8")
    assert gate.finished_slice_attempt(evidence) is False
    (evidence / "device-fault.log").unlink()
    (evidence / "closure-gate-result.json").write_text("{}\n", encoding="utf-8")
    assert gate.finished_slice_attempt(evidence) is True
    named_fault = tmp_path / "named-fault"
    fault_child = named_fault / "closure-gate-aarch64-darwin-gate-packages"
    fault_child.mkdir(parents=True)
    (fault_child / "device-fault.log").write_text("io error\n", encoding="utf-8")
    assert gate.finished_slice_attempt(named_fault) is False


def test_prior_slice_skips_only_a_finished_attempt(tmp_path: Path, monkeypatch) -> None:
    """The retry cell records skip so it does not overwrite the first artifact."""
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("NIXCFG_CLOSURE_GATE_COMMAND", "prior-slice")
    monkeypatch.setenv("PRIOR_SLICE", str(tmp_path / "prior"))
    gate.main()
    assert output.read_text(encoding="utf-8") == "skip=false\n"
    output.unlink()
    plain = tmp_path / "prior" / "closure-gate-aarch64-darwin-gate-packages"
    plain.mkdir(parents=True)
    gate.main()
    assert output.read_text(encoding="utf-8") == "skip=true\n"


def test_prior_slice_rebuilds_a_flattened_fault_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    """45673035217 extracted the fault artifact into PRIOR_SLICE and skipped."""
    output = tmp_path / "github-output.txt"
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "device-fault.log").write_text("io error\n", encoding="utf-8")
    (prior / "cachix-daemon").mkdir()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("NIXCFG_CLOSURE_GATE_COMMAND", "prior-slice")
    monkeypatch.setenv("PRIOR_SLICE", str(prior))
    gate.main()
    assert output.read_text(encoding="utf-8") == "skip=false\n"
    output.unlink()
    (prior / "device-fault.log").unlink()
    (prior / "closure-gate-result.json").write_text("{}\n", encoding="utf-8")
    gate.main()
    assert output.read_text(encoding="utf-8") == "skip=true\n"
