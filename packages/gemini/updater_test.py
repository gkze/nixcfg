"""Behavioral and package-shape tests for Gemini for macOS."""

import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._nix_source import nix_source_fragment_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._source_metadata import (
    assert_https_url,
    assert_platform_source_entry,
    assert_release_version,
)
from lib.tests._updater_helpers import collect_events, load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import DownloadUrlMetadata

_VERSION = "1.99.2.791"
_URL = "https://dl.google.com/release2/j33ro/release/Gemini.dmg"
_HASH = "sha256-79JM4YYzTSCdfXeSIzItva+kffynOfpawClzJ5a1oEw="
_APP_ID = "com.google.geminimacos"
_PREFIX = b")]}'\n"
_EMPTY_VERSION = "0.0.0.0"  # Omaha sentinel, not a bind address.  # noqa: S104


def _load_module() -> ModuleType:
    return load_repo_module("packages/gemini/updater.py", "gemini_updater_test")


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
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.response


def _omaha_response(
    *,
    version: object = _VERSION,
    app_status: object = "ok",
    update_status: object = "ok",
    appid: object = _APP_ID,
) -> bytes:
    return (
        _PREFIX
        + json.dumps({
            "response": {
                "server": "prod",
                "protocol": "4.0",
                "apps": [
                    {
                        "appid": appid,
                        "status": app_status,
                        "updatecheck": {
                            "status": update_status,
                            "nextversion": version,
                            "pipelines": [
                                {
                                    "pipeline_id": "full-release",
                                    "operations": [
                                        {
                                            "type": "download",
                                            "out": {"sha256": "ab" * 32},
                                            "urls": [
                                                "https://dl.google.com/release2/gemini/"
                                                "immutable/current.crx3"
                                            ],
                                        },
                                        {
                                            "type": "crx3",
                                            "path": f"Gemini-{version}.dmg",
                                        },
                                    ],
                                }
                            ],
                        },
                    }
                ],
            }
        }).encode()
    )


def test_gemini_cross_checks_omaha_version_and_official_download_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater should persist one release-scoped Google DMG URL and hash."""
    module = _load_module()
    updater = module.GeminiUpdater()
    session = _FakeSession(_FakeResponse(_omaha_response()))
    page_calls: list[tuple[object, str, dict[str, object]]] = []

    async def _fetch_url(
        passed_session: object,
        url: str,
        **kwargs: object,
    ) -> bytes:
        page_calls.append((passed_session, url, kwargs))
        return f'<a href="{_URL}">Download</a><a href="{_URL}">Again</a>'.encode()

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    info = _run(updater.fetch_latest(session))
    result = updater.build_result(info, {"aarch64-darwin": _HASH})

    assert info == VersionInfo(
        version=_VERSION,
        metadata=DownloadUrlMetadata(url=_URL),
    )
    assert result.version == _VERSION
    assert result.urls == {"aarch64-darwin": _URL}
    assert result.hashes.to_json() == {"aarch64-darwin": _HASH}
    assert updater.materialize_when_current is True
    assert updater.supported_platforms == ("aarch64-darwin",)
    assert page_calls == [
        (
            session,
            updater.DOWNLOAD_PAGE_URL,
            {
                "request_timeout": updater.config.default_timeout,
                "config": updater.config,
            },
        )
    ]
    assert len(session.calls) == 1
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == updater.UPDATE_URL
    assert kwargs["headers"] == {
        "User-Agent": updater.config.default_user_agent,
        "Content-Type": "application/json",
        "X-Goog-Update-Interactivity": "fg",
        "X-Goog-Update-AppId": _APP_ID,
        "X-Goog-Update-Updater": "nixcfg-0",
    }
    assert kwargs["json"] == updater._request_body()
    assert kwargs["json"] == {
        "request": {
            "@os": "mac",
            "@updater": "nixcfg",
            "acceptformat": "crx3,download,puff,run,xz,zucc",
            "apps": [
                {
                    "ap": "m1-prod",
                    "appid": _APP_ID,
                    "enabled": True,
                    "updatecheck": {},
                    "version": _EMPTY_VERSION,
                }
            ],
            "arch": "arm64",
            "dedup": "cr",
            "domainjoined": False,
            "ismachine": False,
            "os": {
                "arch": "arm64",
                "platform": "Mac OS X",
                "version": "15.0",
            },
            "prodversion": "0",
            "protocol": "4.0",
            "testsource": "nixcfg-updater",
            "updaterversion": "0",
        }
    }
    assert kwargs["allow_redirects"] is True
    assert kwargs["timeout"].total == updater.config.default_timeout


def test_gemini_rehashes_the_current_pin_during_a_stale_omaha_rollout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cohort rollback must preserve, but still rehash, a newer checked-in pin."""
    module = _load_module()
    updater = module.GeminiUpdater()
    stale_version = "1.96.4.775"
    stale_url = "https://dl.google.com/release2/stale/release/Gemini.dmg"
    stale_hash = "sha256-jBct4+g6lLdXW4CL9d+hlaY7UB5V69nqKlaIGpse9dQ="
    refreshed_hash = _HASH
    current = SourceEntry(
        version=_VERSION,
        hashes={"aarch64-darwin": stale_hash},
        urls={"aarch64-darwin": _URL},
    )
    session = _FakeSession(_FakeResponse(_omaha_response(version=stale_version)))
    hashed_infos: list[VersionInfo] = []

    async def _fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return f'<a href="{stale_url}">Download</a>'.encode()

    async def _fetch_hashes(
        _self: object,
        info: VersionInfo,
        _session: object,
        **_kwargs: object,
    ):
        hashed_infos.append(info)
        yield UpdateEvent.value(
            "gemini",
            {"aarch64-darwin": refreshed_hash},
        )

    monkeypatch.setattr(module, "fetch_url", _fetch_url)
    monkeypatch.setattr(module.GeminiUpdater, "fetch_hashes", _fetch_hashes)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    events = _run(collect_events(updater.update_stream(current, session)))

    effective_info = VersionInfo(
        version=_VERSION,
        metadata=DownloadUrlMetadata(url=_URL),
    )
    assert hashed_infos == [effective_info]
    assert events[-1] == UpdateEvent.result(
        "gemini",
        SourceEntry(
            version=_VERSION,
            hashes={"aarch64-darwin": refreshed_hash},
            urls={"aarch64-darwin": _URL},
        ),
    )


