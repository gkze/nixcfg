"""Source discovery and update-ownership tests for GooeyPi."""

import asyncio
import json
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    nix_apply,
    nix_attrset_call,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.events import UpdateEventKind
from lib.update.net import github_raw_url
from lib.update.nix import _build_fetch_from_github_call
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT, package_file_names_in
from lib.update.updaters import VersionInfo

_VERSION = "1.1.15"
_COMMIT = "9a562bb34c0accc7b1bd1396309d0c003b23f3e2"
_ELECTRON_VERSION = "43.4.0"
_NODE_ENGINE = ">=24.15.0"
_NPM_ENGINE = ">=12.0.2"
_PACKAGE_MANAGER = "npm@12.0.2"
_NPM_VERSION = "12.0.2"
_NPM_CLI_URL = f"https://registry.npmjs.org/npm/-/npm-{_NPM_VERSION}.tgz"
_SRC_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_NPM_DEPS_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
_NPM_CLI_HASH = "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD="
_PACKAGE_DIR = REPO_ROOT / "packages/gooeypi"


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/gooeypi/updater.py",
        "gooeypi_updater_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/gooeypi/patch_nix_managed.py",
        "gooeypi_nix_policy_patch_test",
    )


def _package_manifest(
    *,
    version: object = _VERSION,
    app_id: object = "app.gooeypi.desktop",
    electron: object = f"^{_ELECTRON_VERSION}",
    node_engine: object = _NODE_ENGINE,
    npm_engine: object = _NPM_ENGINE,
    package_manager: object = _PACKAGE_MANAGER,
) -> dict[str, object]:
    return {
        "version": version,
        "build": {"appId": app_id},
        "devDependencies": {"electron": electron},
        "engines": {"node": node_engine, "npm": npm_engine},
        "packageManager": package_manager,
    }


def _lock_manifest(
    *,
    version: object = _VERSION,
    electron: object = _ELECTRON_VERSION,
) -> dict[str, object]:
    return {
        "version": version,
        "packages": {"node_modules/electron": {"version": electron}},
    }


