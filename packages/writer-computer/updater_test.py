"""Focused contracts for the source-built Writer macOS package."""

import asyncio
from types import ModuleType

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._source_metadata import (
    assert_immutable_commit,
    assert_release_version,
    assert_structured_source_hashes,
)
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.constants import FAKE_HASH
from lib.update.derivation_validation import DerivationValidation
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_package_path_attr_expr,
)
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

_PACKAGE_DIR = REPO_ROOT / "packages/writer-computer"
_VERSION = "0.5.0"
_COMMIT = "e49c16f73e49b9f753ba3f349136d10ed03a286c"
_SRC_HASH = "sha256-IMIaMgRwv165fB5sq3NMltFnDcfcCWXcYm8HVLbXnrk="
_NPM_DEPS_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
_CARGO_HASH = "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD="


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/writer-computer/updater.py",
        "writer_computer_updater_test",
    )


def test_writer_resolves_release_to_an_immutable_public_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release discovery must resolve the tag instead of persisting a branch."""
    module = _load_module()
    updater = module.WriterComputerUpdater()
    api_paths: list[str] = []

    async def github_payload(
        _session: object,
        path: str,
        *,
        config: object,
    ) -> dict[str, str]:
        assert config == updater.config
        api_paths.append(path)
        if path.endswith("/releases/latest"):
            return {"tag_name": f"v{_VERSION}"}
        return {"sha": _COMMIT}

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        github_payload,
    )

    assert run_async(updater.fetch_latest(object())) == VersionInfo(
        version=_VERSION,
        metadata={"commit": _COMMIT, "tag": f"v{_VERSION}"},
    )
    assert api_paths == [
        "repos/joelbqz/writer-computer/releases/latest",
        f"repos/joelbqz/writer-computer/commits/v{_VERSION}",
    ]


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ([], TypeError),
        ({"sha": "main"}, RuntimeError),
        ({"sha": "A" * 40}, RuntimeError),
    ],
)
def test_writer_rejects_release_without_an_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
) -> None:
    """Malformed and mutable release targets must never enter sources.json."""
    module = _load_module()
    responses = iter(({"tag_name": f"v{_VERSION}"}, payload))
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=next(responses)),
    )

    with pytest.raises(error_type, match="has no immutable source commit"):
        run_async(module.WriterComputerUpdater().fetch_latest(object()))


def test_writer_hashes_source_pnpm_and_cargo_in_dependency_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each fixed-output probe must consume all previously resolved hashes."""
    module = _load_module()
    updater = module.WriterComputerUpdater()
    info = VersionInfo(_VERSION, {"commit": _COMMIT, "tag": f"v{_VERSION}"})
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, _SRC_HASH),
            (None, _NPM_DEPS_HASH),
            (None, _CARGO_HASH),
        ),
    )

    run_async(collect_events(updater.fetch_hashes(info, object())))

    assert updater.supported_platforms == ("aarch64-darwin",)
    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "joelbqz",
            "writer-computer",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    fake_hash = updater.config.fake_hash
    pnpm_override = SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("npmDepsHash", fake_hash),
            HashEntry.create("cargoHash", fake_hash),
        ]),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        _build_package_path_attr_expr(
            "writer-computer",
            ".pnpmDeps",
            system="aarch64-darwin",
            source_overrides={"writer-computer": pnpm_override},
        ),
    )
    cargo_override = SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
            HashEntry.create("cargoHash", fake_hash),
        ]),
    )
    assert_nix_ast_equal(
        str(calls[2]["expr"]),
        _build_package_path_attr_expr(
            "writer-computer",
            ".cargoDeps",
            system="aarch64-darwin",
            source_overrides={"writer-computer": cargo_override},
        ),
    )

    result = updater.build_result(
        info,
        [
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ],
    )
    assert result == SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("npmDepsHash", _NPM_DEPS_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ]),
    )


def test_writer_requires_commit_when_building_source_result() -> None:
    """Manually constructed metadata may not bypass the commit invariant."""
    updater = _load_module().WriterComputerUpdater()

    with pytest.raises(RuntimeError, match="missing an immutable source commit"):
        updater.build_result(
            VersionInfo(version=_VERSION),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


def test_writer_always_recomputes_source_closures_for_current_release() -> None:
    """Matching release metadata must still flow through all source hash probes."""
    updater = _load_module().WriterComputerUpdater()
    info = VersionInfo(_VERSION, {"commit": _COMMIT, "tag": f"v{_VERSION}"})

    assert run_async(updater._is_latest(None, info)) is False


def test_writer_package_is_a_nix_owned_source_built_arm64_app() -> None:
    """The package must expose the patched app and an app-free CLI view."""
    package_file = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    platform_assertion = expect_instance(package_file.output, Assertion)
    package_binding = expect_binding(platform_assertion.scope, "package")
    package_call = expect_instance(package_binding.value, FunctionCall)
    arguments = expect_instance(package_call.argument, AttributeSet)

    assert_nix_ast_equal(
        platform_assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    assert_nix_ast_equal(platform_assertion.body, Identifier(name="package"))
    assert_nix_ast_equal(
        package_call.name,
        Identifier(name="rustPlatform.buildRustPackage"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "cargoPatches").value,
        "[ ./nix-managed.patch ]",
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "cargoRoot").value,
        '"apps/desktop/src-tauri"',
    )
    build_environment = expect_instance(
        expect_binding(arguments.values, "env").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(build_environment.values, "CI").value,
        '"true"',
    )

    passthru = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    expect_instance(expect_binding(passthru.values, "cliPackage").value, FunctionCall)
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleRelPath").value,
        '"Applications/${appBundleName}"',
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "postFixup").value,
        "''\n      /usr/bin/xattr -cr \"$out/Applications/${appBundleName}\"\n"
        "      /usr/bin/codesign --force --deep --sign - \\\n"
        "        \"$out/Applications/${appBundleName}\"\n    ''",
    )


def test_writer_source_pin_contains_promoted_authoritative_hashes() -> None:
    """The promoted source graph must contain every reproducible source closure."""
    source = SourceEntry.model_validate_json(
        (_PACKAGE_DIR / "sources.json").read_text(encoding="utf-8")
    )

    assert_release_version(source.version)
    assert_immutable_commit(source.commit)
    assert_structured_source_hashes(
        source,
        hash_types={"srcHash", "npmDepsHash", "cargoHash"},
    )

    entries = source.hashes.entries
    assert entries is not None
    assert all(entry.hash != FAKE_HASH for entry in entries)
