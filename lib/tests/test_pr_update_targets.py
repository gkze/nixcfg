"""PR update-prepare target mapping."""

import io
import runpy
import sys
from pathlib import Path

import pytest
import yaml

from lib import pr_update_targets
from lib.pr_update_targets import main, touched_targets

ROOT = Path(__file__).resolve().parents[2]


def _tree(tmp_path: Path) -> Path:
    for rel in (
        "packages/aside/updater.py",
        "packages/aside/sources.json",
        "packages/gh-current/updater.py",
        "packages/no-updater/default.nix",
        "overlays/mdformat.updater.py",
        "overlays/mdformat.sources.json",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return tmp_path


def test_touched_targets_maps_package_and_overlay_owners(tmp_path: Path) -> None:
    """#268's aside fix and gh-current's overlay map to their updaters."""
    root = _tree(tmp_path)
    changed = [
        "packages/aside/updater.py",
        "packages/aside/updater_test.py\n",
        "overlays/gh-current/default.nix",
        "overlays/mdformat.sources.json",
        "packages/no-updater/default.nix",
        "overlays/default.nix",
        "lib/update/ci/warmup.py",
        "flake.lock",
        "packages",
    ]
    assert touched_targets(changed, root=root) == ("aside", "gh-current", "mdformat")
    assert touched_targets(["lib/update/ci/jobs.py"], root=root) == ()


def test_main_writes_github_output_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The workflow appends stdout to ``$GITHUB_OUTPUT`` verbatim."""
    root = _tree(tmp_path)
    monkeypatch.setattr(pr_update_targets, "get_repo_root", lambda: root)
    assert main(["packages/gh-current/sources.json", "packages/aside/x"]) == 0
    assert capsys.readouterr().out == "targets=aside gh-current\n"
    monkeypatch.setattr("sys.stdin", io.StringIO("README.md\n"))
    assert main() == 0
    assert capsys.readouterr().out == "targets=\n"
    monkeypatch.setattr("sys.stdin", io.StringIO("packages/aside/default.nix\n"))
    monkeypatch.setattr("lib.update.paths.get_repo_root", lambda: root)
    monkeypatch.delitem(sys.modules, "lib.pr_update_targets")
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("lib.pr_update_targets", run_name="__main__")
    assert exited.value.code == 0
    assert capsys.readouterr().out == "targets=aside\n"


def test_real_repo_maps_todays_failed_targets() -> None:
    """Targets from today's failed Update runs resolve in the real tree."""
    assert touched_targets(
        [
            "packages/aside/updater.py",
            "packages/electron-runtimes/sources.json",
            "packages/gh-current/default.nix",
            "packages/unsloth/updater.py",
        ],
        root=ROOT,
    ) == ("aside", "electron-runtimes", "gh-current", "unsloth")


def test_pr_update_prepare_runs_the_update_prepare_stages() -> None:
    """The PR check reuses update-native's prepare and merge stages unchanged."""
    workflow = yaml.load(
        (ROOT / ".github/workflows/pr-update-prepare.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    update = yaml.load(
        (ROOT / ".github/workflows/update.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"pull_request"}
    jobs = workflow["jobs"]
    stages = {
        name: (job["with"]["runner"], job["with"]["system"], job["with"]["stage"])
        for name, job in jobs.items()
        if name != "targets"
    }
    expected = {
        name: (
            update["jobs"][name]["with"]["runner"],
            update["jobs"][name]["with"]["system"],
            update["jobs"][name]["with"]["stage"],
        )
        for name in stages
    }
    assert stages == expected
    assert set(stages) == {
        "prepare-pins",
        *(f"prepare-darwin-{index}" for index in range(5)),
        *(f"prepare-darwin-retry-{index}" for index in range(5)),
        "prepare-darwin-merge",
        "prepare-arm",
        "prepare-x86",
        "prepare-merge",
    }
    for index in range(5):
        retry = jobs[f"prepare-darwin-retry-{index}"]
        assert retry["with"] == jobs[f"prepare-darwin-{index}"]["with"]
        assert " ".join(retry["if"].split()) == " ".join(
            update["jobs"][f"prepare-darwin-retry-{index}"]["if"].split()
        )
    for name in ("prepare-merge", "prepare-darwin-merge"):
        assert " ".join(jobs[name]["if"].split()) == " ".join(
            update["jobs"][name]["if"].split()
        )
    for name, job in jobs.items():
        if name == "targets":
            continue
        assert job["uses"] == "./.github/workflows/update-native.yml"
        assert job["secrets"] == "inherit"
        if name not in {"prepare-merge", "prepare-darwin-merge"}:
            assert job["with"]["targets"] == "${{ needs.targets.outputs.targets }}"
    assert (
        jobs["prepare-merge"]["with"]
        | {"previous": update["jobs"]["prepare-merge"]["with"]["previous"]}
        == update["jobs"]["prepare-merge"]["with"]
    )
    assert "lib.pr_update_targets" in jobs["targets"]["steps"][-1]["run"]
