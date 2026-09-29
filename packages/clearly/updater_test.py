"""Tests for the source-built Clearly updater."""

from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import (
    collect_events,
    install_fixed_hash_stream,
    load_repo_module,
    run_async,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import UpdateEventKind
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_package_path_attr_expr,
)
from lib.update.updaters import UpdateContext, VersionInfo

_COMMIT = "f9ed673ff753698eb55786fe056b367726464543"
_CMARK_COMMIT = "1111111111111111111111111111111111111111"
_CMARK_OLD_COMMIT = "2222222222222222222222222222222222222222"
_KEYBOARD_COMMIT = "3333333333333333333333333333333333333333"
_SRC_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_VENDOR_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
_PROJECT_YAML = b"""
packages:
  cmark-gfm:
    url: https://github.com/brokenhandsio/cmark-gfm.git
    from: "2.1.0"
  Sparkle:
    url: https://github.com/sparkle-project/Sparkle
    from: "2.6.0"
  KeyboardShortcuts:
    url: https://github.com/sindresorhus/KeyboardShortcuts
    from: "2.1.0"
"""
_DEPENDENCY_URLS = {
    "cmark-gfm": (
        f"https://github.com/brokenhandsio/cmark-gfm/archive/{_CMARK_COMMIT}.tar.gz"
    ),
    "KeyboardShortcuts": (
        "https://github.com/sindresorhus/KeyboardShortcuts/archive/"
        f"{_KEYBOARD_COMMIT}.tar.gz"
    ),
}


def _load_module() -> ModuleType:
    return load_repo_module("packages/clearly/updater.py", "clearly_updater_test")


def _load_patch_module() -> ModuleType:
    return load_repo_module(
        "packages/clearly/patch_hosted_sdk.py",
        "clearly_hosted_sdk_patch_test",
    )


def _write_toolbar(root: Path, source: str) -> Path:
    path = root / "Clearly" / "BottomToolbar.swift"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return path


def _toolbar_with_liquid_glass(module: ModuleType) -> str:
    body, glass_body, helpers, mode_pill = module._PATCHES
    return (
        "struct BottomToolbar: View {\n"
        f"{body.old}"
        f"{glass_body.old}"
        "    private var legacyBody: some View { EmptyView() }\n"
        f"{helpers.old}"
        "}\n"
        "private struct ModePill: View {\n"
        f"{mode_pill.old}"
        "}\n"
    )


async def _release_payload(*_args, **_kwargs):
    return {"tag_name": "v3.2.0", "target_commitish": "main"}


async def _commit_payload(*_args, **_kwargs):
    return {"sha": _COMMIT}


async def _project_yaml(*_args, **_kwargs):
    return _PROJECT_YAML


async def _dependency_tags(_session, path, **_kwargs):
    if path == "repos/brokenhandsio/cmark-gfm/tags":
        return [
            "ignore non-mapping entries",
            {"name": 2, "commit": {"sha": _CMARK_COMMIT}},
            {"name": "2.9.0", "commit": "not-a-mapping"},
            {"name": "2.8.0", "commit": {"sha": "not-a-commit"}},
            {"name": "not-semver", "commit": {"sha": _CMARK_COMMIT}},
            {"name": "2.3.0-beta.1", "commit": {"sha": _CMARK_COMMIT}},
            {"name": "2.0.9", "commit": {"sha": _CMARK_COMMIT}},
            {"name": "3.0.0", "commit": {"sha": _CMARK_COMMIT}},
            {"name": "2.1.0", "commit": {"sha": _CMARK_OLD_COMMIT}},
            {"name": "v2.2.0", "commit": {"sha": _CMARK_COMMIT}},
            {"name": "2.1.5", "commit": {"sha": _CMARK_OLD_COMMIT}},
        ]
    assert path == "repos/sindresorhus/KeyboardShortcuts/tags"
    return [
        {"name": "3.0.0", "commit": {"sha": _KEYBOARD_COMMIT}},
        {"name": "2.4.0", "commit": {"sha": _KEYBOARD_COMMIT}},
    ]


def _install_release_network(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType
) -> None:
    async def github_api(*args, **kwargs):
        path = args[1]
        if path.endswith("/releases/latest"):
            return await _release_payload(*args, **kwargs)
        return await _commit_payload(*args, **kwargs)

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        github_api,
    )
    monkeypatch.setattr(module, "fetch_url", _project_yaml)
    monkeypatch.setattr(module, "fetch_github_api_paginated", _dependency_tags)


