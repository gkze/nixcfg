"""Focused contracts for the source-built Reflect Open macOS foundation."""

import asyncio
import io
import json
import tarfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace, TracebackType
from typing import Self

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._nix_source import nix_file_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_repo_package_attr_expr,
)
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/reflect-open"
_VERSION = "0.10.0"
_TAG = f"v{_VERSION}"
_COMMIT = "265eaea2c5b80131da362eccbd38694adf6635cf"
_SRC_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_NPM_DEPS_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
_CARGO_HASH = "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD="
_PNPM_VERSION = "11.18.0"
_PNPM_URL = f"https://registry.npmjs.org/pnpm/-/pnpm-{_PNPM_VERSION}.tgz"
_PNPM_HASH = "sha256-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE="
_REAL_SRC_HASH = "sha256-NlOjgmUGu0AKiUdHQga1+gTvizlKtcHpeaFE8OKC79A="
_REAL_NPM_DEPS_HASH = "sha256-GPn26R1gN43QJHqF+iKZJCx79BJOFn0tCea4xgUjNWs="
_REAL_CARGO_HASH = "sha256-Fsbe0Do9w0ijkWj5gc6eyWaZmVT8mS0U9Reo7Udg14A="
_REAL_PNPM_HASH = "sha256-KcNcqNKih5iP3uPg824H2bk3g/VntXm3/Vt5ikVj3YE="
_MINIMUM_MACOS_VERSION = "14.0"
_ARCHIVE_ROOT = f"reflect-open-{_COMMIT}"

_UPSTREAM_PATCH_SOURCES = {
    "package.json": """{
  "name": "reflect-open",
  "packageManager": "pnpm@11.18.0"
}
""",
    "apps/desktop/src-tauri/tauri.conf.json": """{
  "plugins": {
    "deep-link": {
      "desktop": {
        "schemes": ["reflect"]
      }
    },
    "updater": {
      "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDREQ0RBRkJBN0ZDODdDMzkKUldRNWZNaC91cS9OVFhtTHdrSWVOV3p2cEdhc3RZbmtXM0g0bnpxUnZXVjlQMjZRNXFlWUdPNWMK",
      "endpoints": [
        "https://github.com/team-reflect/reflect-open/releases/download/updater-beta/latest.json"
      ]
    }
  }
}
""",
    "apps/desktop/src-tauri/tauri.macos.conf.json": """{
  "bundle": {
    "macOS": {
      "entitlements": "Entitlements.plist",
      "files": {
        "embedded.provisionprofile": "Reflect.provisionprofile"
      }
    }
  }
}
""",
    "apps/desktop/src-tauri/src/lib.rs": """fn builder() {
    #[cfg(desktop)]
    let builder = builder
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(
            tauri_plugin_window_state::Builder::default()
        );
}
""",
    "apps/desktop/src-tauri/capabilities/desktop.json": """{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "desktop",
  "description": "Desktop-only capabilities: auto-update checks, the post-install relaunch, window-state restore, and deep-link events",
  "platforms": ["macOS", "windows", "linux"],
  "windows": ["main", "note-*"],
  "permissions": ["updater:default", "process:default", "window-state:default", "deep-link:default"]
}
""",
    "apps/desktop/src-tauri/Cargo.toml": """[target.'cfg(not(any(target_os = "android", target_os = "ios")))'.dependencies]
tauri-plugin-updater = "2.10.1"
tauri-plugin-process = "2.3.1"
tauri-plugin-window-state = "2.4.1"
trash = "5.2.6"
""",
    "apps/desktop/src/providers/update-provider.tsx": """import { useMainWindowEffect } from '@/hooks/use-main-window-effect'
import { isNativeShell } from '@/lib/platform'

export function UpdateProvider({ children, autoCheck }: UpdateProviderProps): ReactElement {
  const supported = isNativeShell()
  const resolvedAutoCheck = autoCheck ?? (supported && !import.meta.env.DEV)
  return <UpdateContext value={value}>{children}</UpdateContext>
}
""",
}


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/reflect-open/updater.py",
        "reflect_open_updater_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/reflect-open/patch_nix_managed.py",
        "reflect_open_nix_policy_patch_test",
    )


def _write_patch_fixture(root: Path) -> dict[Path, str]:
    originals: dict[Path, str] = {}
    for relative_path, source in _UPSTREAM_PATCH_SOURCES.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        originals[Path(relative_path)] = source
    return originals


