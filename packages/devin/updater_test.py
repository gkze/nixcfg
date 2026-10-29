"""Devin Desktop must read its version and artifact URL from the vendor API."""

import pytest

from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import UpdateContext

_RELEASE_URL = (
    "https://windsurf-stable.codeium.com/api/update/darwin-arm64/stable/latest"
)
_ARTIFACT = (
    "https://windsurf-stable.codeiumdata.com/darwin-arm64/stable/0534/"
    "Devin-darwin-arm64-3.12.0.zip"
)


def _updater_with(monkeypatch: pytest.MonkeyPatch, payload: object):
    module = load_repo_module("packages/devin/updater.py", "devin_latest_test")

    async def fake_fetch(_session: object, endpoint: str, **_kwargs: object):
        assert endpoint == _RELEASE_URL
        return payload

    monkeypatch.setattr(module, "fetch_json", fake_fetch)
    return module.DevinUpdater()


def test_devin_reads_app_version_and_artifact_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``windsurfVersion`` names the app release; the payload carries the artifact."""
    updater = _updater_with(
        monkeypatch,
        {
            "version": "053434014dbdb7a662a14e38752946a0dafb97f2",
            "productVersion": "1.127.0",
            "url": _ARTIFACT,
            "windsurfVersion": "3.12.0",
        },
    )

    latest = run_async(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )

    assert latest.version == "3.12.0"
    assert updater.get_download_url("aarch64-darwin", latest) == _ARTIFACT


def test_devin_rejects_a_payload_without_app_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The VS Code commit and base version must never stand in for the app release."""
    updater = _updater_with(
        monkeypatch,
        {
            "version": "053434014dbdb7a662a14e38752946a0dafb97f2",
            "productVersion": "1.127.0",
            "url": _ARTIFACT,
        },
    )

    with pytest.raises(TypeError, match="windsurfVersion"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))


def test_devin_rejects_an_empty_app_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty version field fails like a missing one."""
    updater = _updater_with(monkeypatch, {"url": _ARTIFACT, "windsurfVersion": "  "})

    with pytest.raises(RuntimeError, match="Missing version"):
        run_async(updater.fetch_latest(object(), context=UpdateContext(current=None)))
