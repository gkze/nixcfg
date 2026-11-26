"""The PR closure gate selects, realizes, and publishes changed drvs."""

import ast
import builtins
import importlib.util
import json
import os
import shutil
import signal
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
_HOSTED_SPEC = importlib.util.spec_from_file_location(
    "hosted_python",
    ROOT / ".github/closure-gate/hosted_python.py",
)
assert _HOSTED_SPEC is not None
assert _HOSTED_SPEC.loader is not None
hosted = importlib.util.module_from_spec(_HOSTED_SPEC)
sys.modules["hosted_python"] = hosted
_HOSTED_SPEC.loader.exec_module(hosted)

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
        self.build_result: tuple[str, ...] = ()
        self.pushed: tuple[str, ...] | None = None
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
        return self.build_result

    def push(self, paths: tuple[str, ...]) -> None:
        if self.push_error is not None:
            raise self.push_error
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
    first = expected.index("path:.#packages.aarch64-darwin.tool")
    expected = [*expected[:first], "--print-out-paths", *expected[first:]]
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


_REALIZE_RETRY = (
    "needs.realize-aarch64-darwin.outputs.device_fault == 'true' || "
    "needs.realize-aarch64-darwin.outputs.runner_fault == 'true' || "
    "(needs.realize-aarch64-darwin.result == 'failure' && "
    "needs.realize-aarch64-darwin.outputs.runner_fault == '' && "
    "needs.realize-aarch64-darwin.outputs.device_fault == '')"
)


