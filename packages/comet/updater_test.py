"""Behavioral tests for the Comet browser release updater."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import ModuleType

import pytest

from lib.tests._updater_helpers import collect_events, load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent
from lib.update.updaters import VersionInfo

_HASH = "sha256-yAYdnv44sf89OSRK6JIUyiJJlAm/NJdoPDJ3dF1vIvE="


def _load_module() -> ModuleType:
    return load_repo_module("packages/comet/updater.py", "comet_updater_test")


def _artifact_url(version: str, signature: str = "test") -> str:
    return (
        "https://pplx-browser-binaries.a0adf9b772aecba4fa8883581f3c9180."
        f"r2.cloudflarestorage.com/{version}/comet_latest.dmg?"
        f"X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature={signature}"
    )


@dataclass(slots=True)
class _FakeResponse:
    status: int = 307
    reason: str = "Temporary Redirect"
    headers: dict[str, str] = field(default_factory=dict)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        return self._responses[url]


def test_comet_resolves_latest_version_from_platform_download_redirects() -> None:
    """GET redirects should discover the installer version without downloading it."""
    module = _load_module()
    updater = module.CometUpdater()
    responses = {
        url: _FakeResponse(
            headers={"Location": _artifact_url("150.0.7871.228", signature=platform)}
        )
        for platform, url in updater.PLATFORMS.items()
    }
    session = _FakeSession(responses)

    info = _run(updater.fetch_latest(session))

    assert info == VersionInfo(version="150.0.7871.228")
    assert [(method, url) for method, url, _kwargs in session.calls] == [
        ("GET", url) for url in updater.PLATFORMS.values()
    ]
    for _method, _url, kwargs in session.calls:
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"].total == updater.config.default_timeout


@pytest.mark.parametrize(
    "location",
    [
        (
            "http://pplx-browser-binaries.a0adf9b772aecba4fa8883581f3c9180."
            "r2.cloudflarestorage.com/150.0.7871.228/comet_latest.dmg"
        ),
        "https://example.test/150.0.7871.228/comet_latest.dmg",
        (
            "https://pplx-browser-binaries.a0adf9b772aecba4fa8883581f3c9180."
            "r2.cloudflarestorage.com/150.0.7871/comet_latest.dmg"
        ),
        (
            "https://pplx-browser-binaries.a0adf9b772aecba4fa8883581f3c9180."
            "r2.cloudflarestorage.com/150.0.7871.228/other.dmg"
        ),
    ],
)
def test_comet_rejects_untrusted_or_malformed_artifact_redirects(
    location: str,
) -> None:
    """Only the expected HTTPS R2 object shape may supply Comet's version."""
    module = _load_module()

    with pytest.raises(RuntimeError, match="Could not extract Comet version"):
        module.CometUpdater._parse_artifact(location)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            _FakeResponse(status=200, reason="OK"),
            "Expected Comet download redirect",
        ),
        (_FakeResponse(), "did not include Location"),
    ],
)
def test_comet_reports_invalid_download_responses(
    response: _FakeResponse,
    message: str,
) -> None:
    """Discovery should fail before hashing when the download is not a redirect."""
    module = _load_module()
    updater = module.CometUpdater()
    [download_url] = list(updater.PLATFORMS.values())[:1]
    session = _FakeSession({download_url: response})

    with pytest.raises(RuntimeError, match=message):
        _run(updater._resolve_latest_url(session, download_url))


def test_comet_rejects_platform_version_mismatches() -> None:
    """Both official download routes must identify one shared Comet release."""
    module = _load_module()
    updater = module.CometUpdater()
    responses = {
        url: _FakeResponse(headers={"Location": _artifact_url(version)})
        for url, version in zip(
            updater.PLATFORMS.values(),
            ("150.0.7871.228", "151.0.7871.229"),
            strict=True,
        )
    }

    with pytest.raises(RuntimeError, match="mismatched versions"):
        _run(updater.fetch_latest(_FakeSession(responses)))


def test_comet_hashes_fresh_signed_artifact_and_persists_canonical_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash the discovered object once, but keep expiring signatures out of pins."""
    module = _load_module()
    updater = module.CometUpdater()
    responses = {
        url: _FakeResponse(
            headers={"Location": _artifact_url("150.0.7871.228", signature=platform)}
        )
        for platform, url in updater.PLATFORMS.items()
    }
    captured_urls: dict[str, str] = {}

    async def _hash_urls(
        source_name: str,
        urls_by_key: dict[str, str],
        *,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        assert config is updater.config
        assert source_name == "comet"
        captured_urls.update(urls_by_key)
        yield UpdateEvent.value(
            source_name,
            dict.fromkeys(urls_by_key, _HASH),
        )

    monkeypatch.setattr(module, "stream_url_hash_mapping", _hash_urls)
    info = VersionInfo(version="150.0.7871.228")

    events = _run(collect_events(updater.fetch_hashes(info, _FakeSession(responses))))
    result = updater.build_result(
        info,
        dict.fromkeys(updater.PLATFORMS, _HASH),
    )

    assert len(events) == 1
    assert set(captured_urls) == set(updater.PLATFORMS)
    assert len(set(captured_urls.values())) == 1
    assert next(iter(captured_urls.values())).startswith(
        "https://pplx-browser-binaries."
    )
    assert result.urls == updater.PLATFORMS


def test_comet_rejects_artifact_version_drift_before_hashing() -> None:
    """A release change between resolution phases must restart instead of mispinning."""
    module = _load_module()
    updater = module.CometUpdater()
    responses = {
        url: _FakeResponse(headers={"Location": _artifact_url("151.0.7871.229")})
        for url in updater.PLATFORMS.values()
    }

    with pytest.raises(RuntimeError, match="changed from 150.0.7871.228"):
        _run(
            collect_events(
                updater.fetch_hashes(
                    VersionInfo(version="150.0.7871.228"),
                    _FakeSession(responses),
                )
            )
        )


def test_comet_rechecks_same_version_download_hashes() -> None:
    """Comet's mutable download routes must be rehashed for same-version changes."""
    module = _load_module()

    assert module.CometUpdater.materialize_when_current is True