@pytest.mark.parametrize(
    ("current", "upstream_version"),
    [
        (None, "1.0.0.0"),
        (SourceEntry(version=None, hashes={}), "1.0.0.0"),
        (_VERSION, _VERSION),
        (_VERSION, "2.0.0.0"),
    ],
)
def test_gemini_accepts_missing_equal_or_newer_pins(
    current: SourceEntry | str | None,
    upstream_version: str,
) -> None:
    """Only a strictly older Omaha candidate should preserve the current pin."""
    module = _load_module()
    entry = (
        SourceEntry(version=current, hashes={"aarch64-darwin": _HASH})
        if isinstance(current, str)
        else current
    )
    upstream = VersionInfo(
        version=upstream_version,
        metadata=DownloadUrlMetadata(url=_URL),
    )

    assert module._effective_version_info(entry, upstream) is upstream


@pytest.mark.parametrize("urls", [None, {}, {"aarch64-darwin": ""}])
def test_gemini_refuses_to_guess_the_url_for_a_newer_current_pin(
    urls: dict[str, str] | None,
) -> None:
    """A stale cohort cannot safely supply metadata for a newer pinned release."""
    module = _load_module()
    current = SourceEntry(version=_VERSION, hashes={}, urls=urls)
    stale = VersionInfo(
        version="1.96.4.775",
        metadata=DownloadUrlMetadata(url="https://example.com/stale.dmg"),
    )

    with pytest.raises(RuntimeError, match="without its current DMG URL"):
        module._effective_version_info(current, stale)


