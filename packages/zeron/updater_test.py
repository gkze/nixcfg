"""Focused contracts for the source-built Zeron macOS package."""

import asyncio
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
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
from lib.update.derivation_validation import DerivationValidation
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_package_path_attr_expr,
)
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    class _Patch(Protocol):
        old: str
        new: str


_PACKAGE_DIR = REPO_ROOT / "packages/zeron"
_VERSION = "0.2.3"
_COMMIT = "9ab250ceb6317d080a8429435cb15a9eaef5663e"
_SRC_HASH = "sha256-rQKxufOuPROiGubjoYMqMMU4zIh7FraXcDmgMTtwes0="
_CARGO_HASH = "sha256-xB6tLTY5os8oTtbw9G1gbAkHnP+4O9IYk5Q4qz3iC6o="


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "packages/zeron/updater.py",
        "zeron_updater_dedicated_test",
    )


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/zeron/patch_nix_managed.py",
        "zeron_nix_policy_patch_test",
    )


def _write_patch_fixture(root: Path, patches: Iterable[_Patch]) -> Path:
    update_path = root / "crates/update/src/lib.rs"
    update_path.parent.mkdir(parents=True)
    update_path.write_text(
        "\n/* fixture boundary */\n".join(patch.old for patch in patches),
        encoding="utf-8",
    )
    return update_path


def test_zeron_resolves_the_release_tag_to_an_immutable_public_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release discovery must pin a commit rather than trust a mutable tag."""
    module = _load_updater_module()
    updater = module.ZeronUpdater()
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
        "repos/zeronsh/comet/releases/latest",
        f"repos/zeronsh/comet/commits/v{_VERSION}",
    ]


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ([], TypeError),
        ({"sha": "main"}, RuntimeError),
        ({"sha": "A" * 40}, RuntimeError),
    ],
)
def test_zeron_rejects_release_without_an_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
) -> None:
    """Malformed or mutable release targets must never enter sources.json."""
    module = _load_updater_module()
    responses = iter(({"tag_name": f"v{_VERSION}"}, payload))
    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=next(responses)),
    )

    with pytest.raises(error_type, match="has no immutable source commit"):
        run_async(module.ZeronUpdater().fetch_latest(object()))


def test_zeron_hashes_the_exact_source_and_cargo_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dependency probe must evaluate the same commit persisted for the app."""
    module = _load_updater_module()
    updater = module.ZeronUpdater()
    info = VersionInfo(_VERSION, {"commit": _COMMIT, "tag": f"v{_VERSION}"})
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, _SRC_HASH),
            (None, _CARGO_HASH),
        ),
    )

    run_async(collect_events(updater.fetch_hashes(info, object())))

    assert updater.supported_platforms == ("aarch64-darwin",)
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "zeronsh",
            "comet",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    source_override = SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("cargoHash", updater.config.fake_hash),
        ]),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        _build_package_path_attr_expr(
            "zeron",
            ".cargoDeps",
            system="aarch64-darwin",
            source_overrides={"zeron": source_override},
        ),
    )

    result = updater.build_result(
        info,
        [
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ],
    )
    assert result == SourceEntry(
        version=_VERSION,
        commit=_COMMIT,
        hashes=HashCollection.from_value([
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("cargoHash", _CARGO_HASH),
        ]),
    )


def test_zeron_requires_commit_when_building_source_result() -> None:
    """Hand-written updater metadata may not bypass immutable source ownership."""
    with pytest.raises(RuntimeError, match="missing an immutable source commit"):
        _load_updater_module().ZeronUpdater().build_result(
            VersionInfo(_VERSION),
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


def test_zeron_validates_the_materialized_bootstrap_source() -> None:
    """Final validation must see a newly created, not-yet-promoted sidecar."""
    module = _load_updater_module()

    assert module.ZeronUpdater.derivation_validations == (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            mode="build",
        ),
    )


def test_zeron_nix_policy_patch_disables_every_update_path(tmp_path: Path) -> None:
    """One fail-closed patch must cover checks, downloads, installs, and relaunch."""
    module = _load_patch_module()
    update_path = _write_patch_fixture(tmp_path, module._PATCHES)
    original = update_path.read_text(encoding="utf-8")
    expected = original
    for patch in module._PATCHES:
        expected = expected.replace(patch.old, patch.new)

    assert module.main([str(tmp_path)]) == 0
    assert update_path.read_text(encoding="utf-8") == expected
    assert module._PATCH_SENTINEL in expected

    with pytest.raises(RuntimeError, match="already applied"):
        module.patch_tree(tmp_path)


