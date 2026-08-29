"""Tests for the goose-cli updater."""

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent
from lib.update.updaters import VersionInfo


def _load_module(module_name: str):
    return load_repo_module("overlays/goose-cli/updater.py", module_name)


def test_goose_cli_updater_forwards_materialized_artifacts_without_source_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The locked input replaces the former duplicate fixed-output source hash."""
    module = _load_module("goose_cli_updater_artifact_test")
    updater = module.GooseCliUpdater()

    async def _artifacts():
        yield UpdateEvent.status("goose-cli", "materialized cargo artifacts")

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)

    events = _run(_collect(updater.fetch_hashes(VersionInfo("1.2.3", {}), object())))

    assert [event.kind.value for event in events] == ["status", "value"]
    assert events[0].message == "materialized cargo artifacts"
    assert events[-1].payload == []


@pytest.mark.parametrize("ref", [None, "", "main", "v"])
def test_goose_cli_updater_requires_a_versioned_release_ref(
    ref: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Goose versions must be derived from an explicit v-prefixed input ref."""
    module = _load_module(f"goose_cli_updater_ref_test_{ref!r}")
    updater = module.GooseCliUpdater()
    node = FlakeLockNode.model_validate({
        "original": {
            "type": "github",
            "owner": "aaif-goose",
            "repo": "goose",
            **({"ref": ref} if ref is not None else {}),
        },
        "locked": {
            "type": "github",
            "owner": "aaif-goose",
            "repo": "goose",
            "rev": "a" * 40,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
    })
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    with pytest.raises(RuntimeError, match="v<version> ref"):
        _run(updater.fetch_latest(object()))