def test_gemini_reports_omaha_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP errors should fail version discovery before a pin is emitted."""
    module = _load_module()
    updater = module.GeminiUpdater()
    response = _FakeResponse(b"unavailable", status=503, reason="Service Unavailable")

    async def _fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return _URL.encode()

    monkeypatch.setattr(module, "fetch_url", _fetch_url)

    with pytest.raises(
        RuntimeError,
        match="Omaha request failed with HTTP 503 Service Unavailable",
    ):
        _run(updater.fetch_latest(_FakeSession(response)))


@pytest.mark.parametrize(
    ("payload", "exception", "message"),
    [
        (b"{}", RuntimeError, "omitted its anti-XSSI prefix"),
        (_PREFIX + b"not-json", RuntimeError, "was not valid JSON"),
        (_PREFIX + b"\xff", RuntimeError, "was not valid JSON"),
        (_PREFIX + b'"not-an-object"', TypeError, "Expected JSON object"),
        (_PREFIX + b"{}", TypeError, "response.response"),
        (
            _PREFIX + b'{"response":{"apps":"not-a-list"}}',
            TypeError,
            "response.response.apps",
        ),
        (
            _PREFIX + b'{"response":{"apps":["not-an-object"]}}',
            TypeError,
            "Gemini Omaha response app",
        ),
        (_omaha_response(appid="other.app"), RuntimeError, "0 matching apps"),
        (
            _PREFIX
            + json.dumps({
                "response": {
                    "apps": [
                        {
                            "appid": _APP_ID,
                            "status": "ok",
                            "updatecheck": {
                                "status": "ok",
                                "manifest": {"version": _VERSION},
                            },
                        },
                        {
                            "appid": _APP_ID,
                            "status": "ok",
                            "updatecheck": {
                                "status": "ok",
                                "manifest": {"version": _VERSION},
                            },
                        },
                    ]
                }
            }).encode(),
            RuntimeError,
            "2 matching apps",
        ),
        (_omaha_response(app_status="error"), RuntimeError, "app returned status"),
        (_omaha_response(app_status=1), TypeError, "string field 'status'"),
        (
            _omaha_response(update_status="noupdate"),
            RuntimeError,
            "updatecheck returned status",
        ),
        (_omaha_response(update_status=1), TypeError, "string field 'status'"),
        (_omaha_response(version=None), TypeError, "string field 'nextversion'"),
        (_omaha_response(version="1.2.3.4.5"), RuntimeError, "invalid version"),
        (_omaha_response(version="1.2.beta.4"), RuntimeError, "invalid version"),
        (
            _omaha_response(version="4294967296.0.0.0"),
            RuntimeError,
            "invalid version",
        ),
    ],
)
def test_gemini_rejects_malformed_or_unsuccessful_omaha_responses(
    payload: bytes,
    exception: type[Exception],
    message: str,
) -> None:
    """Malformed and unsuccessful Omaha responses must fail closed."""
    module = _load_module()

    with pytest.raises(exception, match=message):
        module.GeminiUpdater._parse_version(payload)


@pytest.mark.parametrize(
    "page",
    [
        b"no download",
        b"https://example.test/release2/j33ro/release/Gemini.dmg",
        b"http://dl.google.com/release2/j33ro/release/Gemini.dmg",
        (
            b"https://dl.google.com/release2/first/release/Gemini.dmg "
            b"https://dl.google.com/release2/second/release/Gemini.dmg"
        ),
    ],
)
def test_gemini_rejects_missing_or_ambiguous_official_downloads(
    page: bytes,
) -> None:
    """Only one strict first-party release-scoped Gemini DMG may be pinned."""
    module = _load_module()

    with pytest.raises(RuntimeError, match="official DMG URLs"):
        module.GeminiUpdater._parse_download_url(page)


def test_gemini_package_preserves_the_vendor_bundle() -> None:
    """The package should use the shared DMG helper without fixups or re-signing."""
    source = Path(REPO_ROOT / "packages/gemini/default.nix").read_text(encoding="utf-8")
    package = expect_instance(parse_nix_expr(source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    bindings = binding_map(arguments.values)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkDmgApp"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "pname").value,
        StringPrimitive(value="gemini"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "appName").value,
        StringPrimitive(value="Gemini"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "sourceName").value,
        StringPrimitive(value="Gemini.dmg"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontFixup").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
    post_install = expect_instance(
        expect_binding(arguments.values, "postInstallApp").value,
        IndentedString,
    )
    assert_nix_ast_equal(
        post_install,
        r"""
        ''
          plist="$out/Applications/Gemini.app/Contents/Info.plist"
          test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = \
            "com.google.GeminiMacOS"
          test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = \
            "${selfSource.version}"
          test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist")" = \
            "${selfSource.version}"
        ''
        """,
    )
    post_install_shell = parse_shell(indented_string_body(post_install.rebuild()))
    assert command_texts(post_install_shell, "test") == [
        "test \"$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "
        '"$plist")" = \\\n      "com.google.GeminiMacOS"',
        "test \"$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "
        '"$plist")" = \\\n      "__NIX_INTERP__"',
        "test \"$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "
        '"$plist")" = \\\n      "__NIX_INTERP__"',
    ]
    assert "codesignApp" not in bindings


def test_gemini_system_route_refuses_future_downgrades() -> None:
    """A vendor self-update must not be replaced by a stale managed pin."""
    routing = expect_instance(
        nix_source_fragment_expr(
            "home/george/work.nix",
            "  routing = ",
            ";\n  projection =",
        ),
        AttributeSet,
    )
    gemini = expect_instance(
        expect_binding(routing.values, "gemini").value, AttributeSet
    )

    assert_nix_ast_equal(
        expect_binding(gemini.values, "package").value,
        "pkgs.gemini",
    )
    assert_nix_ast_equal(
        expect_binding(gemini.values, "scope").value,
        StringPrimitive(value="system"),
    )
    assert_nix_ast_equal(
        expect_binding(gemini.values, "preventDowngrade").value,
        Primitive(value=True),
    )


def test_gemini_sources_pin_the_official_release_dmg() -> None:
    """Checked-in metadata must remain a complete official Omaha-selected DMG."""
    source = SourceEntry.model_validate_json(
        (REPO_ROOT / "packages/gemini/sources.json").read_text(encoding="utf-8")
    )
    assert_release_version(source.version)
    _hashes, urls = assert_platform_source_entry(
        source,
        platforms={"aarch64-darwin"},
    )
    url = urls["aarch64-darwin"]
    assert_https_url(url, host="dl.google.com")
    assert "/release2/" in url
    assert url.endswith("/Gemini.dmg")
