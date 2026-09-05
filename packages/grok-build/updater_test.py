"""Behavioral, package, and update-ownership tests for Grok Build."""

import hashlib
import json
import plistlib
import shutil
import struct
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.asar_integrity import (
    asar_header_hash,
    check_info_plist_hash,
    read_packed_file,
)
from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._source_metadata import (
    assert_https_url,
    assert_platform_source_entry,
    assert_release_version,
    assert_url_contains_version,
)
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

_PACKAGE_DIR = REPO_ROOT / "packages/grok-build"
_VERSION = "0.1.1550-gc21c6ca"
_ARTIFACT_URL = (
    "https://storage.googleapis.com/grok-build-public-artifacts/desktop/stable/"
    "Grok-0.1.1550-gc21c6ca-arm64-mac.zip"
)
_HASH = "sha256-F8R+Za+ZoJbutWtkwDZTxB4C88/ece6lskSTL9hcjN0="
_BLOCK_SIZE = 64
_QUIT_INTERCEPT = (
    b'e.preventDefault(), !he && (he = !0, r.sendToRenderer("app:quit-request"));'
)
_SENTRY_INTEGRATION_FILTER = (
    b'...e.filter((e) => e.name !== "Http" && e.name !== "NodeFetch" '
    b'&& e.name !== "Undici"),'
)


def _quit_policy(
    *,
    allow: bytes = b"me",
    pending: bytes = b"he",
    updater: bytes = b"ae",
    ipc: bytes = b"Ie",
    app: bytes = b"O",
    event: bytes = b"e",
    router: bytes = b"r",
    will_event: bytes = b"e",
    cleanup: bytes = b"ve",
    coordinator: bytes = b"pO",
    worker: bytes = b"w",
    shutdown: bytes = b"i",
    exit_delay: bytes = b"delay",
    timer_event: bytes = b"resolve",
    error: bytes = b"error",
    auth_store: bytes = b"re",
    auth_status: bytes = b"WH",
    windows: bytes = b"Oe",
    window: bytes = b"window",
) -> bytes:
    """Build the complete quit/confirm/cancel/cleanup policy under test."""
    return (
        b"let "
        + allow
        + b" = !1, "
        + pending
        + b" = !1;"
        + updater
        + b".onQuitForUpdate(() => {"
        + allow
        + b" = !0;}),"
        + ipc
        + b'.on("app:quit-confirm", () => {'
        + allow
        + b" || ("
        + pending
        + b" = !1, "
        + allow
        + b" = !0, "
        + app
        + b".quit());}),"
        + ipc
        + b'.on("app:quit-cancel", () => {'
        + pending
        + b" = !1;}),"
        + app
        + b'.on("before-quit", ('
        + event
        + b") => {if (!"
        + allow
        + b"){if ("
        + auth_store
        + b".getState().status !== "
        + auth_status
        + b".Authenticated){"
        + allow
        + b" = !0;return;}if (!"
        + app
        + b".isPackaged || process.env.VITE_DEV_SERVER_URL?.trim()){"
        + allow
        + b" = !0;return;}if (!"
        + windows
        + b".getAllWindows().some(("
        + window
        + b") => !"
        + window
        + b".isDestroyed())){"
        + allow
        + b" = !0;return;}"
        + event
        + b".preventDefault(), !"
        + pending
        + b" && ("
        + pending
        + b" = !0, "
        + router
        + b'.sendToRenderer("app:quit-request"));}});'
        + b"let "
        + cleanup
        + b" = "
        + coordinator
        + b"({cleanup: () => {"
        + updater
        + b".installPendingUpdateOnQuit(),"
        + updater
        + b".dispose(),"
        + worker
        + b".kill();},scheduleExit: () => {let "
        + exit_delay
        + b" = new Promise(("
        + timer_event
        + b") => setTimeout("
        + timer_event
        + b", 0));Promise.race(["
        + shutdown
        + b".shutdown(), "
        + exit_delay
        + b"]).finally(() => "
        + app
        + b".exit());},onError: ("
        + error
        + b') => console.error("[main] quit cleanup error:", '
        + error
        + b")});"
        + app
        + b'.on("will-quit", ('
        + will_event
        + b") => {"
        + will_event
        + b".preventDefault(), "
        + cleanup
        + b"();})"
    )


