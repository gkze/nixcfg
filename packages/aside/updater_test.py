"""Behavioral tests for the Aside browser release updater."""

from dataclasses import dataclass, field
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_ARM_URL = "https://releases.aside.com/dev-updater/Aside-1.0.1022.1-arm64.dmg"
_X64_URL = "https://releases.aside.com/dev-updater/Aside-1.0.1022.1-x64.dmg"
_LANDING_URL = "https://aside.com/download?os=mac"
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
    """Mimic the live endpoint: only an ``arch`` query redirects to a DMG."""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def head(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self.responses.get(
            url, _FakeResponse(headers={"Location": _LANDING_URL})
        )


def _live_shaped_session(module: ModuleType) -> _FakeSession:
    return _FakeSession({
        f"{module._DOWNLOAD_URL}?arch=arm64": _FakeResponse(
            headers={"Location": _ARM_URL}
        ),
        f"{module._DOWNLOAD_URL}?arch=x64": _FakeResponse(
            headers={"Location": _X64_URL}
        ),
    })


def test_aside_resolves_and_persists_per_architecture_artifacts() -> None:
    """Each platform pins the immutable DMG for its own architecture."""
    module = _load_module()
    updater = module.AsideUpdater()
    session = _live_shaped_session(module)

    info = _run(updater.fetch_latest(session, context=UpdateContext(current=None)))
    result = updater.build_result(info, dict.fromkeys(updater.PLATFORMS, _HASH))

    assert info == VersionInfo(
        version="1.0.1022.1",
        metadata=AssetURLsMetadata({
            "aarch64-darwin": _ARM_URL,
            "x86_64-darwin": _X64_URL,
        }),
    )
    assert result.urls == {"aarch64-darwin": _ARM_URL, "x86_64-darwin": _X64_URL}
    assert sorted(url for url, _ in session.calls) == [
        f"{module._DOWNLOAD_URL}?arch=arm64",
        f"{module._DOWNLOAD_URL}?arch=x64",
    ]
    for _url, kwargs in session.calls:
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"].total == updater.config.default_timeout


def test_aside_rejects_the_html_download_page_redirect() -> None:
    """The arch-less endpoint now lands on HTML; that is never an artifact."""
    module = _load_module()

    with pytest.raises(RuntimeError, match="Could not extract Aside"):
        _run(
            module.AsideUpdater().fetch_latest(
                _FakeSession({}), context=UpdateContext(current=None)
            )
        )


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        ("aarch64-darwin", _ARM_URL.replace("https://", "http://")),
        ("aarch64-darwin", _ARM_URL.replace("releases.aside.com", "example.test")),
        ("aarch64-darwin", _ARM_URL.replace("/dev-updater/", "/other/")),
        ("aarch64-darwin", _X64_URL),
        ("x86_64-darwin", _ARM_URL),
        (
            "aarch64-darwin",
            "https://releases.aside.com/dev-updater/Aside-1.0.928.1.dmg",
        ),
        (
            "aarch64-darwin",
            "https://releases.aside.com/dev-updater/Aside-latest-arm64.dmg",
        ),
        ("aarch64-darwin", f"{_ARM_URL}?signature=temporary"),
        ("aarch64-darwin", f"{_ARM_URL}#frag"),
        ("aarch64-darwin", _LANDING_URL),
    ],
)
def test_aside_rejects_untrusted_wrong_arch_or_unversioned_redirects(
    platform: str, url: str
) -> None:
    """Only the official versioned artifact for that architecture is accepted."""
    module = _load_module()

    with pytest.raises(RuntimeError, match=f"Could not extract Aside {platform}"):
        module.AsideUpdater._parse_artifact_url(platform, url)


def test_aside_rejects_mismatched_architecture_versions() -> None:
    """Both architectures must resolve to the same release."""
    module = _load_module()
    session = _live_shaped_session(module)
    session.responses[f"{module._DOWNLOAD_URL}?arch=x64"] = _FakeResponse(
        headers={"Location": _X64_URL.replace("1.0.1022.1", "1.0.1021.1")}
    )

    with pytest.raises(RuntimeError, match="mismatched versions"):
        _run(
            module.AsideUpdater().fetch_latest(
                session, context=UpdateContext(current=None)
            )
        )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_FakeResponse(status=200, reason="OK"), "Expected Aside download redirect"),
        (_FakeResponse(), "omitted Location"),
    ],
)
def test_aside_reports_invalid_download_endpoint_responses(
    response: _FakeResponse,
    message: str,
) -> None:
    """A non-redirect or missing location should fail before artifact hashing."""
    module = _load_module()
    session = _FakeSession({
        f"{module._DOWNLOAD_URL}?arch=arm64": response,
        f"{module._DOWNLOAD_URL}?arch=x64": response,
    })

    with pytest.raises(RuntimeError, match=message):
        _run(
            module.AsideUpdater().fetch_latest(
                session, context=UpdateContext(current=None)
            )
        )
