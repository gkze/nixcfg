"""Behavioral tests for the Aside browser release updater."""

from dataclasses import dataclass, field
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import DownloadUrlMetadata

_ARTIFACT_URL = "https://releases.aside.com/dev-updater/Aside-1.0.813.1.dmg"
_HASH = "sha256-zJPW/9XZMrlCOnN9zQ8Ev4Rj+r3/ZXI4Tb+QYzV4UjM="


def _load_module() -> ModuleType:
    return load_repo_module("packages/aside/updater.py", "aside_updater_test")


@dataclass(slots=True)
class _FakeResponse:
    status: int = 302
    reason: str = "Found"
    headers: dict[str, str] = field(default_factory=dict)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def head(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self.response


def test_aside_resolves_and_persists_versioned_official_artifact() -> None:
    """Discovery should pin the immutable DMG behind Aside's public endpoint."""
    module = _load_module()
    updater = module.AsideUpdater()
    session = _FakeSession(_FakeResponse(headers={"Location": _ARTIFACT_URL}))

    info = _run(updater.fetch_latest(session))
    result = updater.build_result(
        info,
        dict.fromkeys(updater.PLATFORMS, _HASH),
    )

    assert info == VersionInfo(
        version="1.0.813.1",
        metadata=DownloadUrlMetadata(url=_ARTIFACT_URL),
    )
    assert result.urls == dict.fromkeys(updater.PLATFORMS, _ARTIFACT_URL)
    assert session.calls[0][0] == module._DOWNLOAD_URL
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][1]["timeout"].total == updater.config.default_timeout


@pytest.mark.parametrize(
    "url",
    [
        "http://releases.aside.com/dev-updater/Aside-1.0.813.1.dmg",
        "https://example.test/dev-updater/Aside-1.0.813.1.dmg",
        "https://releases.aside.com/other/Aside-1.0.813.1.dmg",
        "https://releases.aside.com/dev-updater/Aside-latest.dmg",
        f"{_ARTIFACT_URL}?signature=temporary",
    ],
)
def test_aside_rejects_untrusted_or_unversioned_redirects(url: str) -> None:
    """Only the official stable versioned artifact shape may be persisted."""
    module = _load_module()

    with pytest.raises(RuntimeError, match="Could not extract Aside version"):
        module.AsideUpdater._parse_artifact_url(url)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            _FakeResponse(status=200, reason="OK"),
            "Expected Aside download redirect",
        ),
        (_FakeResponse(), "omitted Location"),
    ],
)
def test_aside_reports_invalid_download_endpoint_responses(
    response: _FakeResponse,
    message: str,
) -> None:
    """A non-redirect or missing location should fail before artifact hashing."""
    module = _load_module()

    with pytest.raises(RuntimeError, match=message):
        _run(module.AsideUpdater().fetch_latest(_FakeSession(response)))