def test_gooeypi_update_pins_source_and_hashes_build_closure_without_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater should discover all FODs directly from immutable source."""
    module = _load_updater_module()
    updater = module.GooeyPiUpdater()
    current = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )
    api_paths: list[str] = []

    async def _github_api(
        _session: object,
        path: str,
        **_kwargs: object,
    ) -> object:
        api_paths.append(path)
        if path.endswith("/releases/latest"):
            return {"tag_name": f"v{_VERSION}"}
        return {"sha": _COMMIT}

    manifest_urls: list[str] = []

    async def _fetch_json(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> object:
        manifest_urls.append(url)
        if url.endswith("package-lock.json"):
            return _lock_manifest()
        return _package_manifest()

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _github_api,
    )
    monkeypatch.setattr(module, "fetch_json", _fetch_json)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, _SRC_HASH),
            (None, _NPM_DEPS_HASH),
            (None, _NPM_CLI_HASH),
        ),
    )

    events = run_async(collect_events(updater.update_stream(current, object())))

    assert api_paths == [
        "repos/am-will/gooey-pi/releases/latest",
        f"repos/am-will/gooey-pi/commits/v{_VERSION}",
    ]
    assert manifest_urls == [
        github_raw_url("am-will", "gooey-pi", _COMMIT, "package.json"),
        github_raw_url("am-will", "gooey-pi", _COMMIT, "package-lock.json"),
    ]
    assert len(calls) == 3
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "am-will",
            "gooey-pi",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        nix_attrset_call(
            identifier_attr_path("pkgs", "fetchNpmDeps"),
            name=f"gooeypi-{_VERSION}-npm-deps",
            src=nix_attrset_call(
                identifier_attr_path("pkgs", "fetchFromGitHub"),
                owner="am-will",
                repo="gooey-pi",
                hash=_SRC_HASH,
                rev=_COMMIT,
                fetchSubmodules=False,
            ),
            hash=identifier_attr_path("pkgs", "lib", "fakeHash"),
        ),
    )
    assert_nix_ast_equal(
        str(calls[2]["expr"]),
        f"""
        pkgs.fetchurl {{
          url = "{_NPM_CLI_URL}";
          hash = pkgs.lib.fakeHash;
        }}
        """,
    )

    result_events = [event for event in events if event.kind is UpdateEventKind.RESULT]
    assert len(result_events) == 1
    assert result_events[0].payload == SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        electron_version=_ELECTRON_VERSION,
        pins={"npmVersion": _NPM_VERSION},
        hashes=[
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
            HashEntry.create("sha256", _NPM_CLI_HASH, url=_NPM_CLI_URL),
        ],
    )


def test_gooeypi_metadata_has_complete_source_closures() -> None:
    """The exposed derivation must have immutable source and npm closures."""
    source = SourceEntry.model_validate(
        json.loads((_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8"))
    )

    assert source.version == _VERSION
    assert source.commit == _COMMIT
    assert source.electron_version == _ELECTRON_VERSION
    assert source.pins == {"npmVersion": _NPM_VERSION}
    assert source.hashes.entries is not None
    assert {entry.hash_type for entry in source.hashes.entries} == {
        "npmDepsHash",
        "sha256",
        "srcHash",
    }
    assert "gooeypi" in package_file_names_in(REPO_ROOT, "default.nix")


def test_gooeypi_package_is_a_source_built_managed_mac_app() -> None:
    """The package should build, harden, and expose the immutable source app."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    npm_source_assertion = expect_instance(package.output, Assertion)
    platform_assertion = expect_instance(npm_source_assertion.body, Assertion)
    derivation = expect_instance(platform_assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    scope = npm_source_assertion.scope

    assert_nix_ast_equal(
        expect_binding(scope, "src").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="am-will",
            repo="gooey-pi",
            rev=identifier_attr_path("selfSource", "commit"),
            hash=nix_apply(
                identifier_attr_path("outputs", "lib", "sourceHash"),
                Identifier(name="pname"),
                StringPrimitive(value="srcHash"),
            ),
        ),
    )
    assert_nix_ast_equal(
        expect_binding(scope, "npmDeps").value,
        """
        fetchNpmDeps {
          name = "${pname}-${version}-npm-deps";
          inherit src;
          hash = outputs.lib.sourceHash pname "npmDepsHash";
        }
        """,
    )
    assert_nix_ast_equal(
        expect_binding(scope, "electronBuild").value,
        "nixcfgElectron.sourceBuildFor electronVersion",
    )
    npm_cli = expect_instance(
        expect_binding(scope, "npmCli").value,
        FunctionCall,
    )
    assert_nix_ast_equal(npm_cli.name, "stdenvNoCC.mkDerivation")
    npm_cli_arguments = expect_instance(npm_cli.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(npm_cli_arguments.values, "version").value,
        Identifier(name="npmCliVersion"),
    )

    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))
    commands = command_texts(build_shell)
    assert "npm run build" in commands
    assert any(
        command.startswith("npm exec -- electron-rebuild")
        and "--only=node-pty,zeromq" in command
        for command in commands
    )
    assert any(
        command.startswith('PATH="$codesignPath:$PATH" npm exec -- electron-builder')
        and "--mac" in command
        and "--arm64" in command
        and "-c.npmRebuild=false" in command
        for command in commands
    )
    assert command_texts(build_shell, "ln") == [
        'ln -s /usr/bin/codesign "$codesignPath/codesign"'
    ]

    native_build_inputs = expect_instance(
        expect_binding(arguments.values, "nativeBuildInputs").value,
        NixList,
    )
    assert (
        sum(
            isinstance(item, Identifier) and item.name == "cmake"
            for item in native_build_inputs.value
        )
        == 1
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "dontUseCmakeConfigure").value,
        "true",
    )

    install_check_phase = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_shell = parse_shell(
        indented_string_body(install_check_phase.rebuild())
    )
    install_check_commands = command_texts(install_check_shell)
    assert any(
        command.startswith(
            "FORCE_COLOR=0 node node_modules/@electron/fuses/dist/bin.js"
        )
        and 'read --app "$app"' in command
        for command in install_check_commands
    )
    assert [
        command
        for command in command_texts(install_check_shell, "grep")
        if "gooeypi-fuses" in command
    ] == [
        f"grep -Fxq '  {name} is {state}' \"$TMPDIR/gooeypi-fuses\""
        for name, state in [
            ("RunAsNode", "Disabled"),
            ("EnableCookieEncryption", "Enabled"),
            ("EnableNodeOptionsEnvironmentVariable", "Disabled"),
            ("EnableNodeCliInspectArguments", "Disabled"),
            ("EnableEmbeddedAsarIntegrityValidation", "Enabled"),
            ("OnlyLoadAppFromAsar", "Enabled"),
            ("LoadBrowserProcessSpecificV8Snapshot", "Disabled"),
            ("GrantFileProtocolExtraPrivileges", "Disabled"),
            ("WasmTrapHandlers", "Enabled"),
        ]
    ]

    passthru = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value,
        AttributeSet,
    )
    metadata = expect_instance(
        expect_binding(arguments.values, "meta").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleId").value,
        Identifier(name="appId"),
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleName").value,
        Identifier(name="appBundleName"),
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleRelPath").value,
        '"Applications/${appBundleName}"',
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "installMode").value,
        '"copy"',
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )


