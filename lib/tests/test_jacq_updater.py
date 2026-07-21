"""Behavioral tests for the Jacq release updater."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.updaters import VersionInfo


def _load_module() -> ModuleType:
    return load_repo_module("packages/jacq/updater.py", "jacq_updater_test")


@dataclass(slots=True)
class _FakeResponse:
    url: str
    status: int = 200
    reason: str = "OK"

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        return self._response


def test_jacq_resolves_latest_redirect_to_versioned_dmg() -> None:
    """The official latest URL should discover the immutable Jacq release URL."""
    module = _load_module()
    updater = module.JacqUpdater()
    session = _FakeSession(
        _FakeResponse(
            url=(
                "https://downloads.jacquard.dev/releases/0.3.2186/"
                "Jacq-0.3.2186-arm64.dmg"
            )
        )
    )

    info = _run(updater.fetch_latest(session))

    assert info == VersionInfo(version="0.3.2186")
    assert updater.get_download_url("aarch64-darwin", info) == (
        "https://downloads.jacquard.dev/releases/0.3.2186/Jacq-0.3.2186-arm64.dmg"
    )
    assert [(method, url) for method, url, _kwargs in session.calls] == [
        ("HEAD", "https://downloads.jacquard.dev/latest/mac-arm64.dmg")
    ]
    [(_method, _url, kwargs)] = session.calls
    assert kwargs["allow_redirects"] is True
    assert kwargs["timeout"].total == updater.config.default_timeout


@pytest.mark.parametrize(
    "resolved_url",
    [
        "http://downloads.jacquard.dev/releases/1.2.3/Jacq-1.2.3-arm64.dmg",
        "https://example.test/releases/1.2.3/Jacq-1.2.3-arm64.dmg",
        "https://downloads.jacquard.dev/latest/mac-arm64.dmg",
        "https://downloads.jacquard.dev/releases/1.2.3/Jacq-1.2.4-arm64.dmg",
    ],
)
def test_jacq_rejects_untrusted_or_malformed_latest_redirects(
    resolved_url: str,
) -> None:
    """Only Jacq's immutable HTTPS release URL shape should supply a version."""
    module = _load_module()
    updater = module.JacqUpdater()

    with pytest.raises(RuntimeError, match="Could not extract Jacq version"):
        _run(updater.fetch_latest(_FakeSession(_FakeResponse(url=resolved_url))))


def test_jacq_reports_latest_endpoint_http_failures() -> None:
    """HTTP failures should identify the discovery endpoint before hashing starts."""
    module = _load_module()
    updater = module.JacqUpdater()
    session = _FakeSession(
        _FakeResponse(
            url="https://downloads.jacquard.dev/latest/mac-arm64.dmg",
            status=503,
            reason="Service Unavailable",
        )
    )

    with pytest.raises(
        RuntimeError,
        match=("Failed to resolve Jacq latest URL .*: HTTP 503 Service Unavailable"),
    ):
        _run(updater.fetch_latest(session))
