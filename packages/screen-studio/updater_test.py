"""Behavioral and package-shape tests for Screen Studio."""

import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_VERSION = "3.7.5-4595"
_URLS = {
    "aarch64-darwin": (
        "https://screenstudioassets.com/releases/3.7.5-4595/"
        "Screen%20Studio-3.7.5-4595-arm64-mac.zip"
    ),
    "x86_64-darwin": (
        "https://screenstudioassets.com/releases/3.7.5-4595/"
        "Screen%20Studio-3.7.5-4595-mac.zip"
    ),
}
_HASHES = {
    "aarch64-darwin": "sha256-KHUwAF24yj9QUQHLwbs4aYazv741G2yxWCIieaSMxu8=",
    "x86_64-darwin": "sha256-cQuEzo/bdhPxui3gb4k0cGvF5272Adxw1kscu+z+Qsc=",
}


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/screen-studio/updater.py",
        "screen_studio_updater_test",
    )


@dataclass(slots=True)
class _FakeResponse:
    payload: bytes
    status: int = 200
    reason: str = "OK"

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read(self) -> bytes:
        return self.payload


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        headers = kwargs["headers"]
        assert isinstance(headers, dict)
        architecture = headers["x-screen-studio-architecture"]
        assert isinstance(architecture, str)
        return self._responses[architecture]


def _response(url: str, version: str = _VERSION) -> _FakeResponse:
    return _FakeResponse(json.dumps({"url": url, "name": version}).encode())


def test_screen_studio_resolves_official_per_architecture_zips() -> None:
    """The authenticated app feed contract should resolve immutable official ZIPs."""
    module = _load_module()
    updater = module.ScreenStudioUpdater()
    session = _FakeSession({
        "arm64": _response(_URLS["aarch64-darwin"]),
        "x64": _response(_URLS["x86_64-darwin"]),
    })

    info = _run(updater.fetch_latest(session))
    result = updater.build_result(info, _HASHES)

    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata(asset_urls=_URLS),
    )
    assert result.version == _VERSION
    assert result.urls == _URLS
    assert result.hashes.to_json() == _HASHES
    assert [method for method, _url, _kwargs in session.calls] == ["GET", "GET"]
    for platform, (method, url, kwargs) in zip(
        updater.PLATFORMS,
        session.calls,
        strict=True,
    ):
        assert method == "GET"
        assert url == updater.FEED_URL
        assert kwargs["headers"] == {
            "User-Agent": updater.config.default_user_agent,
            "x-screen-studio-architecture": updater.PLATFORMS[platform],
            "x-screen-studio-platform": "darwin",
            "x-screen-studio-version": "0.0.0",
            "x-screen-studio-updates-channel": "stable",
            "x-screen-studio-machine-id": "nixcfg-updater",
        }
        assert kwargs["allow_redirects"] is True
        assert kwargs["timeout"].total == updater.config.default_timeout


def test_screen_studio_rejects_mismatched_feed_versions() -> None:
    """One release transaction must never mix independently published versions."""
    module = _load_module()
    updater = module.ScreenStudioUpdater()
    stale_intel_url = _URLS["x86_64-darwin"].replace(_VERSION, "3.7.4-4498")
    session = _FakeSession({
        "arm64": _response(_URLS["aarch64-darwin"]),
        "x64": _response(stale_intel_url, "3.7.4-4498"),
    })

    with pytest.raises(RuntimeError, match="returned mismatched versions"):
        _run(updater.fetch_latest(session))


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        (
            "aarch64-darwin",
            _URLS["aarch64-darwin"].replace("https://", "http://"),
        ),
        (
            "aarch64-darwin",
            _URLS["aarch64-darwin"].replace(
                "screenstudioassets.com",
                "example.test",
            ),
        ),
        ("aarch64-darwin", f"{_URLS['aarch64-darwin']}?mutable=1"),
        ("aarch64-darwin", f"{_URLS['aarch64-darwin']}#mutable"),
        ("aarch64-darwin", _URLS["x86_64-darwin"]),
        ("x86_64-darwin", _URLS["aarch64-darwin"]),
        (
            "x86_64-darwin",
            _URLS["x86_64-darwin"].replace(".zip", ".dmg"),
        ),
    ],
)
def test_screen_studio_rejects_untrusted_or_wrong_architecture_urls(
    platform: str,
    url: str,
) -> None:
    """Only immutable HTTPS ZIPs with the expected architecture shape are trusted."""
    updater = _load_module().ScreenStudioUpdater()

    with pytest.raises(RuntimeError, match=f"invalid {platform} artifact URL"):
        updater._parse_artifact_url(platform, url)


def test_screen_studio_reports_feed_http_failure() -> None:
    """HTTP failures should identify the architecture-specific discovery request."""
    updater = _load_module().ScreenStudioUpdater()
    failed = _FakeResponse(b"unavailable", status=503, reason="Service Unavailable")
    session = _FakeSession({"arm64": failed, "x64": failed})

    with pytest.raises(
        RuntimeError,
        match="request for aarch64-darwin failed with HTTP 503 Service Unavailable",
    ):
        _run(updater.fetch_latest(session))


def test_screen_studio_rejects_feed_name_that_disagrees_with_artifact() -> None:
    """Squirrel metadata and the immutable artifact path must name one version."""
    updater = _load_module().ScreenStudioUpdater()
    session = _FakeSession({
        "arm64": _response(_URLS["aarch64-darwin"], "3.7.4-4498"),
        "x64": _response(_URLS["x86_64-darwin"]),
    })

    with pytest.raises(RuntimeError, match="does not match its aarch64-darwin"):
        _run(updater.fetch_latest(session))


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"\xff",
        b'"not-an-object"',
        b'{"name":"3.7.5-4595"}',
        json.dumps({"url": _URLS["aarch64-darwin"]}).encode(),
    ],
)
def test_screen_studio_rejects_malformed_feed_payload(payload: bytes) -> None:
    """Invalid JSON, non-object JSON, and missing URLs should fail before hashing."""
    updater = _load_module().ScreenStudioUpdater()
    response = _FakeResponse(payload)
    session = _FakeSession({"arm64": response, "x64": response})

    with pytest.raises((RuntimeError, TypeError)):
        _run(updater.fetch_latest(session))


def test_screen_studio_package_preserves_the_signed_vendor_bundle() -> None:
    """The ZIP package must copy Screen Studio.app without fixups or signing hooks."""
    source = Path(REPO_ROOT / "packages/screen-studio/default.nix").read_text(
        encoding="utf-8"
    )
    package = expect_instance(parse_nix_expr(source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(arguments.values)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkZipApp"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "pname").value,
        StringPrimitive(value="screen-studio"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "appName").value,
        StringPrimitive(value="Screen Studio"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" "x86_64-darwin" ]',
    )
    assert "postInstallApp" not in bindings
    assert "codesignApp" not in bindings


def test_screen_studio_sources_pin_official_architecture_specific_zips() -> None:
    """Checked-in source metadata should match the stable official artifacts."""
    sources = json.loads(
        (REPO_ROOT / "packages/screen-studio/sources.json").read_text(encoding="utf-8")
    )

    assert sources == {
        "hashes": _HASHES,
        "urls": _URLS,
        "version": _VERSION,
    }