def test_faulted_darwin_realize_reruns_once_and_the_retry_is_the_gate_result() -> None:
    """A faulted store or a signalled realize is not a closure result.

    44005867871: substituteInPlace reported null bytes after disk2s7 I/O errors.
    45788923798: the realize process exited 138 during zeus and recorded neither
    flag, so the retry stayed skipped. The fresh VM's result is the gate
    result when device_fault, runner_fault, or a flagless failure selected it.
    A GateError records runner_fault=false and does not select the retry.
    """
    jobs = _workflow("pr-closure-gate.yml")["jobs"]
    first = jobs["realize-aarch64-darwin"]
    retry = jobs["realize-aarch64-darwin-retry"]
    assert retry["needs"] == ["route", "realize-aarch64-darwin"]
    assert " ".join(retry["if"].split()) == (
        "always() && !cancelled() && "
        "github.event.pull_request.head.repo.full_name == github.repository && "
        "needs.route.result == 'success' && "
        f"({_REALIZE_RETRY})"
    )
    assert retry["concurrency"] == first["concurrency"]
    assert retry["uses"] == first["uses"]
    assert retry["secrets"] == first["secrets"]
    assert retry["with"] == {**first["with"], "device_fault_retry": "true"}
    assert "realize-aarch64-darwin-retry" in jobs["closure-gate"]["needs"]
    aggregate = next(
        step
        for step in jobs["closure-gate"]["steps"]
        if step.get("name") == "Require every changed closure on gkze"
    )
    realized = " ".join(aggregate["env"]["REALIZE_RESULTS"].split())
    assert (
        f"({_REALIZE_RETRY}) && "
        "needs.realize-aarch64-darwin-retry.result || "
        "needs.realize-aarch64-darwin.result"
    ) in realized
    darwin_result = next(
        step
        for step in jobs["closure-gate"]["steps"]
        if isinstance(step.get("with"), dict)
        and step["with"].get("name") == "closure-gate-aarch64-darwin-gate"
    )
    assert " ".join(darwin_result["if"].split()) == (
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
    assert flush["shell"] == "python"
    assert "hosted_python.py" in flush["run"]
    assert flush["env"]["NIXCFG_HOSTED_TARGET"] == "lib/update/ci/jobs.py"
    assert realize["id"] == "realize"
    assert realize["shell"] == "python"
    assert "hosted_python.py" in realize["run"]
    assert realize["env"]["NIXCFG_RECORD_SIGNAL"] == "1"
    assert realize["env"]["NIXCFG_HOSTED_TARGET"] == ".github/closure-gate/gate.py"
    assert realize["env"]["NIXCFG_CLOSURE_GATE_COMMAND"] == "realize"
    storage = next(step for step in steps if step.get("id") == "storage")
    assert storage["shell"] == "python"
    assert "hosted_python.py" in storage["run"]
    assert storage["env"]["NIXCFG_REALIZE_OUTCOME"] == "${{ steps.realize.outcome }}"
    assert (
        storage["env"]["NIXCFG_REALIZE_RUNNER_FAULT"]
        == "${{ steps.realize.outputs.runner_fault }}"
    )
    update = _workflow("update.yml")
    budgets = [
        job["with"]["closure_budget_seconds"]
        for job in update["jobs"].values()
        if isinstance(job.get("with"), dict) and "closure_budget_seconds" in job["with"]
    ]
    assert budgets
    assert set(budgets) == {"18000"}
    assert gate.BUILD_BUDGET_SECONDS == 18000


def test_runner_signal_is_the_shell_status_range() -> None:
    """129 through 159 is a signal. Python's negative status is the same death."""
    assert hosted.reported_status(-signal.SIGBUS) == 128 + signal.SIGBUS
    assert hosted.is_runner_signal(-signal.SIGBUS)
    for status in (129, 138, 159, -1, -31):
        assert hosted.is_runner_signal(status)
    for status in (0, 1, 128, 160, -32):
        assert not hosted.is_runner_signal(status)


def test_hosted_python_publishes_the_framework_and_nix_bins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Later shell: python steps inherit the interpreter realize resolved."""
    github_path = tmp_path / "github-path"
    monkeypatch.setenv("GITHUB_PATH", str(github_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(hosted, "FRAMEWORK_BIN", "/framework/bin")
    monkeypatch.setattr(hosted, "NIX_BIN", "/nix/bin")
    monkeypatch.setenv("HOME", str(tmp_path))
    hosted.publish_hosted_path()
    published = os.environ["PATH"].split(os.pathsep)
    assert published[:3] == [
        "/framework/bin",
        "/nix/bin",
        str(tmp_path / ".nix-profile" / "bin"),
    ]
    assert published[-1] == "/usr/bin"
    assert github_path.read_text(encoding="utf-8").splitlines() == published[:3]


def test_hosted_python_records_a_signalled_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """45788923798: the realize child exits 138 and the parent records the fault."""
    target = tmp_path / "child.py"
    target.write_text("import os, sys\nsys.exit(int(os.environ['EXIT_CODE']))\n")
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("NIXCFG_HOSTED_TARGET", str(target))
    monkeypatch.setenv("NIXCFG_RECORD_SIGNAL", "1")
    monkeypatch.delenv("NIXCFG_REALIZE_OUTCOME", raising=False)
    monkeypatch.delenv("NIXCFG_REALIZE_RUNNER_FAULT", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    for status in (129, 138, 159):
        output.unlink(missing_ok=True)
        monkeypatch.setenv("EXIT_CODE", str(status))
        with pytest.raises(SystemExit) as raised:
            hosted.main()
        assert raised.value.code == status
        assert output.read_text(encoding="utf-8") == "runner_fault=true\n"
    for status in (0, 1, 128, 160):
        output.unlink(missing_ok=True)
        monkeypatch.setenv("EXIT_CODE", str(status))
        with pytest.raises(SystemExit) as raised:
            hosted.main()
        assert raised.value.code == status
        assert output.read_text(encoding="utf-8") == "runner_fault=false\n"


def test_hosted_python_maps_a_real_signal_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child killed by SIGBUS is status -SIGBUS, which the runner logs as 138."""
    target = tmp_path / "child.py"
    target.write_text("import os, signal\nos.kill(os.getpid(), signal.SIGBUS)\n")
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("NIXCFG_HOSTED_TARGET", str(target))
    monkeypatch.setenv("NIXCFG_RECORD_SIGNAL", "1")
    monkeypatch.delenv("NIXCFG_REALIZE_OUTCOME", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    with pytest.raises(SystemExit) as raised:
        hosted.main()
    assert raised.value.code == 128 + signal.SIGBUS
    assert output.read_text(encoding="utf-8") == "runner_fault=true\n"


def test_hosted_python_prefers_an_explicit_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NIXCFG_PYTHON wins, then the framework binary, then this process."""
    chosen = tmp_path / "chosen"
    chosen.write_text("")
    chosen.chmod(0o755)
    framework = tmp_path / "framework"
    framework.mkdir()
    (framework / "python").write_text("")
    (framework / "python").chmod(0o755)
    monkeypatch.setattr(hosted, "FRAMEWORK_BIN", str(framework))
    monkeypatch.setenv("NIXCFG_PYTHON", str(chosen))
    assert hosted.resolve_interpreter() == str(chosen)
    monkeypatch.setenv("NIXCFG_PYTHON", str(tmp_path / "missing"))
    assert hosted.resolve_interpreter() == str(framework / "python")
    monkeypatch.delenv("NIXCFG_PYTHON")
    monkeypatch.setattr(hosted, "FRAMEWORK_BIN", str(tmp_path / "absent"))
    assert hosted.resolve_interpreter() == sys.executable


def test_hosted_python_notes_a_realize_that_wrote_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The storage step records the fault when the realize shell died too."""
    target = tmp_path / "child.py"
    target.write_text("marker = True\n")
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("NIXCFG_HOSTED_TARGET", str(target))
    monkeypatch.delenv("NIXCFG_RECORD_SIGNAL", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    monkeypatch.setenv("NIXCFG_REALIZE_OUTCOME", "failure")
    monkeypatch.setenv("NIXCFG_REALIZE_RUNNER_FAULT", "")
    hosted.main()
    assert output.read_text(encoding="utf-8") == "runner_fault=true\n"
    output.unlink()
    monkeypatch.setenv("NIXCFG_REALIZE_RUNNER_FAULT", "false")
    hosted.main()
    assert not output.exists()
    monkeypatch.setenv("NIXCFG_REALIZE_OUTCOME", "skipped")
    monkeypatch.setenv("NIXCFG_REALIZE_RUNNER_FAULT", "")
    hosted.main()
    assert not output.exists()


def test_hosted_python_reexecs_a_different_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dump and flush replace this process with the framework interpreter."""
    target = tmp_path / "child.py"
    target.write_text("raise SystemExit(0)\n")
    framework = tmp_path / "framework"
    framework.mkdir()
    interpreter = framework / "python"
    interpreter.write_text("")
    interpreter.chmod(0o755)
    monkeypatch.setattr(hosted, "FRAMEWORK_BIN", str(framework))
    monkeypatch.delenv("NIXCFG_PYTHON", raising=False)
    monkeypatch.delenv("NIXCFG_RECORD_SIGNAL", raising=False)
    monkeypatch.delenv("NIXCFG_REALIZE_OUTCOME", raising=False)
    monkeypatch.setenv("NIXCFG_HOSTED_TARGET", str(target))
    seen: list[list[str]] = []

    def _execv(path: str, argv: list[str]) -> None:
        seen.append([path, *argv])
        raise SystemExit(0)

    monkeypatch.setattr(hosted.os, "execv", _execv)
    with pytest.raises(SystemExit):
        hosted.main()
    assert seen == [[str(interpreter), str(interpreter), str(target)]]


def test_hosted_python_requires_a_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIXCFG_HOSTED_TARGET", raising=False)
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    monkeypatch.delenv("NIXCFG_REALIZE_OUTCOME", raising=False)
    with pytest.raises(SystemExit, match="NIXCFG_HOSTED_TARGET is required"):
        hosted.main()