def test_gooeypi_makes_npm_cache_writable_for_pinned_npm() -> None:
    """Pinned npm 12 must not install from a read-only fixed-output cache."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    npm_source_assertion = expect_instance(package.output, Assertion)
    platform_assertion = expect_instance(npm_source_assertion.body, Assertion)
    derivation = expect_instance(platform_assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(arguments.values, "makeCacheWritable").value,
        "true",
    )


@pytest.mark.parametrize("electron_spec", [_ELECTRON_VERSION, f"^{_ELECTRON_VERSION}"])
def test_gooeypi_accepts_exact_locked_electron_contract(electron_spec: str) -> None:
    """Only an exact or exact-caret manifest may select the locked runtime."""
    module = _load_updater_module()

    assert module.GooeyPiUpdater._validate_release_manifests(
        version=_VERSION,
        package_manifest=_package_manifest(electron=electron_spec),
        lock_manifest=_lock_manifest(),
    ) == (
        _ELECTRON_VERSION,
        _NPM_VERSION,
    )


@pytest.mark.parametrize(
    ("package", "lock", "error_type", "message"),
    [
        (
            [],
            _lock_manifest(),
            TypeError,
            "package manifest is not a JSON object",
        ),
        (
            {"build": {}, "devDependencies": {}},
            _lock_manifest(),
            TypeError,
            "package manifest version is missing",
        ),
        (
            _package_manifest(version="1.1.9"),
            _lock_manifest(),
            RuntimeError,
            "package manifest version",
        ),
        (
            _package_manifest(),
            [],
            TypeError,
            "lock manifest is not a JSON object",
        ),
        (
            _package_manifest(),
            {"packages": {}},
            TypeError,
            "lock manifest version is missing",
        ),
        (
            _package_manifest(),
            _lock_manifest(version="1.1.9"),
            RuntimeError,
            "lock manifest version",
        ),
        (
            {"version": _VERSION, "devDependencies": {"electron": "43.4.0"}},
            _lock_manifest(),
            TypeError,
            "build configuration is missing",
        ),
        (
            _package_manifest(app_id="example.wrong"),
            _lock_manifest(),
            RuntimeError,
            "package appId",
        ),
        (
            {"version": _VERSION, "build": {"appId": "app.gooeypi.desktop"}},
            _lock_manifest(),
            TypeError,
            "Electron dependency is missing",
        ),
        (
            _package_manifest(electron=""),
            _lock_manifest(),
            TypeError,
            "Electron dependency is missing",
        ),
        (
            _package_manifest(),
            {"version": _VERSION},
            TypeError,
            "package lock has no package mapping",
        ),
        (
            _package_manifest(),
            {"version": _VERSION, "packages": {}},
            TypeError,
            "package lock has no exact Electron package",
        ),
        (
            _package_manifest(),
            {
                "version": _VERSION,
                "packages": {"node_modules/electron": {}},
            },
            TypeError,
            "package lock has no exact Electron version",
        ),
        (
            _package_manifest(electron="^43.3.0"),
            _lock_manifest(),
            RuntimeError,
            "does not resolve exactly",
        ),
        (
            {
                "version": _VERSION,
                "build": {"appId": "app.gooeypi.desktop"},
                "devDependencies": {"electron": f"^{_ELECTRON_VERSION}"},
            },
            _lock_manifest(),
            TypeError,
            "build toolchain is missing",
        ),
        (
            _package_manifest(node_engine=">=25.0.0"),
            _lock_manifest(),
            RuntimeError,
            "Node engine",
        ),
        (
            _package_manifest(npm_engine=">=11.0.0"),
            _lock_manifest(),
            RuntimeError,
            "npm engine",
        ),
        (
            _package_manifest(package_manager="pnpm@10.0.0"),
            _lock_manifest(),
            RuntimeError,
            "package manager",
        ),
    ],
)
def test_gooeypi_rejects_incoherent_release_manifests(
    package: object,
    lock: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """Release, app identity, and runtime metadata must form one exact tree."""
    module = _load_updater_module()

    with pytest.raises(error_type, match=message):
        module.GooeyPiUpdater._validate_release_manifests(
            version=_VERSION,
            package_manifest=package,
            lock_manifest=lock,
        )


@pytest.mark.parametrize(
    ("commit_payload", "error_type"),
    [
        ([], TypeError),
        ({}, RuntimeError),
        ({"sha": "main"}, RuntimeError),
        ({"sha": "A" * 40}, RuntimeError),
    ],
)
def test_gooeypi_rejects_release_without_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
    commit_payload: object,
    error_type: type[Exception],
) -> None:
    """Mutable or malformed release targets must never enter sources.json."""
    module = _load_updater_module()

    responses = iter(({"tag_name": f"v{_VERSION}"}, commit_payload))
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=next(responses)),
    )

    with pytest.raises(error_type, match="no immutable source commit"):
        run_async(module.GooeyPiUpdater().fetch_latest(object()))


@pytest.mark.parametrize("commit", [None, "main", "A" * 40])
def test_gooeypi_build_result_requires_immutable_commit(commit: str | None) -> None:
    """Hand-written metadata may not bypass immutable source ownership."""
    module = _load_updater_module()

    with pytest.raises(RuntimeError, match="missing an immutable source commit"):
        module.GooeyPiUpdater().build_result(
            VersionInfo(
                version=_VERSION,
                metadata={
                    "commit": commit,
                    "electronVersion": _ELECTRON_VERSION,
                    "npmVersion": _NPM_VERSION,
                },
            ),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


def test_gooeypi_build_result_requires_electron_version() -> None:
    """The package evaluator must always receive the exact locked runtime."""
    module = _load_updater_module()

    with pytest.raises(TypeError, match="electronVersion"):
        module.GooeyPiUpdater().build_result(
            VersionInfo(version=_VERSION, metadata={"commit": _COMMIT}),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


def test_gooeypi_build_result_requires_npm_version() -> None:
    """The package evaluator must always receive its pinned npm source."""
    module = _load_updater_module()

    with pytest.raises(TypeError, match="npmVersion"):
        module.GooeyPiUpdater().build_result(
            VersionInfo(
                version=_VERSION,
                metadata={
                    "commit": _COMMIT,
                    "electronVersion": _ELECTRON_VERSION,
                },
            ),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


@pytest.mark.parametrize(
    ("hashes", "error_type", "message"),
    [
        (
            {"aarch64-darwin": _SRC_HASH},
            TypeError,
            "structured source hash entries",
        ),
        (
            [HashEntry.create("srcHash", _SRC_HASH)],
            RuntimeError,
            "expected one npm CLI hash, found 0",
        ),
        (
            [
                HashEntry.create("srcHash", _SRC_HASH),
                HashEntry.create("sha256", _NPM_CLI_HASH),
                HashEntry.create("sha256", _NPM_CLI_HASH),
            ],
            RuntimeError,
            "expected one npm CLI hash, found 2",
        ),
    ],
)
def test_gooeypi_build_result_requires_exactly_one_structured_npm_cli_hash(
    hashes: dict[str, str] | list[HashEntry],
    error_type: type[Exception],
    message: str,
) -> None:
    """Results must identify exactly one pinned npm CLI artifact."""
    module = _load_updater_module()
    info = VersionInfo(
        version=_VERSION,
        metadata={
            "commit": _COMMIT,
            "electronVersion": _ELECTRON_VERSION,
            "npmVersion": _NPM_VERSION,
        },
    )

    with pytest.raises(error_type, match=message):
        module.GooeyPiUpdater().build_result(info, hashes)


def test_gooeypi_nix_policy_patch_disables_packaged_updates(tmp_path: Path) -> None:
    """The source build must never register electron-updater under Nix."""
    module = _load_patch_module()
    index_path = tmp_path / module._INDEX_PATH
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        f"before\n{module._UPDATER_ANCHOR}\nafter\n",
        encoding="utf-8",
    )

    assert module.main([str(tmp_path)]) == 0
    patched = index_path.read_text(encoding="utf-8")
    assert module._UPDATER_ANCHOR not in patched
    assert patched == f"before\n{module._NIX_MANAGED_UPDATER}\nafter\n"


@pytest.mark.parametrize("copies", [0, 2])
def test_gooeypi_nix_policy_patch_rejects_source_drift(
    tmp_path: Path,
    copies: int,
) -> None:
    """A missing or ambiguous updater anchor must fail instead of half-patching."""
    module = _load_patch_module()
    index_path = tmp_path / module._INDEX_PATH
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        "\n".join([module._UPDATER_ANCHOR] * copies),
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError,
        match=f"expected one GooeyPi updater anchor, found {copies}",
    ):
        module.patch_tree(tmp_path)