def test_clearly_update_pins_source_and_swift_dependency_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The updater should persist only immutable source and dependency commits."""
    module = _load_module()
    updater = module.ClearlyUpdater()
    _install_release_network(monkeypatch, module)
    assert updater.get_derivation_validations() == (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
            systems=("aarch64-darwin",),
        ),
    )

    current = SourceEntry(
        version="3.2.0",
        hashes=[
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("vendorHash", updater.config.fake_hash),
        ],
    )
    calls = install_fixed_hash_stream(
        monkeypatch,
        (
            (None, _SRC_HASH),
            (None, _VENDOR_HASH),
        ),
    )

    events = run_async(
        collect_events(lambda emit: updater.update_stream(current, object(), emit=emit))
    )

    assert len(calls) == 2
    assert_nix_ast_equal(
        str(calls[0]["expr"]),
        _build_fetch_from_github_call(
            "Shpigford",
            "clearly",
            rev=_COMMIT,
            fetch_submodules=False,
        ),
    )
    assert_nix_ast_equal(
        str(calls[1]["expr"]),
        _build_package_path_attr_expr(
            "clearly",
            ".swiftDeps",
            system="aarch64-darwin",
            source_overrides={
                "clearly": SourceEntry(
                    version="3.2.0",
                    commit=_COMMIT,
                    urls=_DEPENDENCY_URLS,
                    hashes=[
                        HashEntry.create("srcHash", _SRC_HASH),
                        HashEntry.create("vendorHash", updater.config.fake_hash),
                    ],
                )
            },
        ),
    )

    result_events = [event for event in events if event.kind is UpdateEventKind.RESULT]
    assert len(result_events) == 1
    assert result_events[0].payload == SourceEntry(
        version="3.2.0",
        commit=_COMMIT,
        urls=_DEPENDENCY_URLS,
        hashes=[
            HashEntry.create("srcHash", _SRC_HASH),
            HashEntry.create("vendorHash", _VENDOR_HASH),
        ],
    )


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://github.com/owner/repo", "unsupported Swift dependency URL"),
        ("https://example.test/owner/repo", "unsupported Swift dependency URL"),
        ("https://github.com/owner", "unsupported Swift dependency URL"),
    ],
)
def test_clearly_rejects_unsupported_dependency_repositories(
    url: str,
    message: str,
) -> None:
    module = _load_module()

    with pytest.raises(TypeError, match=message):
        module.ClearlyUpdater._github_repo_from_url(url)


@pytest.mark.parametrize(
    ("payload", "error", "message"),
    [
        (b"[", RuntimeError, "Could not parse Clearly project.yml"),
        (b"[]", TypeError, "has no package mapping"),
        (b"packages: []", TypeError, "has no package mapping"),
        (
            b"packages: {cmark-gfm: []}",
            TypeError,
            "missing the cmark-gfm Swift dependency",
        ),
        (
            b"packages: {cmark-gfm: {url: https://example.test}}",
            TypeError,
            "must use a SwiftPM 'url' plus 'from' requirement",
        ),
        (
            b"packages: {cmark-gfm: {url: https://example.test, from: '2.1.0'}}",
            TypeError,
            "missing the KeyboardShortcuts Swift dependency",
        ),
    ],
)
def test_clearly_validates_upstream_dependency_metadata(
    payload: bytes,
    error: type[Exception],
    message: str,
) -> None:
    module = _load_module()

    with pytest.raises(error, match=message):
        module.ClearlyUpdater._dependency_requirements(payload)


def test_clearly_rejects_invalid_or_unsatisfied_swift_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    updater = module.ClearlyUpdater()

    with pytest.raises(RuntimeError, match="invalid SwiftPM version"):
        run_async(
            updater._resolve_dependency_url(
                object(),
                dependency="cmark-gfm",
                repo_url="https://github.com/brokenhandsio/cmark-gfm.git",
                minimum="not-semver",
            )
        )

    async def no_eligible_tags(*_args, **_kwargs):
        return [{"name": "3.0.0", "commit": {"sha": _CMARK_COMMIT}}]

    monkeypatch.setattr(module, "fetch_github_api_paginated", no_eligible_tags)
    with pytest.raises(RuntimeError, match="Could not resolve Clearly cmark-gfm"):
        run_async(
            updater._resolve_dependency_url(
                object(),
                dependency="cmark-gfm",
                repo_url="https://github.com/brokenhandsio/cmark-gfm.git",
                minimum="2.1.0",
            )
        )


@pytest.mark.parametrize("payload", [[], {"sha": "main"}])
def test_clearly_rejects_release_without_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    module = _load_module()

    async def github_api(*args, **kwargs):
        if args[1].endswith("/releases/latest"):
            return await _release_payload(*args, **kwargs)
        return payload

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        github_api,
    )
    error = TypeError if isinstance(payload, list) else RuntimeError
    with pytest.raises(error, match="has no immutable source commit"):
        run_async(
            module.ClearlyUpdater().fetch_latest(
                object(), context=UpdateContext(current=None)
            )
        )


@pytest.mark.parametrize(
    ("info", "message"),
    [
        (VersionInfo(version="3.2.0"), "missing an immutable source commit"),
        (
            VersionInfo(version="3.2.0", metadata={"commit": _COMMIT}),
            "missing Swift dependency URLs",
        ),
        (
            VersionInfo(
                version="3.2.0",
                metadata={"commit": _COMMIT, "dependency_urls": {}},
            ),
            "missing the cmark-gfm URL",
        ),
        (
            VersionInfo(
                version="3.2.0",
                metadata={
                    "commit": _COMMIT,
                    "dependency_urls": {"cmark-gfm": _DEPENDENCY_URLS["cmark-gfm"]},
                },
            ),
            "missing the KeyboardShortcuts URL",
        ),
    ],
)
def test_clearly_requires_complete_source_metadata(
    info: VersionInfo,
    message: str,
) -> None:
    module = _load_module()

    with pytest.raises((RuntimeError, TypeError), match=message):
        module.ClearlyUpdater().build_result(
            info,
            [HashEntry.create("srcHash", _SRC_HASH)],
        )


def test_hosted_sdk_patch_keeps_the_macos_15_toolbar_layout(tmp_path: Path) -> None:
    """Hosted Xcode 16.4 cannot type-check Liquid Glass; keep the 15.5 fallback."""
    module = _load_patch_module()
    toolbar = _write_toolbar(tmp_path, _toolbar_with_liquid_glass(module))

    assert module.main([str(tmp_path)]) == 0

    patched = toolbar.read_text(encoding="utf-8")
    assert "GlassEffectContainer" not in patched
    assert ".glassEffect(" not in patched
    assert "macOS 26.0" not in patched
    assert "legacyBody" in patched


def test_hosted_sdk_patch_is_a_noop_without_liquid_glass_apis(tmp_path: Path) -> None:
    """Older pins that already compile on macos-15 must not be rewritten."""
    module = _load_patch_module()
    original = "struct BottomToolbar: View { var body: some View { legacyBody } }\n"
    toolbar = _write_toolbar(tmp_path, original)

    module.patch_tree(tmp_path)

    assert toolbar.read_text(encoding="utf-8") == original


def test_hosted_sdk_patch_rejects_a_missing_toolbar(tmp_path: Path) -> None:
    """Absence is distinct from a toolbar that still contains Liquid Glass."""
    module = _load_patch_module()

    with pytest.raises(RuntimeError, match="missing Clearly/BottomToolbar.swift"):
        module.patch_tree(tmp_path)


def test_hosted_sdk_patch_rejects_drifted_toolbar_anchors(tmp_path: Path) -> None:
    """A new Liquid Glass layout must fail closed instead of compiling on 16.4."""
    module = _load_patch_module()
    toolbar = _write_toolbar(
        tmp_path,
        "struct BottomToolbar: View {\n"
        "    var body: some View { GlassEffectContainer(spacing: 0) { EmptyView() } }\n"
        "}\n",
    )
    original = toolbar.read_text(encoding="utf-8")

    with pytest.raises(
        RuntimeError, match="expected one Clearly hosted-SDK toolbar match"
    ):
        module.patch_tree(tmp_path)

    assert toolbar.read_text(encoding="utf-8") == original


def test_hosted_sdk_patch_rejects_remaining_liquid_glass_files(tmp_path: Path) -> None:
    """Toolbar rewriting is not enough when another Swift file still uses glass APIs."""
    module = _load_patch_module()
    toolbar = _write_toolbar(tmp_path, _toolbar_with_liquid_glass(module))
    extra = tmp_path / "Clearly" / "PreviewChrome.swift"
    extra.write_text(".glassEffect(.regular, in: .capsule)\n", encoding="utf-8")
    original = toolbar.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="still references Liquid Glass APIs"):
        module.patch_tree(tmp_path)

    assert toolbar.read_text(encoding="utf-8") == original
    assert extra.read_text(encoding="utf-8") == ".glassEffect(.regular, in: .capsule)\n"


def test_clearly_package_rewrites_liquid_glass_before_xcodebuild() -> None:
    """The Darwin derivation must run the hosted-SDK patcher on the unpacked tree."""
    package = expect_instance(
        nix_file_expr("packages/clearly/default.nix"),
        FunctionDefinition,
    )
    formal_names = {
        argument.name
        for argument in package.argument_set
        if isinstance(argument, Identifier)
    }
    assert "python3" in formal_names
    derivation = expect_instance(package.output, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(arguments.values, "nativeBuildInputs").value,
        "[ python3 xcodegen ]",
    )
    post_patch = expect_instance(
        expect_binding(arguments.values, "postPatch").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(post_patch.rebuild()))
    assert command_texts(shell, "__NIX_INTERP__") == [
        '__NIX_INTERP__ __NIX_INTERP__ "$PWD"',
    ]
    assert command_texts(shell, "ln") == ["ln -s __NIX_INTERP__ nix-swift-deps"]