_QUIT_POLICY = _quit_policy()
_RENAMED_QUIT_POLICY = _quit_policy(
    allow=b"allowQuit",
    pending=b"pending",
    updater=b"updater",
    ipc=b"ipc",
    app=b"electronApp",
    event=b"event",
    router=b"bridge",
    will_event=b"willEvent",
    cleanup=b"cleanup",
    coordinator=b"coordinateExit",
    worker=b"worker",
    shutdown=b"shutdownClient",
    exit_delay=b"exitDelay",
    timer_event=b"resolveDelay",
    error=b"failure",
    auth_store=b"authStore",
    auth_status=b"AuthStatus",
    windows=b"windows",
    window=b"window",
)


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/grok-build/updater.py",
        "grok_build_updater_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/grok-build/patch_renderer_ota.py",
        "grok_build_renderer_patch_test",
    )


def _main_payload(module: ModuleType) -> bytes:
    return (
        b"before;"
        + module._APP_UPDATER_ENABLE
        + b"updater;"
        + module._MANIFEST_LOOKUP
        + b"middle;"
        + module._CACHE_LOOKUP
        + b"quit;"
        + _QUIT_POLICY
        + b"sentry;"
        + _SENTRY_INTEGRATION_FILTER
        + b";after"
    )


def _executable_policy_payload(module: ModuleType, *, report: bytes) -> bytes:
    return (
        b"const calls = [];\n"
        b"let prevented = false;\n"
        b"delete process.env.VITE_DEV_SERVER_URL;\n"
        b"const r = {sendToRenderer(...args) {calls.push(args);}};\n"
        b"function configureUpdater() {" + module._APP_UPDATER_ENABLE + b";}\n"
        b"function rendererManifest() {" + module._MANIFEST_LOOKUP + b"}\n"
        b"function rendererCache() {" + module._CACHE_LOOKUP + b"return e;}\n"
        b"const handlers = {};\n"
        b"const event = {preventDefault() {prevented = true; "
        b"calls.push(['preventDefault']);}};\n"
        b"const ae = {onQuitForUpdate(callback) {handlers.update = callback;}, "
        b"installPendingUpdateOnQuit() {calls.push(['cleanup']);}, dispose() {}};\n"
        b"const Ie = {on(name, callback) {handlers[name] = callback;}};\n"
        b"const pO = ({cleanup, scheduleExit}) => () => {cleanup(); scheduleExit();};\n"
        b"const w = {kill() {}};\n"
        b"const i = {shutdown() {return Promise.resolve();}};\n"
        b"const WH = {Authenticated: 'authenticated'};\n"
        b"const re = {getState() {return {status: WH.Authenticated};}};\n"
        b"const Oe = {getAllWindows() {return [{isDestroyed() {return false;}}];}};\n"
        b"const O = {isPackaged: true, on(name, callback) {handlers[name] = callback;}, "
        b"exit() {calls.push(['exit']);}, "
        b"quit() {prevented = false; handlers['before-quit'](event); "
        b"if (!prevented) handlers['will-quit'](event);}};\n" + _QUIT_POLICY + b"\n"
        b"function filterIntegrations(e) {return ["
        + _SENTRY_INTEGRATION_FILTER
        + b"];}\n"
        b"O.quit();\n"
        b"setTimeout(() => console.log(JSON.stringify(" + report + b")), 0);\n"
    )


def _executable_quit_payload(module: ModuleType) -> bytes:
    return _executable_policy_payload(module, report=b"{calls, me, he}")


def _executable_crashpad_payload(module: ModuleType) -> bytes:
    return _executable_policy_payload(
        module,
        report=(
            b"filterIntegrations(["
            b"{name: 'Http'}, {name: 'NodeFetch'}, {name: 'Undici'}, "
            b"{name: 'SentryMinidump'}, {name: 'Other'}"
            b"]).map(({name}) => name)"
        ),
    )


