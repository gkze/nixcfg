"""Behavioral tests for the Factory desktop release updater."""

from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.updaters import VersionInfo

_HASH = "sha256-XvFsRD+vPvNIST+8qrgI9x38gGqPyjPzyNUXaBdKm+Q="


def _load_module() -> ModuleType:
    return load_repo_module("packages/factory/updater.py", "factory_updater_test")


@pytest.mark.parametrize("feed", [b"0.154.0\n", b"v0.154.0"])
def test_factory_resolves_latest_and_builds_versioned_arch_urls(
    monkeypatch: pytest.MonkeyPatch,
    feed: bytes,
) -> None:
    """The official feed should produce stable per-architecture release paths."""
    module = _load_module()
    updater = module.FactoryUpdater()

    async def _fetch_url(_session: object, url: str, **kwargs: object) -> bytes:
        assert url == updater.LATEST_URL
        assert kwargs["request_timeout"] == updater.config.default_timeout
        assert kwargs["config"] == updater.config
        return feed

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    info = _run(updater.fetch_latest(object()))
    result = updater.build_result(
        info,
        dict.fromkeys(updater.PLATFORMS, _HASH),
    )

    assert info == VersionInfo(version="0.154.0")
    assert result.urls == {
        "aarch64-darwin": (
            "https://downloads.factory.ai/factory-desktop/releases/0.154.0/"
            "darwin/arm64/Factory-0.154.0-arm64.dmg"
        ),
        "x86_64-darwin": (
            "https://downloads.factory.ai/factory-desktop/releases/0.154.0/"
            "darwin/x64/Factory-0.154.0-x64.dmg"
        ),
    }
    assert updater.materialize_when_current is True


@pytest.mark.parametrize(
    "feed",
    [
        b"",
        b"latest",
        b"0",
        b"0.154.0 extra",
        b"0.154.0\n0.155.0",
    ],
)
def test_factory_rejects_malformed_latest_feed(
    monkeypatch: pytest.MonkeyPatch,
    feed: bytes,
) -> None:
    """Unexpected feed contents must fail before constructing download URLs."""
    module = _load_module()
    updater = module.FactoryUpdater()

    async def _fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return feed

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    with pytest.raises(RuntimeError, match="Could not parse Factory version"):
        _run(updater.fetch_latest(object()))
