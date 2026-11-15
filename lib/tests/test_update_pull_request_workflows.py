"""Workflow contracts for per-package update pull requests."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def _parsed(name: str) -> dict[str, object]:
    loaded = yaml.load(_workflow(name), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def test_nightly_update_and_prepare_skip_planner_branches() -> None:
    update = _parsed("update.yml")
    schedule = update["on"]["schedule"]
    assert schedule == [{"cron": "17 7 * * *"}]
    prepare = _workflow("pr-update-prepare.yml")
    assert "startsWith(github.head_ref, 'update/')" in prepare


def test_automerge_pins_the_head_and_does_not_bypass_checks() -> None:
    text = _workflow("update-automerge.yml")
    assert "--match-head-commit" in text
    assert "--auto" not in text
    assert "--admin" not in text
    assert "update/" in text


def test_self_heal_stays_on_the_same_branch() -> None:
    text = _workflow("update-self-heal.yml")
    assert "commit-heal" in text
    assert "gh pr create" not in text
    assert "--force" not in text
    assert "nixcfg-darwin-" in text
    assert "copilot-requests: write" in text
    actionlint = (ROOT / ".github" / "actionlint.yaml").read_text()
    assert "update-self-heal" in actionlint


def test_planner_reuses_one_pull_request_and_caps_darwin() -> None:
    planner = _parsed("update-planner.yml")
    assert planner["on"]["schedule"] == [{"cron": "23 9 * * *"}]
    assert planner["concurrency"]["group"] == "nixcfg-update-planner"
    pull = _workflow("update-pr.yml")
    assert "nixcfg-darwin-" in pull
    assert "cancel-in-progress: false" in pull
    assert '"git", "push", "origin"' in pull
    assert "--force" not in pull
    assert "accept-candidate" in pull
    native = _workflow("update-native.yml")
    assert "inputs.checkout_ref" in native
