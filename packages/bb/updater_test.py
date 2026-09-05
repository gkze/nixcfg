"""Tests for the source-built bb desktop updater."""

import json
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent
from types import ModuleType
from typing import cast

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import UpdateEventKind
from lib.update.net import github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_package_path_attr_expr,
)
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/bb"
_COMMIT = "45145e51af36b4bd1346a9d2e73d7612d250ba4f"
_SRC_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_NPM_DEPS_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
_ELECTRON_VERSION = "41.7.0"

_PINNED_MAIN_PROCESS_FIXTURE = dedent(
    """\
    import { randomUUID } from "node:crypto";
    import { accessSync, constants as fsConstants } from "node:fs";
    import { homedir } from "node:os";
    import { dirname, join, resolve } from "node:path";
    import {
      app,
      BrowserWindow,
      clipboard,
      ipcMain,
      nativeImage,
      nativeTheme,
      net,
      safeStorage,
      session,
      shell,
      type Event,
      type IpcMainInvokeEvent,
      type WebContents,
    } from "electron";
    import {
      createConnectCredentialCache,
      type ConnectCredentialCache,
    } from "./connect-credential-cache.js";
    import { enrollDesktopMachine } from "./connect-machine-enrollment.js";
    import {
      createConnectSessionRenewal,
      type ConnectSessionRenewal,
    } from "./connect-session-renewal.js";

    let serverTargetStore;
    let connectCredentialCache;
    let cachedConnectCredential;
    const userDataPath = "/tmp/bb-test";
    const SERVER_TARGET_FILE_NAME = "server-target.json";

    function createServerTargetStore(_options: object) {
      return { async load(): Promise<void> {} };
    }

    function createDesktopLogger() {
      return {};
    }

    async function startDesktop(): Promise<void> {
      serverTargetStore = createServerTargetStore({
        storagePath: join(userDataPath, SERVER_TARGET_FILE_NAME),
      });
      await serverTargetStore.load();
      connectCredentialCache = createConnectCredentialCache({
        encryption: safeStorage,
        userDataPath,
      });
      cachedConnectCredential = await connectCredentialCache.read();
      const logger = createDesktopLogger();
    }

    await startDesktop();
    console.log(JSON.stringify({
      cacheCallCount: globalThis.__bbCacheEncryptionSources.length,
      cacheEncryptionSource: globalThis.__bbCacheEncryptionSources[0],
    }));
    """
)

_PINNED_THEME_GENERATOR_FIXTURE = dedent(
    """\
    import { readFile } from "node:fs/promises";
    import path from "node:path";
    import { fileURLToPath } from "node:url";

    const scriptDir = path.dirname(fileURLToPath(import.meta.url));
    const packageRoot = path.join(scriptDir, "..");
    const repoRoot = path.join(packageRoot, "..", "..");
    const themeCssPath = path.join(
      repoRoot,
      "apps",
      "app",
      "src",
      "components",
      "ui",
      "theme.css",
    );
    // Read through apps/app's node_modules link so we vendor exactly the version
    // the app ships (its package.json exports block require.resolve, so plain
    // path reads are the only way in).
    const twAnimateDir = path.join(
      repoRoot,
      "apps",
      "app",
      "node_modules",
      "tw-animate-css",
    );
    const outPath = path.join(
      packageRoot,
      "src",
      "generated",
      "plugin-theme.generated.ts",
    );

    await readFile(path.join(twAnimateDir, "package.json"), "utf8");
    console.log(twAnimateDir);
    void themeCssPath;
    void outPath;
    """
)


def _load_module() -> ModuleType:
    return load_repo_module("packages/bb/updater.py", "bb_updater_test")


def _github_release_api(
    *,
    commit: object = _COMMIT,
    paths: list[str] | None = None,
):
    async def _fetch(
        _session: object,
        path: str,
        **_kwargs: object,
    ) -> object:
        if paths is not None:
            paths.append(path)
        if path == "repos/get-bb/bb/releases/latest":
            return {
                "tag_name": "desktop-v0.38.0",
                # GitHub commonly exposes the mutable branch here. The updater
                # must resolve the tag through the commits API instead.
                "target_commitish": "main",
            }
        assert path == "repos/get-bb/bb/commits/desktop-v0.38.0"
        return {"sha": commit}

    return _fetch