def test_reflect_updater_build_validates_the_materialized_darwin_package() -> None:
    """Promotion must build the exact Reflect package after persisting hashes."""
    updater = _load_updater_module().ReflectOpenUpdater()

    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


def _tar_stream(*, duplicate_path: str | None = None) -> io.BytesIO:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        root = tarfile.TarInfo(f"{_ARCHIVE_ROOT}/")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)

        detached = b"ignored because it has no archive root\n"
        detached_info = tarfile.TarInfo("detached.txt")
        detached_info.size = len(detached)
        archive.addfile(detached_info, io.BytesIO(detached))

        unrelated = b"unrelated source\n"
        unrelated_info = tarfile.TarInfo(f"{_ARCHIVE_ROOT}/README.md")
        unrelated_info.size = len(unrelated)
        archive.addfile(unrelated_info, io.BytesIO(unrelated))

        entries = list(_UPSTREAM_PATCH_SOURCES.items())
        if duplicate_path is not None:
            entries.append((
                duplicate_path,
                _UPSTREAM_PATCH_SOURCES[duplicate_path],
            ))
        for relative_path, source in entries:
            encoded = source.encode()
            info = tarfile.TarInfo(f"{_ARCHIVE_ROOT}/{relative_path}")
            info.size = len(encoded)
            archive.addfile(info, io.BytesIO(encoded))
    stream.seek(0)
    return stream


class _UnreadableArchive:
    def __init__(self, relative_path: str) -> None:
        self.member = tarfile.TarInfo(f"{_ARCHIVE_ROOT}/{relative_path}")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def __iter__(self) -> Iterator[tarfile.TarInfo]:
        return iter((self.member,))

    def extractfile(self, _member: tarfile.TarInfo) -> None:
        return None


def test_reflect_resolves_the_release_to_its_immutable_public_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release discovery must persist the exact public source commit."""
    module = _load_updater_module()
    updater = module.ReflectOpenUpdater()
    api_paths: list[str] = []
    manifest_urls: list[str] = []

    async def github_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        if path.endswith("/releases/latest"):
            return {"tag_name": _TAG}
        return {"sha": _COMMIT}

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        github_payload,
    )

    async def manifest_payload(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        manifest_urls.append(url)
        return {"packageManager": f"pnpm@{_PNPM_VERSION}"}

    monkeypatch.setattr(module, "fetch_json", manifest_payload)

    assert run_async(updater.fetch_latest(object())) == VersionInfo(
        version=_VERSION,
        metadata={
            "commit": _COMMIT,
            "pnpmVersion": _PNPM_VERSION,
            "tag": _TAG,
        },
    )
    assert api_paths == [
        "repos/team-reflect/reflect-open/releases/latest",
        f"repos/team-reflect/reflect-open/commits/{_TAG}",
    ]
    assert manifest_urls == [
        f"https://raw.githubusercontent.com/team-reflect/reflect-open/{_COMMIT}/package.json"
    ]


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ([], TypeError),
        ({"sha": "main"}, RuntimeError),
        ({"sha": "A" * 40}, RuntimeError),
    ],
)
def test_reflect_rejects_release_without_an_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
) -> None:
    """Mutable or malformed release targets must never enter sources.json."""
    module = _load_updater_module()
    responses = iter(({"tag_name": _TAG}, payload))
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=next(responses)),
    )

    with pytest.raises(error_type, match="has no immutable source commit"):
        run_async(module.ReflectOpenUpdater().fetch_latest(object()))


@pytest.mark.parametrize(
    ("manifest", "error_type", "match"),
    [
        ([], TypeError, "manifest is not a JSON object"),
        ({}, TypeError, "packageManager is missing"),
        ({"packageManager": ""}, TypeError, "packageManager is missing"),
        (
            {"packageManager": "pnpm@^11"},
            RuntimeError,
            "requires an exact pnpm packageManager",
        ),
    ],
)
def test_reflect_rejects_release_without_an_exact_pnpm_toolchain(
    monkeypatch: pytest.MonkeyPatch,
    manifest: object,
    error_type: type[Exception],
    match: str,
) -> None:
    """Release discovery must reject absent, ranged, or foreign package managers."""
    module = _load_updater_module()

    async def github_payload(
        _session: object,
        path: str,
        **_kwargs: object,
    ) -> object:
        return (
            {"tag_name": _TAG}
            if path.endswith("/releases/latest")
            else {"sha": _COMMIT}
        )

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        github_payload,
    )
    monkeypatch.setattr(
        module,
        "fetch_json",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=manifest),
    )

    with pytest.raises(error_type, match=match):
        run_async(module.ReflectOpenUpdater().fetch_latest(object()))


def test_reflect_hashes_source_pnpm_and_cargo_without_exporting_the_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater should probe the internal foundation in dependency order."""
    module = _load_updater_module()
    updater = module.ReflectOpenUpdater()
    info = VersionInfo(
        _VERSION,
        {"commit": _COMMIT, "pnpmVersion": _PNPM_VERSION, "tag": _TAG},
    )
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, _SRC_HASH),
            (None, _PNPM_HASH),
            (None, _NPM_DEPS_HASH),
            (None, _CARGO_HASH),
        ),
    )

    run_async(collect_events(updater.fetch_hashes(info, object())))

    assert updater.supported_platforms == ("aarch64-darwin",)
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "team-reflect",
            "reflect-open",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    fake_hash = updater.config.fake_hash
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        f"""
        pkgs.fetchurl {{
          url = "{_PNPM_URL}";
          hash = pkgs.lib.fakeHash;
        }}
        """,
    )
    pnpm_override = SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("sha256", _PNPM_HASH, url=_PNPM_URL),
            HashEntry.create("npmDepsHash", fake_hash),
            HashEntry.create("cargoHash", fake_hash),
        ]),
    )
    assert_nix_ast_equal(
        str(calls[2]["expr"]),
        _build_repo_package_attr_expr(
            "packages/reflect-open/package.nix",
            ".pnpmDeps",
            system="aarch64-darwin",
            source_overrides={"reflect-open": pnpm_override},
        ),
    )
    cargo_override = SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("sha256", _PNPM_HASH, url=_PNPM_URL),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
            HashEntry.create("cargoHash", fake_hash),
        ]),
    )
    assert_nix_ast_equal(
        str(calls[3]["expr"]),
        _build_repo_package_attr_expr(
            "packages/reflect-open/package.nix",
            ".cargoDeps",
            system="aarch64-darwin",
            source_overrides={"reflect-open": cargo_override},
        ),
    )

    result = updater.build_result(
        info,
        [
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("sha256", _PNPM_HASH),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ],
    )
    assert result == SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("sha256", _PNPM_HASH, url=_PNPM_URL),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ]),
    )


