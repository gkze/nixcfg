"""Tests for concrete updater modules in overlays/packages."""

from __future__ import annotations

import asyncio
import json
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance, expect_not_none
from lib.tests._nix_ast import assert_nix_ast_equal, parse_nix_expr
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module_for_test as _load_module
from lib.tests._updater_helpers import run_async as _run
from lib.update import flake as update_flake
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters import strategies as updater_strategies
from lib.update.updaters.core import source_override_env
from lib.update.updaters.vendor_feeds import SparkleAppcastItem

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

HASH_A = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
HASH_B = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


def _require_hash_entries(payload: object) -> list[HashEntry]:
    raw = expect_instance(payload, list)
    return [expect_instance(entry, HashEntry) for entry in raw]


def _module_fixture(path: str, fixture_name: str) -> object:
    @pytest.fixture(scope="module", name=fixture_name)
    def _fixture() -> ModuleType:
        return _load_module(path, prefix=fixture_name.removesuffix("_module"))

    return _fixture


code_cursor_module = _module_fixture(
    "overlays/code-cursor/updater.py", "code_cursor_module"
)
datagrip_module = _module_fixture("overlays/datagrip/updater.py", "datagrip_module")
google_chrome_module = _module_fixture(
    "overlays/google-chrome/updater.py", "google_chrome_module"
)
goose_v8_module = _module_fixture("overlays/goose-v8/updater.py", "goose_v8_module")
netnewswire_module = _module_fixture(
    "packages/netnewswire/updater.py", "netnewswire_module"
)
sentry_cli_module = _module_fixture(
    "overlays/sentry-cli/updater.py", "sentry_cli_module"
)
conductor_module = _module_fixture("packages/conductor/updater.py", "conductor_module")
droid_module = _module_fixture("packages/droid/updater.py", "droid_module")
goose_desktop_module = _module_fixture(
    "packages/goose-desktop/updater.py", "goose_desktop_module"
)
scratch_module = _module_fixture("packages/scratch/updater.py", "scratch_module")
superconductor_module = _module_fixture(
    "packages/superconductor/updater.py", "superconductor_module"
)
tsgolint_module = _module_fixture("overlays/tsgolint/updater.py", "tsgolint_module")
sculptor_module = _module_fixture("packages/sculptor/updater.py", "sculptor_module")
neutils_module = _module_fixture("packages/neutils/updater.py", "neutils_module")