@pytest.mark.parametrize("copies", [0, 2])
def test_zeron_nix_policy_patch_rejects_source_drift_atomically(
    tmp_path: Path,
    copies: int,
) -> None:
    """No source file may be partially patched when an upstream anchor drifts."""
    module = _load_patch_module()
    target = module._PATCHES[-1]
    patches = [*module._PATCHES[:-1], *([target] * copies)]
    update_path = _write_patch_fixture(tmp_path, patches)
    original = update_path.read_text(encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match=f"expected one Zeron updater anchor, found {copies}",
    ):
        module.patch_tree(tmp_path)

    assert update_path.read_text(encoding="utf-8") == original


def test_zeron_package_is_a_source_built_nix_owned_arm64_app() -> None:
    """The package must pin source, compile policy, bundle, and ad-hoc sign last."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "default.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    assertion = expect_instance(package.output, Assertion)
    derivation = expect_instance(assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)

    formal_names = {
        argument.name
        for argument in package.argument_set
        if isinstance(argument, Identifier)
    }
    assert "outputs" in formal_names
    assert "selfSource" not in formal_names
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "source").value,
        "outputs.lib.sourceEntry pname",
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "version").value,
        "source.version",
    )

    assert_nix_ast_equal(
        assertion.expression,
        'stdenv.hostPlatform.system == "aarch64-darwin"',
    )
    assert_nix_ast_equal(
        derivation.name,
        Identifier(name="rustPlatform.buildRustPackage"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "src").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="zeronsh",
            repo="comet",
            rev=identifier_attr_path("source", "commit"),
            hash=nix_apply(
                identifier_attr_path("outputs", "lib", "sourceHash"),
                Identifier(name="pname"),
                StringPrimitive(value="srcHash"),
            ),
        ),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "cargoHash").value,
        'outputs.lib.sourceHash pname "cargoHash"',
    )
    assert "cargoPatches" not in binding_map(arguments.values)
    assert_nix_ast_equal(
        expect_binding(arguments.values, "cargoBuildFlags").value,
        '[ "-p" "zeron" ]',
    )
    environment = expect_instance(
        expect_binding(arguments.values, "env").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(environment.values, "ZERON_NIX_MANAGED").value,
        StringPrimitive(value="1"),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "doCheck").value,
        Primitive(value=False),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "strictDeps").value,
        Primitive(value=True),
    )
    assert_nix_ast_equal(
        expect_binding(arguments.values, "doInstallCheck").value,
        Primitive(value=True),
    )

    post_patch = expect_instance(
        expect_binding(arguments.values, "postPatch").value,
        IndentedString,
    )
    assert command_texts(
        parse_shell(indented_string_body(post_patch.rebuild())),
        "__NIX_INTERP__",
    ) == ['__NIX_INTERP__ __NIX_INTERP__ "$PWD"']

    post_fixup = expect_instance(
        expect_binding(arguments.values, "postFixup").value,
        IndentedString,
    )
    assert command_texts(
        parse_shell(indented_string_body(post_fixup.rebuild())),
        "/usr/bin/codesign",
    ) == [
        '/usr/bin/codesign --force --sign - "$app"',
        '/usr/bin/codesign --verify --deep --strict --verbose=2 "$app"',
    ]

    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_commands = parse_shell(
        indented_string_body(install_check.rebuild()),
    )
    assert command_texts(install_check_commands, "/usr/bin/codesign") == [
        '/usr/bin/codesign --verify --deep --strict --verbose=2 "$app"',
    ]
    assert command_texts(install_check_commands, "/usr/bin/file") == [
        '/usr/bin/file "$executable"',
    ]

    passthru_set = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    passthru = expect_instance(
        expect_binding(passthru_set.values, "macApp").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "bundleId").value,
        StringPrimitive(value="sh.zeron.app"),
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "bundleRelPath").value,
        StringPrimitive(value="Applications/Zeron.app"),
    )

    metadata = expect_instance(
        expect_binding(arguments.values, "meta").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(metadata.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