def _file_integrity(payload: bytes) -> dict[str, object]:
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(payload).hexdigest(),
        "blockSize": _BLOCK_SIZE,
        "blocks": [
            hashlib.sha256(payload[offset : offset + _BLOCK_SIZE]).hexdigest()
            for offset in range(0, len(payload), _BLOCK_SIZE)
        ],
    }


def _valid_header(payload: bytes) -> dict[str, object]:
    return {
        "files": {
            "dist-electron": {
                "files": {
                    "main.js": {
                        "size": len(payload),
                        "offset": "0",
                        "integrity": _file_integrity(payload),
                    }
                }
            }
        }
    }


def _write_asar(
    path: Path,
    payload: bytes,
    *,
    header: dict[str, object] | list[object] | None = None,
) -> None:
    archive_header = _valid_header(payload) if header is None else header
    header_bytes = json.dumps(
        archive_header,
        separators=(",", ":"),
    ).encode()
    data_padding = b"\0\0\0"
    prefix = struct.pack(
        "<4I",
        4,
        len(header_bytes) + 8 + len(data_padding),
        len(header_bytes) + 4,
        len(header_bytes),
    )
    path.write_bytes(prefix + header_bytes + data_padding + payload)


def _write_plist(path: Path, value: object | None = None) -> None:
    payload = (
        {
            "CFBundleIdentifier": "ai.x.grok-desktop",
            "ElectronAsarIntegrity": {
                "Resources/app.asar": {
                    "algorithm": "SHA256",
                    "hash": "0" * 64,
                }
            },
        }
        if value is None
        else value
    )
    with path.open("wb") as handle:
        plistlib.dump(payload, handle)


def _fixture_paths(
    tmp_path: Path,
    module: ModuleType,
    *,
    name: str = "fixture",
    payload: bytes | None = None,
    header: dict[str, object] | list[object] | None = None,
    plist: object | None = None,
) -> tuple[Path, Path, bytes]:
    main_payload = _main_payload(module) if payload is None else payload
    asar_path = tmp_path / f"{name}.asar"
    plist_path = tmp_path / f"{name}.plist"
    _write_asar(asar_path, main_payload, header=header)
    _write_plist(plist_path, plist)
    return asar_path, plist_path, main_payload


def _archive_payload(module: ModuleType, asar_path: Path) -> bytes:
    return read_packed_file(asar_path, module.MAIN_PATH)


def test_grok_build_resolves_one_immutable_official_arm64_zip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stable electron-builder feed should pin xAI's versioned arm64 ZIP."""
    module = _load_updater_module()
    updater = module.GrokBuildUpdater()

    async def _fetch_asset_urls(
        _session: object,
        url: str,
        selectors: object,
        *,
        config: object,
    ) -> tuple[str, dict[str, str]]:
        assert url == updater.FEED_URL
        assert selectors is updater.SELECTORS
        assert config == updater.config
        return _VERSION, {"aarch64-darwin": _ARTIFACT_URL}

    monkeypatch.setattr(
        "lib.update.updaters.strategies.fetch_electron_builder_asset_urls",
        _fetch_asset_urls,
    )

    info = _run(updater.fetch_latest(object()))
    result = updater.build_result(info, {"aarch64-darwin": _HASH})

    assert updater.PLATFORMS == {"aarch64-darwin": "arm64"}
    assert info == VersionInfo(
        version=_VERSION,
        metadata=AssetURLsMetadata({"aarch64-darwin": _ARTIFACT_URL}),
    )
    assert result.urls == {"aarch64-darwin": _ARTIFACT_URL}
    assert result.hashes.to_json() == {"aarch64-darwin": _HASH}