def test_goose_desktop_updater_uses_goose_cli_source_file(
    goose_desktop_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Goose desktop should follow the overlay-managed Goose CLI source version."""
    updater = goose_desktop_module.GooseDesktopUpdater()
    assert updater.companion_of == "goose-cli"
    source_file = tmp_path / "sources.json"
    entry = SourceEntry.model_validate({"version": "1.37.0", "hashes": []})

    monkeypatch.setattr(
        goose_desktop_module,
        "sources_file_for",
        lambda name: source_file if name == "goose-cli" else None,
    )
    monkeypatch.setattr(
        goose_desktop_module.update_sources,
        "load_source_entry",
        lambda path: entry if path == source_file else None,
    )

    assert _run(updater.fetch_latest(object())).version == "1.37.0"

    monkeypatch.setattr(goose_desktop_module, "sources_file_for", lambda _name: None)
    with pytest.raises(RuntimeError, match="goose-cli sources.json was not found"):
        _run(updater.fetch_latest(object()))

    missing_version = SourceEntry.model_validate({"hashes": []})
    monkeypatch.setattr(
        goose_desktop_module,
        "sources_file_for",
        lambda name: source_file if name == "goose-cli" else None,
    )
    monkeypatch.setattr(
        goose_desktop_module.update_sources,
        "load_source_entry",
        lambda _path: missing_version,
    )
    with pytest.raises(RuntimeError, match="missing a pinned version"):
        _run(updater.fetch_latest(object()))


def test_goose_desktop_updater_hashes_flake_package_pnpm_deps(
    goose_desktop_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dependency hash should target the package's fixed-output pnpm deps."""
    updater = goose_desktop_module.GooseDesktopUpdater()
    captured: dict[str, object] = {}

    async def _fake_compute_fixed_output_hash(
        source: str,
        expr: str,
        *,
        env: dict[str, str] | None = None,
        config: object | None = None,
    ) -> EventStream:
        captured.update({"source": source, "expr": expr, "env": env, "config": config})
        yield UpdateEvent.value(source, HASH_A)

    monkeypatch.setattr(
        "lib.update.nix.compute_fixed_output_hash",
        _fake_compute_fixed_output_hash,
    )

    events = _run(_collect(updater.fetch_hashes(VersionInfo("1.37.0"), object())))
    payload = _require_hash_entries(events[-1].payload)
    assert payload == [
        HashEntry.create("nodeModulesHash", HASH_A),
    ]
    assert captured["source"] == "goose-desktop"
    env = expect_instance(captured["env"], dict)
    override_payload = json.loads(env["UPDATE_SOURCE_OVERRIDES_JSON"])
    assert override_payload == {
        "goose-desktop": {
            "version": "1.37.0",
            "hashes": [
                {
                    "hashType": "nodeModulesHash",
                    "hash": updater.config.fake_hash,
                    "platform": "aarch64-darwin",
                }
            ],
        }
    }
    assert 'packages."aarch64-darwin"."goose-desktop".pnpmDeps' in str(captured["expr"])

    result = updater.build_result(VersionInfo("1.37.0"), payload)
    assert result.version == "1.37.0"
    assert result.hashes.entries == [
        HashEntry.create(
            "nodeModulesHash",
            HASH_A,
            platform="aarch64-darwin",
        )
    ]

    with pytest.raises(RuntimeError, match="expected structured hash entries"):
        updater.build_result(VersionInfo("1.37.0"), {"aarch64-darwin": HASH_A})


class _FakeHeadResponse:
    def __init__(
        self,
        *,
        status: int,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.url = url
        self.headers = headers or {}

    async def __aenter__(self) -> _FakeHeadResponse:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeHeadSession:
    def __init__(self, responses: list[_FakeHeadResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def head(
        self,
        url: str,
        *,
        allow_redirects: bool,
        timeout: object,
    ) -> _FakeHeadResponse:
        self.calls.append({
            "url": url,
            "allow_redirects": allow_redirects,
            "timeout": timeout,
        })
        return self.responses.pop(0)


def test_superconductor_updater_resolves_nightly_redirect(
    superconductor_module: ModuleType,
) -> None:
    """Superconductor should follow the mutable download endpoint to a stable URL."""
    updater = superconductor_module.SuperconductorUpdater()
    url = (
        "https://releases.superconductor.so/nightly/"
        "Superconductor-nightly-9bd387bf-arm64.dmg?signature=ignored"
    )
    session = _FakeHeadSession([
        _FakeHeadResponse(
            status=200,
            url=url,
            headers={"Last-Modified": "Fri, 12 Jun 2026 16:42:50 GMT"},
        )
    ])

    latest = _run(updater.fetch_latest(session))

    resolved_url = url.removesuffix("?signature=ignored")
    assert latest.version == "2026-06-12-9bd387bf"
    assert latest.metadata["asset_urls"] == {"aarch64-darwin": resolved_url}
    assert updater.get_download_url("aarch64-darwin", latest) == resolved_url
    assert session.calls[0]["url"] == updater.DISCOVERY_URL
    assert session.calls[0]["allow_redirects"] is True


def test_superconductor_updater_rejects_bad_metadata(
    superconductor_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid Superconductor redirect metadata should fail before hashing."""
    updater = superconductor_module.SuperconductorUpdater()

    with pytest.raises(RuntimeError, match="Could not parse"):
        updater._version_from_url(
            "https://example.com/Superconductor-nightly-arm64.dmg",
            "Fri, 12 Jun 2026 16:42:50 GMT",
        )
    with pytest.raises(RuntimeError, match="Missing Last-Modified"):
        updater._version_from_url(
            "https://example.com/Superconductor-nightly-9bd387bf-arm64.dmg",
            None,
        )

    failing_session = _FakeHeadSession([
        _FakeHeadResponse(
            status=500,
            url="https://example.com/failure.dmg",
        )
    ])
    with pytest.raises(RuntimeError, match="failed with HTTP 500"):
        _run(updater._fetch_resolved_artifact(failing_session, "aarch64-darwin"))

    monkeypatch.setattr(
        superconductor_module.SuperconductorUpdater,
        "PLATFORMS",
        {"aarch64-darwin": "arm64", "x86_64-darwin": "x64"},
    )
    mismatch_session = _FakeHeadSession([
        _FakeHeadResponse(
            status=200,
            url=(
                "https://releases.superconductor.so/nightly/"
                "Superconductor-nightly-9bd387bf-arm64.dmg"
            ),
            headers={"Last-Modified": "Fri, 12 Jun 2026 16:42:50 GMT"},
        ),
        _FakeHeadResponse(
            status=200,
            url=(
                "https://releases.superconductor.so/nightly/"
                "Superconductor-nightly-deadbeef-x64.dmg"
            ),
            headers={"Last-Modified": "Fri, 12 Jun 2026 16:42:50 GMT"},
        ),
    ])
    with pytest.raises(RuntimeError, match="mismatched versions"):
        _run(updater.fetch_latest(mismatch_session))


def test_code_cursor_updater_paths(
    code_cursor_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise Code Cursor platform URL and checksum helpers."""
    updater = code_cursor_module.CodeCursorUpdater()
    assert (
        object.__getattribute__(updater, "_api_url")("darwin-arm64")
        == "https://api2.cursor.sh/updates/download/golden/darwin-arm64/cursor/3.9"
    )
    platform_info = {
        nix_plat: {"downloadUrl": f"https://example.com/{api_plat}.zip"}
        for nix_plat, api_plat in updater.PLATFORMS.items()
    }
    info = VersionInfo(version="1.0.0", metadata={"platform_info": platform_info})

    assert (
        object.__getattribute__(updater, "_download_url")("darwin-arm64", info)
        == "https://example.com/darwin-arm64.zip"
    )

    async def _hashes(_name: str, urls: Iterable[str]) -> EventStream:
        url_map = dict.fromkeys(urls, HASH_A)
        yield UpdateEvent.status("code-cursor", "hashing")
        yield UpdateEvent.value("code-cursor", url_map)

    monkeypatch.setattr("lib.update.process.compute_url_hashes", _hashes)
    checksums = _run(updater.fetch_checksums(info, object()))
    assert set(checksums) == set(updater.PLATFORMS)
    assert all(v == HASH_A for v in checksums.values())


def test_code_cursor_fetch_latest_from_download_page(
    code_cursor_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor should derive the full version and commit from redirect targets."""
    updater = code_cursor_module.CodeCursorUpdater()
    api_urls = {
        api_platform: updater._api_url(api_platform)
        for api_platform in updater.PLATFORMS.values()
    }
    page = "".join(
        f'{{\\"downloadUrl\\":\\"{api_url}\\"}}' for api_url in api_urls.values()
    )
    commit = "042b3c1a4c53f2c3808067f519fbfc67b72cad8b"
    resolved_urls = {
        "darwin-arm64": (
            f"https://downloads.cursor.com/production/{commit}/darwin/arm64/"
            "Cursor-darwin-arm64.dmg"
        ),
        "darwin-x64": (
            f"https://downloads.cursor.com/production/{commit}/darwin/x64/"
            "Cursor-darwin-x64.dmg"
        ),
        "linux-arm64": (
            f"https://downloads.cursor.com/production/{commit}/linux/arm64/"
            "Cursor-3.9.16-aarch64.AppImage"
        ),
        "linux-x64": (
            f"https://downloads.cursor.com/production/{commit}/linux/x64/"
            "Cursor-3.9.16-x86_64.AppImage"
        ),
    }

    async def _fetch_url(_session: object, url: str, **_kwargs: object) -> bytes:
        assert url == updater.DOWNLOAD_PAGE
        return page.encode()

    async def _resolve_download_url(_session: object, api_url: str) -> str:
        api_platform = api_url.rsplit("/cursor/", maxsplit=1)[0].rsplit("/", 1)[1]
        return resolved_urls[api_platform]

    monkeypatch.setattr(code_cursor_module, "fetch_url", _fetch_url)
    monkeypatch.setattr(updater, "_resolve_download_url", _resolve_download_url)

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "3.9.16"
    assert latest.commit == commit
    assert latest.metadata["commitSha"] == commit
    assert (
        latest.metadata["platform_info"]["aarch64-linux"]["downloadUrl"]
        == (resolved_urls["linux-arm64"])
    )


def test_datagrip_updater_paths(
    datagrip_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise DataGrip release parsing and checksum retrieval."""
    updater = datagrip_module.DataGripUpdater()
    payload = {
        "DG": [
            {
                "version": "2025.1",
                "downloads": {
                    "mac": {"checksumLink": "https://c/mac", "link": "https://d/mac"},
                    "macM1": {"checksumLink": "https://c/m1", "link": "https://d/m1"},
                    "linuxARM64": {
                        "checksumLink": "https://c/a64",
                        "link": "https://d/a64",
                    },
                    "linux": {"checksumLink": "https://c/x64", "link": "https://d/x64"},
                },
            }
        ]
    }

    monkeypatch.setattr(
        datagrip_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=payload),
    )
    info = _run(updater.fetch_latest(object()))
    assert info.version == "2025.1"

    monkeypatch.setattr(
        datagrip_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result={"DG": []}),
    )
    with pytest.raises(RuntimeError, match="No DataGrip releases"):
        _run(updater.fetch_latest(object()))

    monkeypatch.setattr(
        datagrip_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result={"DG": [{"downloads": {}}]}),
    )
    with pytest.raises(RuntimeError, match="Missing DataGrip version"):
        _run(updater.fetch_latest(object()))

    urls_seen: dict[str, str] = {}

    async def _fetch_checksums(
        _session: object,
        urls: dict[str, str],
        parser: Callable[[bytes, str], str] | None = None,
    ) -> dict[str, str]:
        for platform, url in urls.items():
            urls_seen[platform] = parser(b"abcd  file", url) if parser else ""
        return dict.fromkeys(urls, "abcd")

    monkeypatch.setattr(updater, "_fetch_checksums_from_urls", _fetch_checksums)
    parsed_checksums = _run(updater.fetch_checksums(info, object()))
    assert parsed_checksums == dict.fromkeys(updater.PLATFORMS, "abcd")
    assert set(urls_seen) == set(updater.PLATFORMS)

    result = updater.build_result(
        info,
        {
            "x86_64-darwin": HASH_A,
            "x86_64-linux": HASH_A,
        },
    )
    urls = expect_not_none(result.urls)
    assert urls["x86_64-darwin"] == "https://d/mac"
    assert urls["x86_64-linux"] == "https://d/x64"


def test_google_chrome_updater_paths(
    google_chrome_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise Google Chrome release parsing edge cases."""
    updater = google_chrome_module.GoogleChromeUpdater()
    assert updater.materialize_when_current is True
    assert updater.PLATFORMS["aarch64-darwin"] == updater.PLATFORMS["x86_64-darwin"]
    monkeypatch.setattr(
        google_chrome_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[{"version": "133.0.1"}]),
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "133.0.1"

    monkeypatch.setattr(
        google_chrome_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[]),
    )
    with pytest.raises(RuntimeError, match="No Chrome releases"):
        _run(updater.fetch_latest(object()))

    monkeypatch.setattr(
        google_chrome_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[{}]),
    )
    with pytest.raises(RuntimeError, match="Missing version"):
        _run(updater.fetch_latest(object()))


def test_netnewswire_updater_paths(
    netnewswire_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise NetNewsWire appcast parsing and download URL selection."""
    updater = netnewswire_module.NetNewsWireUpdater()

    async def _items(*_args: object, **_kwargs: object):
        return (
            SparkleAppcastItem(
                "100",
                "6.2.1",
                "https://example.com/NetNewsWire.zip",
            ),
        )

    monkeypatch.setattr(updater_strategies, "fetch_sparkle_appcast_items", _items)
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "6.2.1"
    assert latest.metadata["url"] == "https://example.com/NetNewsWire.zip"

    assert (
        updater.get_download_url("aarch64-darwin", latest)
        == "https://example.com/NetNewsWire.zip"
    )

    async def _missing_version(*_args: object, **_kwargs: object):
        return (SparkleAppcastItem("100", None, "https://example.com/app.zip"),)

    monkeypatch.setattr(
        updater_strategies,
        "fetch_sparkle_appcast_items",
        _missing_version,
    )
    with pytest.raises(RuntimeError, match="Missing version"):
        _run(updater.fetch_latest(object()))

    async def _missing_url(*_args: object, **_kwargs: object):
        return (SparkleAppcastItem("100", "6.2.1", None),)

    monkeypatch.setattr(
        updater_strategies,
        "fetch_sparkle_appcast_items",
        _missing_url,
    )
    with pytest.raises(RuntimeError, match="Missing download URL"):
        _run(updater.fetch_latest(object()))


def test_goose_v8_updater_skips_unchanged_pinned_revision(
    goose_v8_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged pinned goose-v8 revisions should not trigger rehash churn."""
    updater = goose_v8_module.GooseV8Updater()
    current = SourceEntry.model_validate({
        "version": "0123456789abcdef0123456789abcdef01234567",
        "input": "goose-v8",
        "hashes": [
            {
                "hashType": "srcHash",
                "hash": "sha256-UtbHrrBQleMb0KMyuX+7ELJJos3la7ZPtYv9Ri+kBTw=",
            },
            {
                "hashType": "rustyV8ArchiveHash",
                "hash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                "platform": "x86_64-linux",
            },
            {
                "hashType": "rustyV8BindingHash",
                "hash": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                "platform": "x86_64-linux",
            },
        ],
    })
    monkeypatch.setattr(
        "lib.update.nix.compute_drv_fingerprint",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unexpected call")),
    )

    assert (
        _run(
            object.__getattribute__(updater, "_is_latest")(
                current,
                VersionInfo(version=current.version, metadata={}),
            )
        )
        is True
    )
    assert (
        _run(
            object.__getattribute__(updater, "_is_latest")(
                current,
                VersionInfo(version="changed", metadata={}),
            )
        )
        is False
    )

    current_without_linux_artifacts = current.model_copy(
        update={
            "hashes": [{"hashType": "srcHash", "hash": current.hashes.entries[0].hash}]
        }
    )
    assert (
        _run(
            object.__getattribute__(updater, "_is_latest")(
                current_without_linux_artifacts,
                VersionInfo(version=current.version, metadata={}),
            )
        )
        is False
    )


def test_sentry_cli_updater_paths(
    sentry_cli_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise Sentry CLI release parsing and fixed-output hashing."""
    updater = sentry_cli_module.SentryCliUpdater()
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(0, result={"tag_name": "v9.9.9"}),
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "v9.9.9"

    src_expr = object.__getattribute__(updater, "_src_nix_expr")("v9.9.9")
    cargo_expr = object.__getattribute__(updater, "_cargo_nix_expr")("v9.9.9", HASH_A)
    assert_nix_ast_equal(
        src_expr,
        object.__getattribute__(updater, "_src_nix_expression")("v9.9.9"),
    )
    cargo_call = expect_instance(
        parse_nix_expr(cargo_expr), sentry_cli_module.FunctionCall
    )
    assert_nix_ast_equal(
        cargo_call.name,
        sentry_cli_module.identifier_attr_path(
            "pkgs", "rustPlatform", "fetchCargoVendor"
        ),
    )

    monkeypatch.setattr(sentry_cli_module, "_build_nix_expr", lambda expr: expr)

    call_count = 0

    async def _fixed_hash(_name: str, expr: str, **_kwargs: object) -> EventStream:
        nonlocal call_count
        call_count += 1
        yield UpdateEvent.status("sentry-cli", f"build {expr[:5]}")
        yield UpdateEvent.value("sentry-cli", HASH_A if call_count == 1 else HASH_B)

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    events = _run(_collect(updater.fetch_hashes(latest, object())))
    values = [e for e in events if e.kind == UpdateEventKind.VALUE]
    payload = _require_hash_entries(values[-1].payload)
    assert payload[0].hash_type == "srcHash"
    assert payload[1].hash_type == "cargoHash"

    async def _no_hash(_name: str, _expr: str, **_kwargs: object) -> EventStream:
        if False:
            yield UpdateEvent.status("x", "y")

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _no_hash)
    with pytest.raises(RuntimeError, match="Missing srcHash output"):
        _run(_collect(updater.fetch_hashes(latest, object())))


def test_conductor_updater_paths(
    conductor_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise Conductor filename parsing and resolved asset URL persistence."""
    updater = conductor_module.ConductorUpdater()

    assert (
        updater._version_from_header('attachment; filename="Conductor_1.2.3_arm64.dmg"')
        == "1.2.3"
    )
    assert (
        updater._url_without_query(
            "https://cdn.crabnebula.app/asset/example?from=latest#fragment"
        )
        == "https://cdn.crabnebula.app/asset/example"
    )

    class _HeadResponse:
        def __init__(self, *, status: int, header: str, url: str) -> None:
            self.status = status
            self.headers = {"Content-Disposition": header}
            self.url = url

        async def __aenter__(self) -> _HeadResponse:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    class _HeadSession:
        def __init__(self, *, status: int, header: str, url: str) -> None:
            self.status = status
            self.header = header
            self.url = url
            self.calls: list[tuple[str, bool, object]] = []

        def head(
            self,
            url: str,
            *,
            allow_redirects: bool,
            timeout: object,
        ) -> _HeadResponse:
            self.calls.append((url, allow_redirects, timeout))
            return _HeadResponse(
                status=self.status,
                header=self.header,
                url=self.url,
            )

    head_session = _HeadSession(
        status=200,
        header='attachment; filename="Conductor_1.2.3_arm64.dmg"',
        url="https://cdn.crabnebula.app/asset/conductor-arm64.dmg?token=ephemeral",
    )
    resolved = _run(updater._fetch_resolved_artifact(head_session, "aarch64-darwin"))
    assert resolved == conductor_module._ResolvedArtifact(
        "1.2.3",
        "https://cdn.crabnebula.app/asset/conductor-arm64.dmg",
    )
    [(discovery_url, allow_redirects, timeout)] = head_session.calls
    assert discovery_url == (
        "https://cdn.crabnebula.app/download/melty/conductor/latest/platform/"
        "dmg-aarch64"
    )
    assert allow_redirects is True
    assert timeout.total == updater.config.default_timeout

    failed_head_session = _HeadSession(
        status=500,
        header='attachment; filename="Conductor_1.2.3_x64.dmg"',
        url="https://cdn.crabnebula.app/asset/conductor-x64.dmg",
    )
    with pytest.raises(RuntimeError, match="x86_64-darwin failed with HTTP 500"):
        _run(updater._fetch_resolved_artifact(failed_head_session, "x86_64-darwin"))

    async def _artifact(_session: object, platform: str) -> object:
        return conductor_module._ResolvedArtifact(
            "1.2.3",
            f"https://cdn.crabnebula.app/asset/{platform}",
        )

    monkeypatch.setattr(
        updater,
        "_fetch_resolved_artifact",
        _artifact,
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "1.2.3"
    assert updater.get_download_url("aarch64-darwin", latest) == (
        "https://cdn.crabnebula.app/asset/aarch64-darwin"
    )
    assert updater.get_download_url("x86_64-darwin", latest) == (
        "https://cdn.crabnebula.app/asset/x86_64-darwin"
    )
    assert _run(updater._is_latest(None, latest)) is False

    with pytest.raises(RuntimeError, match="Could not parse version"):
        updater._version_from_header("attachment; filename=oops")

    async def _mismatched_artifact(_session: object, platform: str) -> object:
        version = "1.2.3" if platform == "aarch64-darwin" else "1.2.4"
        return conductor_module._ResolvedArtifact(
            version,
            f"https://cdn.crabnebula.app/asset/{platform}",
        )

    monkeypatch.setattr(
        updater,
        "_fetch_resolved_artifact",
        _mismatched_artifact,
    )
    with pytest.raises(RuntimeError, match="mismatched versions"):
        _run(updater.fetch_latest(object()))


def test_droid_updater_paths(
    droid_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise Droid version parsing, checksum lookup, and URL building."""
    updater = droid_module.DroidUpdater()
    assert object.__getattribute__(updater, "_download_url")(
        "x86_64-linux", "1.0.0"
    ).endswith("/1.0.0/linux/x64/droid")
    monkeypatch.setattr(
        droid_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b'#!/bin/sh\nVER="2.3.4"\n'),
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "2.3.4"

    monkeypatch.setattr(
        droid_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b"no version"),
    )
    with pytest.raises(RuntimeError, match="Could not parse version"):
        _run(updater.fetch_latest(object()))

    captured: dict[str, str] = {}

    async def _fetch_checksums(
        _session: object, urls: dict[str, str], **_kwargs: object
    ) -> dict[str, str]:
        captured.update(urls)
        return dict.fromkeys(urls, "sum")

    monkeypatch.setattr(updater, "_fetch_checksums_from_urls", _fetch_checksums)
    checksums = _run(
        updater.fetch_checksums(VersionInfo(version="2.3.4", metadata={}), object())
    )
    assert checksums["x86_64-linux"] == "sum"
    assert captured["x86_64-linux"].endswith("/2.3.4/linux/x64/droid.sha256")

    built = updater.build_result(
        VersionInfo(version="2.3.4", metadata={}), {"x86_64-linux": HASH_A}
    )
    built_urls = expect_not_none(built.urls)
    assert built_urls["x86_64-linux"].endswith("/2.3.4/linux/x64/droid")


def test_scratch_updater_paths(
    scratch_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise Scratch flake-backed version discovery and hash handling."""
    updater = scratch_module.ScratchUpdater()

    real_get_flake_input_node = update_flake.get_flake_input_node
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda name: (
            SimpleNamespace(locked=SimpleNamespace(rev="f" * 40))
            if name == "scratch"
            else real_get_flake_input_node(name)
        ),
    )
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_version",
        lambda _node: "9.9.9",
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "9.9.9"
    assert latest.metadata["commit"] == "f" * 40

    npm_expr = object.__getattribute__(updater, "_expr_for_npm_deps")()
    cargo_expr = object.__getattribute__(updater, "_expr_for_cargo_vendor")()
    seen_exprs: list[str] = []

    async def _fixed_hash(_name: str, expr: str, **_kwargs: object) -> EventStream:
        seen_exprs.append(expr)
        yield UpdateEvent.value("scratch", HASH_A if len(seen_exprs) == 1 else HASH_B)

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    events = _run(_collect(updater.fetch_hashes(latest, object())))
    payload = _require_hash_entries(
        [e for e in events if e.kind == UpdateEventKind.VALUE][-1].payload
    )
    assert_nix_ast_equal(seen_exprs[0], npm_expr)
    assert_nix_ast_equal(seen_exprs[1], cargo_expr)
    assert [entry.hash_type for entry in payload] == ["npmDepsHash", "cargoHash"]

    async def _no_hash(_name: str, _expr: str, **_kwargs: object) -> EventStream:
        if False:
            yield UpdateEvent.status("scratch", "none")

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _no_hash)
    with pytest.raises(RuntimeError, match="Missing npmDepsHash output"):
        _run(_collect(updater.fetch_hashes(latest, object())))

    built = updater.build_result(
        latest,
        [HashEntry.create("npmDepsHash", HASH_A)],
    )
    assert built.input == "scratch"
    assert built.commit == "f" * 40


def test_tsgolint_updater_paths(
    tsgolint_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise tsgolint release parsing and override payload shape."""
    updater = tsgolint_module.TsgolintUpdater()

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(0, result={"tag_name": "v0.21.0"}),
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "0.21.0"

    env = source_override_env(
        "tsgolint",
        version=latest.version,
        src_hash=HASH_A,
        dependency_hash_type="vendorHash",
        dependency_hash=HASH_B,
    )
    payload = json.loads(env["UPDATE_SOURCE_OVERRIDES_JSON"])
    assert payload == {
        "tsgolint": {
            "version": "0.21.0",
            "hashes": [
                {"hashType": "srcHash", "hash": HASH_A},
                {"hashType": "vendorHash", "hash": HASH_B},
            ],
        }
    }

    fake_current = SourceEntry(
        version=latest.version,
        hashes=[
            HashEntry.create("srcHash", HASH_A),
            HashEntry.create("vendorHash", HASH_B),
        ],
    )
    real_current = SourceEntry(
        version=latest.version,
        hashes=[
            HashEntry.create("srcHash", HASH_B),
            HashEntry.create("vendorHash", HASH_B),
        ],
    )
    assert _run(updater._is_latest(fake_current, latest)) is False
    assert _run(updater._is_latest(real_current, latest)) is True


def test_neutils_updater_emits_generated_artifact_and_src_hash(
    neutils_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neutils should refresh build.zig.zon.nix alongside the source hash."""
    updater = neutils_module.NeutilsUpdater()

    async def _render(_info: object, _session: object) -> EventStream:
        yield UpdateEvent.value("neutils", "# generated\n")

    monkeypatch.setattr(updater, "_render_build_zig_zon_nix", _render)
    monkeypatch.setattr(
        "lib.update.paths.package_dir_for",
        lambda _name: REPO_ROOT / "packages" / "neutils",
    )

    async def _fixed_hash(_name: str, _expr: str, **_kwargs: object) -> EventStream:
        yield UpdateEvent.value("neutils", HASH_A)

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    latest = VersionInfo(version="0.7.2")
    events = _run(_collect(updater.fetch_hashes(latest, object())))

    artifact_event = next(
        event for event in events if event.kind == UpdateEventKind.ARTIFACT
    )
    payload = expect_instance(artifact_event.payload, list)
    artifact = expect_instance(payload[0], GeneratedArtifact)
    assert artifact.path == REPO_ROOT / "packages" / "neutils" / "build.zig.zon.nix"
    assert artifact.content == "# generated\n"

    hashes = _require_hash_entries(
        [event for event in events if event.kind == UpdateEventKind.VALUE][-1].payload
    )
    assert hashes == [HashEntry.create("srcHash", HASH_A)]
    assert updater.materialize_when_current is True
    assert updater.generated_artifact_files == ("build.zig.zon.nix",)


def test_sculptor_updater_paths(
    sculptor_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise Sculptor Last-Modified parsing and fallback behavior."""
    updater = sculptor_module.SculptorUpdater()

    monkeypatch.setattr(
        sculptor_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0, result={"Last-Modified": "Tue, 20 Feb 2024 12:34:56 GMT"}
        ),
    )
    latest = _run(updater.fetch_latest(object()))
    assert latest.version == "2024-02-20"

    monkeypatch.setattr(
        sculptor_module,
        "parsedate_to_datetime",
        lambda _value: (_ for _ in ()).throw(ValueError("bad date")),
    )
    monkeypatch.setattr(
        sculptor_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"Last-Modified": "invalid-date"}),
    )
    fallback = _run(updater.fetch_latest(object()))
    assert fallback.version == "invalid-da"

    monkeypatch.setattr(
        sculptor_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"Last-Modified": ""}),
    )
    with pytest.raises(RuntimeError, match="No Last-Modified header"):
        _run(updater.fetch_latest(object()))


def test_code_cursor_download_page_requires_all_platform_links(
    code_cursor_module: ModuleType,
) -> None:
    """Reject download pages that omit expected platforms, skipping unknown ones."""
    updater = code_cursor_module.CodeCursorUpdater()
    partial_page = '"'.join((
        updater._api_url("darwin-arm64"),
        # Unknown platforms in the page are skipped, not collected.
        updater._api_url("windows-x64"),
    ))
    with pytest.raises(RuntimeError, match="missing platform links"):
        updater._extract_download_api_urls(partial_page)


def test_code_cursor_resolve_download_url_follows_redirects(
    code_cursor_module: ModuleType,
) -> None:
    """Resolve API URLs via redirect Location and reject non-redirect answers."""
    updater = code_cursor_module.CodeCursorUpdater()
    api_url = updater._api_url("darwin-arm64")

    class _HeadResponse:
        def __init__(self, status: int, headers: dict[str, str]) -> None:
            self.status = status
            self.headers = headers

        async def __aenter__(self) -> _HeadResponse:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

    class _Session:
        def __init__(self, response: _HeadResponse) -> None:
            self._response = response

        def head(self, url: str, **_kwargs: object) -> _HeadResponse:
            assert url == api_url
            return self._response

    redirect = _HeadResponse(302, {"Location": "/production/abc/darwin/arm64/C.dmg"})
    resolved = _run(updater._resolve_download_url(_Session(redirect), api_url))
    assert resolved == "https://api2.cursor.sh/production/abc/darwin/arm64/C.dmg"

    with pytest.raises(RuntimeError, match="Expected Cursor download redirect"):
        _run(updater._resolve_download_url(_Session(_HeadResponse(200, {})), api_url))

    with pytest.raises(RuntimeError, match="did not include Location"):
        _run(updater._resolve_download_url(_Session(_HeadResponse(302, {})), api_url))


def test_code_cursor_require_uniform_regex_value_rejects_mixed_urls(
    code_cursor_module: ModuleType,
) -> None:
    """Reject resolved URL sets that disagree on the extracted value."""
    import re

    updater = code_cursor_module.CodeCursorUpdater()
    pattern = re.compile(r"/(?P<commit>[0-9a-f]{3})/")
    urls = {
        "darwin-arm64": "https://d/abc/x.dmg",
        "darwin-x64": "https://d/def/y.dmg",
    }
    with pytest.raises(RuntimeError, match="Unable to resolve one Cursor commit"):
        updater._require_uniform_regex_value(urls, pattern, "commit", context="commit")