@pytest.mark.parametrize("metadata", [{}, {"commit": "main"}])
def test_reflect_hashing_rejects_mutable_or_missing_source_commits(
    metadata: dict[str, str],
) -> None:
    """Hash probes must never accept an absent or mutable source revision."""
    module = _load_updater_module()
    info = VersionInfo(_VERSION, metadata)

    with pytest.raises(
        RuntimeError,
        match="missing an immutable source commit",
    ):
        run_async(
            collect_events(module.ReflectOpenUpdater().fetch_hashes(info, object()))
        )


def test_reflect_hashing_requires_pnpm_release_metadata() -> None:
    """A valid source commit is insufficient without its exact pnpm toolchain."""
    with pytest.raises(RuntimeError, match="missing a pnpm version"):
        run_async(
            collect_events(
                _load_updater_module()
                .ReflectOpenUpdater()
                .fetch_hashes(
                    VersionInfo(_VERSION, {"commit": _COMMIT}),
                    object(),
                )
            )
        )


@pytest.mark.parametrize(
    ("hashes", "error_type", "match"),
    [
        (
            {"aarch64-darwin": _PNPM_HASH},
            TypeError,
            "structured source hash entries",
        ),
        (
            [HashEntry.create("srcHash", _SRC_HASH)],
            RuntimeError,
            "one pnpm source hash, found 0",
        ),
        (
            [
                HashEntry.create("srcHash", _SRC_HASH),
                HashEntry.create("sha256", _PNPM_HASH),
                HashEntry.create(
                    "sha256", _SRC_HASH, url="https://example.invalid/pnpm.tgz"
                ),
            ],
            RuntimeError,
            "one pnpm source hash, found 2",
        ),
    ],
)
def test_reflect_result_requires_one_structured_pnpm_source(
    hashes: SourceHashes,
    error_type: type[Exception],
    match: str,
) -> None:
    """Promotion must reject ambiguous or non-structured pnpm source metadata."""
    info = VersionInfo(
        _VERSION,
        {"commit": _COMMIT, "pnpmVersion": _PNPM_VERSION, "tag": _TAG},
    )

    with pytest.raises(error_type, match=match):
        _load_updater_module().ReflectOpenUpdater().build_result(info, hashes)