@pytest.mark.parametrize(
    "url",
    [
        _ARTIFACT_URL.replace("https://", "http://"),
        _ARTIFACT_URL.replace("storage.googleapis.com", "example.test"),
        _ARTIFACT_URL.replace("/stable/", "/alpha/"),
        _ARTIFACT_URL.replace(_VERSION, "0.1.1540-gdeadbee"),
        _ARTIFACT_URL.replace("arm64-mac", "mac"),
        _ARTIFACT_URL.replace(".zip", ".dmg"),
        f"{_ARTIFACT_URL}?mutable=1",
        f"{_ARTIFACT_URL}#mutable",
    ],
)
def test_grok_build_selector_rejects_noncanonical_artifacts(url: str) -> None:
    """Only the exact immutable stable xAI arm64 artifact is acceptable."""
    updater = _load_updater_module().GrokBuildUpdater()
    selector = updater.SELECTORS["aarch64-darwin"]

    assert selector(_VERSION, _ARTIFACT_URL)
    assert not selector(_VERSION, url)
    assert (
        updater.get_download_url("aarch64-darwin", VersionInfo(_VERSION))
        == _ARTIFACT_URL
    )


def test_grok_build_package_owns_required_runtime_entitlements() -> None:
    """Ad-hoc Electron processes must opt out of Team-ID library validation."""
    with (_PACKAGE_DIR / "Entitlements.plist").open("rb") as handle:
        entitlements = plistlib.load(handle)

    assert entitlements == {
        "com.apple.security.cs.allow-jit": True,
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
        "com.apple.security.cs.disable-library-validation": True,
        "com.apple.security.device.audio-input": True,
        "com.apple.security.files.user-selected.read-write": True,
        "com.apple.security.network.client": True,
    }


def test_grok_build_package_owns_update_policy_and_resigns_bundle() -> None:
    """The package must patch updates and re-sign every Electron process."""
    source = (_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")
    package = expect_instance(parse_nix_expr(source), FunctionDefinition)
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(arguments.values, "builder").value,
        Identifier(name="mkZipApp"),
    )
    for name, value in {
        "pname": "grok-build",
        "appName": "Grok Build",
        "bundleName": "Grok Build.app",
        "executableName": "Grok",
        "sourceAppPath": "Grok.app",
    }.items():
        assert_nix_ast_equal(
            expect_binding(arguments.values, name).value,
            StringPrimitive(value=value),
        )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "info").value,
        Identifier(name="selfSource"),
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
    shell = parse_shell(indented_string_body(post_install.rebuild()))
    patch_commands = command_texts(shell, "__NIX_INTERP__")
    sign_commands = command_texts(shell, "/usr/bin/codesign")

    assert patch_commands == [
        "PYTHONPATH=__NIX_INTERP__ __NIX_INTERP__ __NIX_INTERP__ \\\n"
        '      "$app_bundle/Contents/Resources/app.asar" \\\n'
        '      "$app_bundle/Contents/Info.plist"'
    ]
    assert sign_commands == [
        "/usr/bin/codesign \\\n"
        "      --force \\\n"
        "      --deep \\\n"
        "      --sign - \\\n"
        "      --preserve-metadata=identifier,flags,runtime \\\n"
        "      --entitlements __NIX_INTERP__ \\\n"
        '      "$app_bundle"'
    ]


def test_grok_build_sources_pin_the_official_stable_zip() -> None:
    """Checked-in metadata must identify one immutable official arm64 ZIP."""
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )
    version = assert_release_version(source.version)
    _hashes, urls = assert_platform_source_entry(
        source,
        platforms={"aarch64-darwin"},
    )
    url = urls["aarch64-darwin"]
    assert_https_url(url, host="storage.googleapis.com")
    assert_url_contains_version(url, version)
    assert url.endswith("-arm64-mac.zip")


