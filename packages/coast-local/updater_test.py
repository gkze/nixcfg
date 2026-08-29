"""Behavioral and package-shape tests for Coast Local."""

import json
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from collections.abc import Mapping

_DOWNLOAD_URL = "https://dmg.cdn-coast.app/Coast%20Local.dmg"
_DISCOVERY_VERSION = "20260817.0123456789abcdef0123456789abcdef"
_MULTIPART_DISCOVERY_VERSION = (
    "20260817.7ef30ec3cbce996e6102e127601f8cafd16a9c624c0a4d72de4c3bbd328cfbbc"
)
_SRI_HASH = "sha256-5Krz8O5QUJpc7sXDTxzGMrejFJvF7Et3iNmMviXwGcs="
_CONTENT_VERSION = (
    "sha256-e4aaf3f0ee50509a5ceec5c34f1cc632b7a3149bc5ec4b7788d98cbe25f019cb"
)
# Synthetic metadata exercises the strict discovery contract. Live CDN headers could
# not be refreshed in this sandbox; the persisted identity above is the full hash
# observed by the daemon-capable fetcher, not this provisional discovery token.
_VALID_HEADERS = {
    "Content-Encoding": "identity",
    "Content-Length": "73318751",
    "Content-Type": "application/x-apple-diskimage",
    "ETag": '"0123456789abcdef0123456789abcdef"',
    "Last-Modified": "Mon, 17 Aug 2026 19:34:49 GMT",
}


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/coast-local/updater.py",
        "coast_local_updater_test",
    )


@dataclass(slots=True)
class _FakeResponse:
    headers: Mapping[str, str] = field(default_factory=lambda: dict(_VALID_HEADERS))
    status: int = 200
    reason: str = "OK"
    url: str = _DOWNLOAD_URL

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.response = response or _FakeResponse()
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.response


def test_coast_local_discovers_public_dmg_without_device_headers() -> None:
    """Discovery must use one direct, anonymous HEAD request to the public DMG."""
    updater = _load_module().CoastLocalUpdater()
    session = _FakeSession()

    info = _run(updater.fetch_latest(session))

    assert info == VersionInfo(_DISCOVERY_VERSION)
    assert updater.PLATFORMS == {"aarch64-darwin": _DOWNLOAD_URL}
    assert updater.supported_platforms == ("aarch64-darwin",)
    [(method, url, kwargs)] = session.calls
    assert (method, url) == ("HEAD", _DOWNLOAD_URL)
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"] == {
        "Accept": "application/x-apple-diskimage, application/octet-stream",
        "Accept-Encoding": "identity",
        "User-Agent": updater.config.default_user_agent,
    }
    assert kwargs["timeout"].total == updater.config.default_timeout


def test_coast_local_accepts_the_cdns_strong_multipart_etag() -> None:
    """Discovery accepts the CDN's opaque multipart ETag and still rehashes."""
    updater = _load_module().CoastLocalUpdater()
    headers = dict(_VALID_HEADERS)
    headers["ETag"] = '"1b97eb0299c78f5d6ce92ab854c583cd-10"'

    info = _run(updater.fetch_latest(_FakeSession(_FakeResponse(headers=headers))))

    assert info == VersionInfo(_MULTIPART_DISCOVERY_VERSION)
    assert not _run(updater._is_latest(None, info))


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({"Content-Type": ""}, "missing Content-Type"),
        ({"Content-Type": "text/html"}, "Unexpected .* Content-Type"),
        ({"Content-Encoding": "gzip"}, "Unexpected .* Content-Encoding"),
        ({"Content-Length": "unknown"}, "Invalid .* Content-Length"),
        ({"Content-Length": "1024"}, "Implausible .* Content-Length"),
        ({"ETag": 'W/"a18b459968f76d323c7b3f1ca64a7f83"'}, "bounded strong ETag"),
        ({"ETag": '"contains a space"'}, "bounded strong ETag"),
        ({"Last-Modified": "not-a-date"}, "Invalid .* Last-Modified"),
        ({"Last-Modified": "Wed, 29 Jul 2026 19:34:49"}, "lacks a timezone"),
    ],
)
def test_coast_local_rejects_weak_or_ambiguous_dmg_metadata(
    headers: dict[str, str],
    message: str,
) -> None:
    """Headers must prove one plausible, unencoded, content-identified DMG."""
    module = _load_module()
    response_headers = dict(_VALID_HEADERS)
    response_headers.update(headers)

    with pytest.raises(RuntimeError, match=message):
        module._parse_artifact_identity(response_headers)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_FakeResponse(status=403, reason="Forbidden"), "HTTP 403 Forbidden"),
        (_FakeResponse(status=302, reason="Found"), "HTTP 302 Found"),
        (
            _FakeResponse(url="https://example.test/Coast%20Local.dmg"),
            "unexpected URL",
        ),
    ],
)
def test_coast_local_rejects_http_failures_and_redirects(
    response: _FakeResponse,
    message: str,
) -> None:
    """Discovery must never follow a redirect or accept a gated response."""
    updater = _load_module().CoastLocalUpdater()

    with pytest.raises(RuntimeError, match=message):
        _run(updater.fetch_latest(_FakeSession(response)))