def test_reflect_never_skips_dependency_closure_recomputation() -> None:
    """Even unchanged release metadata must refresh pnpm and Cargo closures."""
    module = _load_updater_module()
    info = VersionInfo(_VERSION, {"commit": _COMMIT, "tag": _TAG})
    current = SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ]),
    )

    assert run_async(module.ReflectOpenUpdater()._is_latest(current, info)) is False


def test_reflect_source_pin_contains_promoted_authoritative_hashes() -> None:
    """Metadata should pin the exact source, pnpm, and Cargo closures."""
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )

    assert source == SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("cargoHash", _REAL_CARGO_HASH),
            HashEntry.create("npmDepsHash", _REAL_NPM_DEPS_HASH),
            HashEntry.create("sha256", _REAL_PNPM_HASH, url=_PNPM_URL),
            HashEntry.create("srcHash", _REAL_SRC_HASH),
        ]),
    )


def test_reflect_nix_policy_suppresses_updates_and_uses_dev_entitlements() -> None:
    """The Nix source view must retain stable identity without mutable updates."""
    module = _load_patch_module()

    patched = module.patch_sources(_UPSTREAM_PATCH_SOURCES)

    tauri_config = json.loads(patched["apps/desktop/src-tauri/tauri.conf.json"])
    assert tauri_config["plugins"] == {
        "deep-link": {"desktop": {"schemes": ["reflect"]}}
    }
    macos_config = json.loads(patched["apps/desktop/src-tauri/tauri.macos.conf.json"])
    assert macos_config["bundle"]["macOS"] == {
        "entitlements": "Entitlements.dev.plist",
        "files": {"embedded.provisionprofile": None},
        "minimumSystemVersion": _MINIMUM_MACOS_VERSION,
    }
    rust_source = patched["apps/desktop/src-tauri/src/lib.rs"]
    assert "tauri_plugin_updater" not in rust_source
    assert "tauri_plugin_process" not in rust_source
    provider = patched["apps/desktop/src/providers/update-provider.tsx"]
    assert "const supported = false" in provider
    assert "isNativeShell" not in provider
    capabilities = json.loads(
        patched["apps/desktop/src-tauri/capabilities/desktop.json"]
    )
    assert capabilities["description"] == (
        "Desktop-only capabilities: window-state restore and deep-link events"
    )
    assert capabilities["permissions"] == [
        "window-state:default",
        "deep-link:default",
    ]
    cargo_manifest = patched["apps/desktop/src-tauri/Cargo.toml"]
    assert "tauri-plugin-updater" not in cargo_manifest
    assert "tauri-plugin-process" not in cargo_manifest
    assert 'tauri-plugin-window-state = "2.4.1"' in cargo_manifest
    assert patched != _UPSTREAM_PATCH_SOURCES


@pytest.mark.parametrize(
    "package_source",
    [
        '{"packageManager": "pnpm@11.19.0"}',
        "{}",
        "[]",
        "{not-json",
    ],
)
def test_reflect_nix_policy_rejects_package_manager_drift(
    package_source: str,
) -> None:
    """The pinned pnpm runtime must match upstream's packageManager contract."""
    module = _load_patch_module()
    drifted = dict(_UPSTREAM_PATCH_SOURCES)
    drifted["package.json"] = package_source

    with pytest.raises(
        RuntimeError,
        match=r"expected Reflect packageManager pnpm@11\.18\.0",
    ):
        module.patch_sources(drifted)


@pytest.mark.parametrize("copies", [0, 2])
def test_reflect_nix_policy_rejects_drift_before_patching_any_source(
    copies: int,
) -> None:
    """One missing anchor must reject the whole source transaction."""
    module = _load_patch_module()
    drifted = dict(_UPSTREAM_PATCH_SOURCES)
    provider_path = "apps/desktop/src/providers/update-provider.tsx"
    anchor = "const supported = isNativeShell()"
    drifted[provider_path] = drifted[provider_path].replace(
        anchor,
        anchor * copies,
    )
    before = dict(drifted)

    with pytest.raises(RuntimeError, match="expected one Reflect source anchor"):
        module.patch_sources(drifted)

    assert drifted == before