def test_update_patch_updates_payload_file_hashes_and_plist(
    tmp_path: Path,
) -> None:
    """Both updater families and every associated integrity layer are updated."""
    module = _load_patch_module()
    asar_path, plist_path, original = _fixture_paths(tmp_path, module)
    original_size = asar_path.stat().st_size

    digest = module.patch_bundle(asar_path, plist_path)
    patched = _archive_payload(module, asar_path)

    assert asar_path.stat().st_size == original_size
    assert len(patched) == len(original)
    assert module._APP_UPDATER_ENABLE not in patched
    assert module._MANIFEST_LOOKUP not in patched
    assert module._CACHE_LOOKUP not in patched
    assert _QUIT_INTERCEPT not in patched
    assert module._SENTRY_INTEGRATION_FILTER not in patched
    assert module._APP_UPDATER_DISABLED in patched
    assert module._MANIFEST_DISABLED in patched
    assert module._CACHE_DISABLED in patched
    assert b"me" + module._QUIT_DIRECT_SUFFIX in patched
    assert module._SENTRY_WITHOUT_MINIDUMPS in patched
    assert digest == asar_header_hash(asar_path)
    assert check_info_plist_hash(plist_path, asar_path) == digest

    with pytest.raises(module.PatchError, match="app updater.*found 0"):
        module.patch_bundle(asar_path, plist_path)