def test_coast_local_persists_full_sha256_content_identity() -> None:
    """The mutable URL must be versioned and pinned by its complete SHA-256."""
    updater = _load_module().CoastLocalUpdater()

    result = updater.build_result(
        VersionInfo(_DISCOVERY_VERSION),
        {"aarch64-darwin": _SRI_HASH},
    )

    assert result.version == _CONTENT_VERSION
    assert result.hashes.to_json() == {"aarch64-darwin": _SRI_HASH}
    assert result.urls == {"aarch64-darwin": _DOWNLOAD_URL}
    assert not _run(updater._is_latest(result, VersionInfo(_DISCOVERY_VERSION)))


@pytest.mark.parametrize(
    ("version", "hashes", "message"),
    [
        ("1.0", {"aarch64-darwin": _SRI_HASH}, "Invalid .* discovery version"),
        (
            _DISCOVERY_VERSION,
            {
                "aarch64-darwin": _SRI_HASH,
                "x86_64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            },
            "one platform-independent SHA-256 hash",
        ),
        (
            _DISCOVERY_VERSION,
            {"aarch64-darwin": "sha256-A="},
            "Invalid Coast Local SRI hash",
        ),
        (
            _DISCOVERY_VERSION,
            {"aarch64-darwin": "sha256-QQ=="},
            "Invalid Coast Local SHA-256 digest length",
        ),
    ],
)
def test_coast_local_result_fails_closed_on_ambiguous_identity(
    version: str,
    hashes: dict[str, str],
    message: str,
) -> None:
    """Malformed discovery or differing artifacts must not reach sources.json."""
    updater = _load_module().CoastLocalUpdater()

    with pytest.raises(RuntimeError, match=message):
        updater.build_result(VersionInfo(version), hashes)


def test_coast_local_package_preserves_bundle_and_exposes_vendor_cli() -> None:
    """Use the shared no-fixup DMG helper and link the bundled Coast CLI."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/coast-local/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(arguments.values)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkDmgApp7zz"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "pname").value,
        StringPrimitive(value="coast-local"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "bundleName").value,
        StringPrimitive(value="Coast Local.app"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "createBin").value,
        Primitive(value=False),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "mainProgram").value,
        StringPrimitive(value="coast"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
    assert "codesignApp" not in bindings


def test_coast_local_sources_pin_retained_official_dmg() -> None:
    """The checked-in source is the content-addressed official public DMG."""
    sources = json.loads(
        (REPO_ROOT / "packages/coast-local/sources.json").read_text(encoding="utf-8")
    )

    assert sources == {
        "hashes": {"aarch64-darwin": _SRI_HASH},
        "urls": {"aarch64-darwin": _DOWNLOAD_URL},
        "version": _CONTENT_VERSION,
    }