def test_reflect_nix_policy_requires_every_source_file() -> None:
    """A partial source view must fail before any replacement is attempted."""
    module = _load_patch_module()
    missing = dict(_UPSTREAM_PATCH_SOURCES)
    missing.pop("apps/desktop/src-tauri/src/lib.rs")

    with pytest.raises(RuntimeError, match="missing Reflect source file"):
        module.patch_sources(missing)


def test_reflect_nix_policy_patches_a_validated_unpacked_tree(
    tmp_path: Path,
) -> None:
    """The packaged CLI path should write the complete validated source view."""
    module = _load_patch_module()
    originals = _write_patch_fixture(tmp_path)
    expected = dict(originals)
    for patch in module._PATCHES:
        path = Path(patch.relative_path)
        expected[path] = expected[path].replace(patch.old, patch.new)

    assert module.main([str(tmp_path)]) == 0

    assert {
        path: (tmp_path / path).read_text(encoding="utf-8") for path in originals
    } == expected


def test_reflect_nix_policy_checks_a_source_archive_without_extracting() -> None:
    """The streaming archive mode should accept the exact expected source view."""
    _load_patch_module().check_tar_stream(_tar_stream())


def test_reflect_nix_policy_rejects_duplicate_archive_sources() -> None:
    """Ambiguous archive members must fail closed before policy validation."""
    module = _load_patch_module()
    duplicate_path = "apps/desktop/src-tauri/tauri.conf.json"

    with pytest.raises(RuntimeError, match="duplicate Reflect source file"):
        module.check_tar_stream(_tar_stream(duplicate_path=duplicate_path))


def test_reflect_nix_policy_rejects_unreadable_archive_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regular archive member with no readable payload must fail closed."""
    module = _load_patch_module()
    relative_path = "apps/desktop/src-tauri/tauri.conf.json"
    monkeypatch.setattr(
        module.tarfile,
        "open",
        lambda **_kwargs: _UnreadableArchive(relative_path),
    )

    with pytest.raises(RuntimeError, match="could not read Reflect source file"):
        module.check_tar_stream(io.BytesIO())


def test_reflect_nix_policy_main_routes_streaming_archive_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dry-check CLI should consume stdin without requiring a source root."""
    module = _load_patch_module()
    stream = io.BytesIO(b"archive")
    calls: list[io.BytesIO] = []
    monkeypatch.setattr(module, "check_tar_stream", calls.append)
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(buffer=stream))

    assert module.main(["--check-tar-stdin"]) == 0
    assert calls == [stream]


@pytest.mark.parametrize("argv", [[], ["source", "--check-tar-stdin"]])
def test_reflect_nix_policy_main_rejects_ambiguous_inputs(argv: list[str]) -> None:
    """The CLI must require exactly one patch or archive-check mode."""
    with pytest.raises(SystemExit, match="2"):
        _load_patch_module().main(argv)