def _install_main_process_runtime(root: Path) -> None:
    (root / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    electron = root / "node_modules/electron"
    electron.mkdir(parents=True)
    (electron / "package.json").write_text(
        '{"name":"electron","type":"module","exports":"./index.js"}\n',
        encoding="utf-8",
    )
    (electron / "index.js").write_text(
        dedent(
            """\
            export const app = {};
            export const BrowserWindow = {};
            export const clipboard = {};
            export const ipcMain = {};
            export const nativeImage = {};
            export const nativeTheme = {};
            export const net = {};
            export const safeStorage = { source: "electron-safe-storage" };
            export const session = {};
            export const shell = {};
            """
        ),
        encoding="utf-8",
    )

    source_dir = root / "apps/desktop/src"
    source_dir.mkdir(parents=True)
    (source_dir / "connect-credential-cache.js").write_text(
        dedent(
            """\
            export function createConnectCredentialCache(options) {
              globalThis.__bbCacheEncryptionSources ??= [];
              globalThis.__bbCacheEncryptionSources.push(options.encryption.source);
              return { async read() { return null; } };
            }
            """
        ),
        encoding="utf-8",
    )
    (source_dir / "connect-machine-enrollment.js").write_text(
        "export function enrollDesktopMachine() {}\n",
        encoding="utf-8",
    )
    (source_dir / "connect-session-renewal.js").write_text(
        "export function createConnectSessionRenewal() {}\n",
        encoding="utf-8",
    )
    (source_dir / "nix-managed-connect-credential-encryption.js").write_text(
        "export const nixManagedConnectCredentialEncryption = "
        '{ source: "nix-managed-adapter" };\n',
        encoding="utf-8",
    )


def _run_main_process(source_path: Path) -> dict[str, object]:
    node = shutil.which("node")
    assert node is not None
    completed = subprocess.run(  # noqa: S603 -- fixed runtime fixture
        [
            node,
            "--experimental-strip-types",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return cast("dict[str, object]", json.loads(completed.stdout))


def test_bb_nix_managed_encryption_adapter_never_reaches_keychain() -> None:
    """The Nix app must make every safe-storage operation unreachable."""
    node = shutil.which("node")
    assert node is not None
    adapter_uri = (
        _PACKAGE_DIR / "nix-managed-connect-credential-encryption.ts"
    ).as_uri()
    script = f"""
const {{ nixManagedConnectCredentialEncryption: encryption }} =
  await import({json.dumps(adapter_uri)});

if (encryption.isEncryptionAvailable() !== false) {{
  throw new Error("Nix-managed credential persistence must stay disabled");
}}
for (const [name, value] of [
  ["decryptString", Buffer.from("preserved-cache-bytes")],
  ["encryptString", "credential-that-must-not-be-persisted"],
]) {{
  let rejected = false;
  try {{
    encryption[name](value);
  }} catch {{
    rejected = true;
  }}
  if (!rejected) {{
    throw new Error(`${{name}} unexpectedly reached a storage backend`);
  }}
}}
"""

    subprocess.run(  # noqa: S603 -- fixed script exercises a repository-owned module
        [
            node,
            "--experimental-strip-types",
            "--input-type=module",
            "--eval",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_bb_package_patches_the_main_process_away_from_safe_storage() -> None:
    """The built app must pass the tested adapter into its credential cache."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    platform_assertion = expect_instance(package.output, Assertion)
    derivation = expect_instance(platform_assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(arguments.values, "patches").value,
        """
        [
          ./patches/nix-managed-updates.patch
          ./patches/nix-managed-connect-credential-cache.patch
          ./patches/nix-managed-runtime-closure.patch
          ./patches/pnpm-10-hoisted-runtime-manifest.patch
        ]
        """,
    )


def test_bb_normalizes_pnpm_patch_hashes_before_dependency_fetch_and_build() -> None:
    """Use one pnpm-10-compatible source for both dependency and app builds."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )

    assert_nix_ast_equal(
        expect_binding(package.output.scope, "src").value,
        """
        runCommand "${pname}-${version}-pnpm10-source" {
          nativeBuildInputs = [ python3 ];
        } ''
          cp -R ${upstreamSrc} "$out"
          chmod -R u+w "$out"
          ${lib.getExe python3} ${./normalize_pnpm_patch_hashes.py} \\
            --source "$out"
        ''
        """,
    )
    pnpm_deps = expect_binding(package.output.scope, "pnpmDeps").value
    args = expect_instance(
        expect_binding(pnpm_deps.scope, "args").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        args,
        """{
          inherit pname pnpm src version;
          fetcherVersion = 3;
          hash = outputs.lib.sourceHash pname "npmDepsHash";
        }""",
    )


def test_bb_theme_generator_supports_pnpm_10_root_hoisting(tmp_path: Path) -> None:
    """Resolve the app's style dependency from pnpm 10's workspace root."""
    git = shutil.which("git")
    assert git is not None
    source = (
        tmp_path / "packages" / "plugin-build" / "scripts" / "generate-plugin-theme.mjs"
    )
    source.parent.mkdir(parents=True)
    source.write_text(_PINNED_THEME_GENERATOR_FIXTURE, encoding="utf-8")
    root_dependency = tmp_path / "node_modules" / "tw-animate-css"
    root_dependency.mkdir(parents=True)
    (root_dependency / "package.json").write_text("{}\n", encoding="utf-8")

    subprocess.run(  # noqa: S603 -- applies a fixed repository patch fixture
        [
            git,
            "apply",
            "--include=packages/plugin-build/scripts/generate-plugin-theme.mjs",
            str(_PACKAGE_DIR / "patches/pnpm-10-hoisted-runtime-manifest.patch"),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(  # noqa: S603 -- executes a fixed test fixture
        ["node", str(source)],  # noqa: S607 -- resolves Node from the test PATH
        check=True,
        capture_output=True,
        text=True,
    )

    assert Path(completed.stdout.strip()) == root_dependency


def test_bb_main_process_uses_nix_managed_encryption_adapter(
    tmp_path: Path,
) -> None:
    """The real source patch must replace safeStorage at the cache boundary."""
    git = shutil.which("git")
    assert git is not None
    _install_main_process_runtime(tmp_path)
    main_source = tmp_path / "apps/desktop/src/main.ts"
    main_source.write_text(_PINNED_MAIN_PROCESS_FIXTURE, encoding="utf-8")

    assert _run_main_process(main_source) == {
        "cacheCallCount": 1,
        "cacheEncryptionSource": "electron-safe-storage",
    }
    subprocess.run(  # noqa: S603 -- applies the repository-owned source patch
        [
            git,
            "apply",
            str(_PACKAGE_DIR / "patches/nix-managed-connect-credential-cache.patch"),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert _run_main_process(main_source) == {
        "cacheCallCount": 1,
        "cacheEncryptionSource": "nix-managed-adapter",
    }


def test_bb_update_pins_release_source_and_pnpm_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater should use the immutable release commit, not its binary asset."""
    module = _load_module()
    updater = module.BbUpdater()
    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )
    current = SourceEntry(
        version="0.38.0",
        commit=_COMMIT,
        hashes=[
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("npmDepsHash", updater.config.fake_hash),
        ],
    )

    release_api_paths: list[str] = []
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _github_release_api(paths=release_api_paths),
    )
    manifest_urls: list[str] = []

    async def _fetch_json(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> object:
        manifest_urls.append(url)
        if url.endswith("apps/desktop/package.json"):
            return {
                "version": "0.38.0",
                "devDependencies": {"electron": _ELECTRON_VERSION},
            }
        return {"version": "0.38.0"}

    monkeypatch.setattr(module, "fetch_json", _fetch_json, raising=False)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, _SRC_HASH),
            (None, _NPM_DEPS_HASH),
        ),
    )

    events = run_async(collect_events(updater.update_stream(current, object())))

    assert release_api_paths == [
        "repos/get-bb/bb/releases/latest",
        "repos/get-bb/bb/commits/desktop-v0.38.0",
    ]
    assert manifest_urls == [
        github_raw_url("get-bb", "bb", _COMMIT, "apps/desktop/package.json"),
        github_raw_url("get-bb", "bb", _COMMIT, "packages/bb-app/package.json"),
    ]
    assert len(calls) == 2
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "get-bb",
            "bb",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        _build_package_path_attr_expr(
            "bb",
            ".pnpmDeps",
            system="aarch64-darwin",
            source_overrides={
                "bb": SourceEntry(
                    version="0.38.0",
                    commit=_COMMIT,
                    electron_version=_ELECTRON_VERSION,
                    hashes=[
                        HashEntry.create("srcHash", _SRC_HASH),
                        HashEntry.create("npmDepsHash", updater.config.fake_hash),
                    ],
                )
            },
        ),
    )

    result_events = [event for event in events if event.kind is UpdateEventKind.RESULT]
    assert len(result_events) == 1
    assert result_events[0].payload == SourceEntry(
        version="0.38.0",
        commit=_COMMIT,
        electron_version=_ELECTRON_VERSION,
        hashes=[
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
        ],
    )


@pytest.mark.parametrize(
    ("desktop", "bb_app", "error_type", "message"),
    [
        (
            [],
            {"version": "0.38.0"},
            TypeError,
            "desktop manifest is not a JSON object",
        ),
        (
            {"devDependencies": {"electron": "41.7.0"}},
            {"version": "0.38.0"},
            TypeError,
            "desktop manifest version is missing",
        ),
        (
            {"version": "0.38.1", "devDependencies": {"electron": "41.7.0"}},
            {"version": "0.38.0"},
            RuntimeError,
            "desktop manifest version",
        ),
        (
            {"version": "0.38.0", "devDependencies": {"electron": "41.7.0"}},
            [],
            TypeError,
            "bb-app manifest is not a JSON object",
        ),
        (
            {"version": "0.38.0", "devDependencies": {"electron": "41.7.0"}},
            {},
            TypeError,
            "bb-app manifest version is missing",
        ),
        (
            {"version": "0.38.0", "devDependencies": {"electron": "41.7.0"}},
            {"version": "0.38.1"},
            RuntimeError,
            "bb-app manifest version",
        ),
        (
            {"version": "0.38.0", "devDependencies": []},
            {"version": "0.38.0"},
            TypeError,
            "Electron version",
        ),
        (
            {"version": "0.38.0", "devDependencies": {}},
            {"version": "0.38.0"},
            TypeError,
            "Electron version",
        ),
    ],
)
def test_bb_rejects_incoherent_release_manifests(
    monkeypatch: pytest.MonkeyPatch,
    desktop: object,
    bb_app: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """Release metadata must agree with both immutable upstream manifests."""
    module = _load_module()
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _github_release_api(),
    )

    async def _fetch_json(
        _session: object,
        url: str,
        **_kwargs: object,
    ) -> object:
        return desktop if url.endswith("apps/desktop/package.json") else bb_app

    monkeypatch.setattr(module, "fetch_json", _fetch_json, raising=False)

    with pytest.raises(error_type, match=message):
        run_async(module.BbUpdater().fetch_latest(object()))


@pytest.mark.parametrize("commit", [None, "main", "ABCDEF"])
def test_bb_rejects_tag_without_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
    commit: object,
) -> None:
    """Mutable branches and malformed commit identifiers must never be pinned."""
    module = _load_module()
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _github_release_api(commit=commit),
    )

    with pytest.raises(RuntimeError, match="no immutable source commit"):
        run_async(module.BbUpdater().fetch_latest(object()))


def test_bb_requires_commit_when_building_source_result() -> None:
    """Manually constructed metadata may not bypass the commit invariant."""
    module = _load_module()

    with pytest.raises(RuntimeError, match="missing an immutable source commit"):
        module.BbUpdater().build_result(
            VersionInfo(version="0.38.0"),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


def test_bb_requires_electron_version_when_building_source_result() -> None:
    """Source results must preserve every evaluator-visible metadata field."""
    module = _load_module()

    with pytest.raises(TypeError, match="electronVersion"):
        module.BbUpdater().build_result(
            VersionInfo(version="0.38.0", metadata={"commit": _COMMIT}),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


def test_bb_rejects_non_object_desktop_manifest_for_electron_discovery() -> None:
    """Electron discovery must reject a malformed desktop manifest."""
    module = _load_module()

    with pytest.raises(TypeError, match="desktop manifest is not a JSON object"):
        module.BbUpdater._electron_version([])