def test_update_patch_allows_normal_quit_to_reach_process_cleanup(
    tmp_path: Path,
) -> None:
    """A normal Quit must not wait forever for a renderer confirmation."""
    module = _load_patch_module()
    payload = _executable_quit_payload(module)
    asar_path, plist_path, _original = _fixture_paths(
        tmp_path,
        module,
        payload=payload,
    )
    node = shutil.which("node")
    assert node is not None

    intercepted = subprocess.run(  # noqa: S603
        [node, "-e", payload.decode()],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(intercepted.stdout) == {
        "calls": [["preventDefault"], ["app:quit-request"]],
        "me": False,
        "he": True,
    }

    module.patch_bundle(asar_path, plist_path)
    patched = _archive_payload(module, asar_path)
    direct = subprocess.run(  # noqa: S603
        [node, "-e", patched.decode()],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(direct.stdout) == {
        "calls": [["preventDefault"], ["cleanup"], ["exit"]],
        "me": True,
        "he": False,
    }


def test_update_patch_tracks_quit_guard_across_minifier_renames() -> None:
    """The quit policy should follow structure instead of minified identifiers."""
    module = _load_patch_module()
    payload = _main_payload(module).replace(
        _QUIT_POLICY,
        _RENAMED_QUIT_POLICY,
    )

    patched = module._patch_main_payload(payload)

    assert b"allowQuit = !0; /* Nix: allow will-quit cleanup. */" in patched
    assert b"app:quit-request" not in patched
    assert len(patched) == len(payload)


def test_update_patch_does_not_cross_between_before_quit_handlers() -> None:
    """A nearby listener must not donate the allow flag for the real request."""
    module = _load_patch_module()
    unrelated = b'O.on("before-quit", (e) => { if (!wrong) { noop(); }});'
    actual = _quit_policy(allow=b"right")

    patched = module._replace_quit_confirmation(unrelated + actual)

    assert b"right = !0; /* Nix: allow will-quit cleanup. */" in patched
    assert b"wrong = !0; /* Nix: allow will-quit cleanup. */" not in patched


@pytest.mark.parametrize(
    "first_close",
    [
        b"e.preventDefault();}});",
        b"e.preventDefault();}/* split */});",
        b"e.preventDefault();}})\n",
    ],
)
def test_update_patch_does_not_borrow_request_from_later_listener(
    first_close: bytes,
) -> None:
    """The request statement must remain inside the captured before-quit callback."""
    module = _load_patch_module()
    malformed = _QUIT_POLICY.replace(
        _QUIT_INTERCEPT + b"}});let ve",
        (
            first_close
            + b'X.on("other", (e) => {if (ready){'
            + _QUIT_INTERCEPT
            + b"}});let ve"
        ),
    )
    assert malformed != _QUIT_POLICY

    with pytest.raises(
        module.PatchError,
        match="structural quit confirmation policy.*found 0",
    ):
        module._replace_quit_confirmation(malformed)


def test_update_patch_rejects_ambiguous_structural_quit_handlers() -> None:
    """Two matching quit state machines are unsafe to rewrite."""
    module = _load_patch_module()

    with pytest.raises(
        module.PatchError,
        match="structural quit confirmation.*found 2",
    ):
        module._replace_quit_confirmation(_QUIT_POLICY + _QUIT_POLICY)


@pytest.mark.parametrize(
    "extra_request",
    [
        b';r.sendToRenderer("app:quit-request");',
        b";r.sendToRenderer('app:quit-request');",
        b";log(`app:quit-request`);",
    ],
)
def test_update_patch_rejects_a_second_quit_request_channel(
    extra_request: bytes,
) -> None:
    """No unreviewed renderer-confirmation send site may survive the patch."""
    module = _load_patch_module()
    payload = _QUIT_POLICY + extra_request

    with pytest.raises(module.PatchError, match="app:quit-request channel.*found 2"):
        module._replace_quit_confirmation(payload)


def test_update_patch_requires_shared_confirm_and_pending_state() -> None:
    """Confirm, cancel, and request paths must agree on one pending flag."""
    module = _load_patch_module()
    malformed = _QUIT_POLICY.replace(
        b"he = !1, me = !0, O.quit()",
        b"other = !1, me = !0, O.quit()",
    )

    with pytest.raises(
        module.PatchError,
        match="structural quit confirmation policy.*found 0",
    ):
        module._replace_quit_confirmation(malformed)


@pytest.mark.parametrize(
    "malformed",
    [
        _QUIT_POLICY.replace(b"O.exit()", b"noop()"),
        _QUIT_POLICY.replace(b"ve();})", b"noop();})"),
        _QUIT_POLICY.replace(
            b"ae.installPendingUpdateOnQuit(),ae.dispose(),w.kill();",
            b"noop();",
        ),
        _QUIT_POLICY.replace(
            b"Promise.race([i.shutdown(), delay]).finally(() => O.exit());",
            b'log("O.exit()");',
        ),
        _QUIT_POLICY.replace(
            b"Promise.race([i.shutdown(), delay]).finally(() => O.exit());",
            b"/* O.exit(); */",
        ),
    ],
)
def test_update_patch_requires_linked_cleanup_and_exit(malformed: bytes) -> None:
    """Will-quit must invoke the coordinator whose schedule exits this app."""
    module = _load_patch_module()
    assert malformed != _QUIT_POLICY

    with pytest.raises(
        module.PatchError,
        match="structural quit confirmation policy.*found 0",
    ):
        module._replace_quit_confirmation(malformed)


def test_update_patch_rejects_quit_guard_too_long_for_in_place_rewrite() -> None:
    """ASAR source patches must never change the packed JavaScript length."""
    module = _load_patch_module()
    allow_guard = b"a" * 128
    policy = _quit_policy(allow=allow_guard)

    with pytest.raises(module.PatchError, match="replacement is longer"):
        module._replace_quit_confirmation(policy)


def test_update_patch_disables_only_the_native_crashpad_integration(
    tmp_path: Path,
) -> None:
    """Normal telemetry may remain, but Quit must not orphan Crashpad."""
    module = _load_patch_module()
    payload = _executable_crashpad_payload(module)
    asar_path, plist_path, _original = _fixture_paths(
        tmp_path,
        module,
        payload=payload,
    )
    node = shutil.which("node")
    assert node is not None

    original = subprocess.run(  # noqa: S603
        [node, "-e", payload.decode()],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(original.stdout) == ["SentryMinidump", "Other"]

    module.patch_bundle(asar_path, plist_path)
    patched = _archive_payload(module, asar_path)
    crashpad_free = subprocess.run(  # noqa: S603
        [node, "-e", patched.decode()],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(crashpad_free.stdout) == ["Other"]


@pytest.mark.parametrize(
    ("anchor_name", "message"),
    [
        ("_APP_UPDATER_ENABLE", "app updater.*found 0"),
        ("_MANIFEST_LOOKUP", "renderer manifest.*found 0"),
        ("_CACHE_LOOKUP", "renderer cache.*found 0"),
        ("quit-handler", "structural quit confirmation.*found 0"),
        (
            "_SENTRY_INTEGRATION_FILTER",
            "Sentry minidump integration.*found 0",
        ),
    ],
)
def test_update_patch_requires_every_vendor_anchor(
    tmp_path: Path,
    anchor_name: str,
    message: str,
) -> None:
    """A drift in either updater family must fail before archive mutation."""
    module = _load_patch_module()
    payload = _main_payload(module)
    anchor = (
        _QUIT_POLICY if anchor_name == "quit-handler" else getattr(module, anchor_name)
    )
    drifted = payload.replace(anchor, b"x" * len(anchor))
    asar_path = tmp_path / "drifted.asar"
    _write_asar(asar_path, drifted)
    before = asar_path.read_bytes()

    with pytest.raises(module.PatchError, match=message):
        module._patch_asar(asar_path)

    assert asar_path.read_bytes() == before


@pytest.mark.parametrize(
    ("old", "new", "payload", "message"),
    [
        (b"a", b"longer", b"a", "replacement is longer"),
        (b"anchor", b"short", b"no match", "found 0"),
        (b"anchor", b"short", b"anchor anchor", "found 2"),
    ],
)
def test_update_patch_rejects_unstable_replacement_contracts(
    old: bytes,
    new: bytes,
    payload: bytes,
    message: str,
) -> None:
    """A changed or ambiguous vendor anchor must fail before archive mutation."""
    module = _load_patch_module()

    with pytest.raises(module.PatchError, match=message):
        module._replace_padded_once(payload, old, new, name="test")


def test_update_patch_requires_the_packed_main_entry(tmp_path: Path) -> None:
    """Missing ASAR paths must fail without mutating the archive."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(
        tmp_path,
        module,
        header={"files": {}},
    )
    original = asar_path.read_bytes()

    with pytest.raises(module.PatchError, match="missing packed file"):
        module.patch_bundle(asar_path, plist_path)

    assert asar_path.read_bytes() == original


@pytest.mark.parametrize(
    ("plist", "message"),
    [
        (["not", "a", "dictionary"], "Expected a plist dictionary"),
        (
            {"ElectronAsarIntegrity": "invalid"},
            "Expected ElectronAsarIntegrity dictionary",
        ),
    ],
)
def test_update_patch_rejects_invalid_plist_integrity_shape(
    tmp_path: Path,
    plist: object,
    message: str,
) -> None:
    """The top-level Electron integrity contract must remain dictionary-shaped."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(
        tmp_path,
        module,
        plist=plist,
    )
    original_asar = asar_path.read_bytes()
    original_plist = plist_path.read_bytes()

    with pytest.raises(module.PatchError, match=message):
        module.patch_bundle(asar_path, plist_path)

    assert asar_path.read_bytes() == original_asar
    assert plist_path.read_bytes() == original_plist


def test_update_patch_restores_asar_when_plist_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed second publish must roll the first bundle-file publish back."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(tmp_path, module)
    original_asar = asar_path.read_bytes()
    original_plist = plist_path.read_bytes()
    replace = Path.replace

    def fail_plist_publish(source: Path, target: str | Path) -> Path:
        if Path(target) == plist_path:
            raise OSError("simulated plist publish failure")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_plist_publish)

    with pytest.raises(OSError, match="simulated plist publish failure"):
        module.patch_bundle(asar_path, plist_path)

    assert asar_path.read_bytes() == original_asar
    assert plist_path.read_bytes() == original_plist


def test_update_patch_cli_reports_success_and_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The package hook should expose a concise success or fail-closed diagnostic."""
    module = _load_patch_module()
    asar_path, plist_path, _payload = _fixture_paths(tmp_path, module)

    assert module.main([str(asar_path), str(plist_path)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith(
        "disabled Grok app and renderer updates; ASAR header SHA256 "
    )

    missing = tmp_path / "missing.asar"
    assert module.main([str(missing), str(plist_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No such file or directory" in captured.err