def test_reflect_package_pins_upstream_pnpm_with_the_non_hanging_helper() -> None:
    """Pnpm hashing must use the upstream version and Node-24 helper pairing."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    package_call = expect_instance(
        expect_instance(package.output, Assertion).body,
        FunctionCall,
    )
    assert Identifier(name="fetchPnpmDeps") in package.argument_set
    assert Identifier(name="fetchurl") in package.argument_set
    assert Identifier(name="nodejs_24") in package.argument_set
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "pnpmSource").value,
        'outputs.lib.sourceHashEntry pname "sha256"',
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "pnpmVersionMatch").value,
        'builtins.match ".*/pnpm-([^/]+)\\\\.tgz" pnpmSource.url',
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "pnpmVersion").value,
        """
        if pnpmVersionMatch == null then
          throw "Reflect updater produced an invalid pnpm source URL: ${pnpmSource.url}"
        else
          builtins.head pnpmVersionMatch
        """,
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "nodejs").value,
        "nodejs_22",
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "pnpm").value,
        """(pnpm_11.override { nodejs-slim = nodejs_24; }).overrideAttrs (_: {
          version = pnpmVersion;
          src = fetchurl {
            inherit (pnpmSource) hash url;
          };
        })""",
    )
    pnpm_deps = expect_instance(
        expect_binding(package_call.scope, "pnpmDeps").value,
        FunctionCall,
    )
    assert pnpm_deps.name == Identifier(name="fetchPnpmDeps")
    assert pnpm_deps.argument == Identifier(name="args")
    pnpm_args = expect_instance(
        expect_binding(pnpm_deps.scope, "args").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(pnpm_args.values, "fetcherVersion").value,
        "4",
    )


def test_reflect_internal_package_preserves_the_managed_arm64_app_contract() -> None:
    """The source package must encode identity, ORT, signing, and app shape."""
    assert_nix_ast_equal(
        nix_file_expr("packages/reflect-open/default.nix"),
        "import ./package.nix",
    )
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    platform_assertion = expect_instance(package.output, Assertion)
    package_call = expect_instance(platform_assertion.body, FunctionCall)

    assert Identifier(name="perl") in package.argument_set

    assert_nix_ast_equal(
        platform_assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "appName").value,
        '"Reflect"',
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "appBundleName").value,
        '"${appName}.app"',
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "appExecutable").value,
        '"reflect-open"',
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "appId").value,
        '"app.reflect.desktop"',
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "minimumMacosVersion").value,
        f'"{_MINIMUM_MACOS_VERSION}"',
    )
    assert_nix_ast_equal(
        expect_binding(package_call.scope, "onnxruntimeShared").value,
        """(onnxruntime.override {
          coremlSupport = false;
          pythonSupport = false;
        }).overrideAttrs {
          doCheck = false;
        }""",
    )
    assert_nix_ast_equal(
        package_call.name,
        Identifier(name="rustPlatform.buildRustPackage"),
    )
    arguments = expect_instance(package_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(arguments.values, "buildAndTestSubdir").value,
        '"apps/desktop"',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "cargoRoot").value,
        '"."',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "tauriBuildFlags").value,
        '[ "--no-sign" ]',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "nativeBuildInputs").value,
        "[ cargo-tauri.hook nodejs perl pkg-config pnpm pnpmConfigHook python3 ]",
    )
    environment = expect_instance(
        expect_binding(arguments.values, "env").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(environment.values, "ORT_LIB_LOCATION").value,
        '"${onnxruntimeShared}/lib"',
    )
    assert_nix_ast_equal(
        expect_binding(environment.values, "ORT_PREFER_DYNAMIC_LINK").value,
        '"1"',
    )
    assert_nix_ast_equal(
        expect_binding(environment.values, "ORT_SKIP_DOWNLOAD").value,
        '"1"',
    )
    assert_nix_ast_equal(
        expect_binding(environment.values, "MACOSX_DEPLOYMENT_TARGET").value,
        Identifier(name="minimumMacosVersion"),
    )
    post_fixup = expect_instance(
        expect_binding(arguments.values, "postFixup").value,
        IndentedString,
    )
    assert command_texts(
        parse_shell(indented_string_body(post_fixup.rebuild())),
        "/usr/bin/codesign",
    ) == [
        "/usr/bin/codesign --force --sign - \\\n"
        '      "$appBundle/Contents/MacOS/reflect"',
        "/usr/bin/codesign --force --sign - \\\n"
        '      "$appBundle/Contents/MacOS/reflect-capture-host"',
        "/usr/bin/codesign --force --sign - \\\n"
        '      --entitlements "__NIX_INTERP__/apps/desktop/src-tauri/'
        'Entitlements.dev.plist" \\\n'
        '      "$appBundle"',
    ]
    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_commands = parse_shell(indented_string_body(install_check.rebuild()))
    assert (
        "/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "
        '"$infoPlist"'
        in command_texts(
            install_check_commands,
            "/usr/libexec/PlistBuddy",
        )
    )
    assert command_texts(install_check_commands, "/usr/bin/otool") == [
        '/usr/bin/otool -l "$machO"',
        "/usr/bin/otool -l \\\n"
        '              "__NIX_INTERP__/lib/libonnxruntime.1.dylib"',
        '/usr/bin/otool -L "$executable"',
    ]
    assert (
        "/usr/bin/codesign -d --entitlements - --xml \\\n"
        '              "$sidecar"'
        in command_texts(install_check_commands, "/usr/bin/codesign")
    )
    assert 'test ! -s "$sidecarEntitlements"' in command_texts(
        install_check_commands,
        "test",
    )
    passthru = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value,
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
    metadata = expect_instance(
        expect_binding(arguments.values, "meta").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "license").value,
        "lib.licenses.mit",
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "mainProgram").value,
        Identifier(name="appExecutable"),
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
