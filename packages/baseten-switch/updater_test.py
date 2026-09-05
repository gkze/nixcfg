"""Focused tests for the Baseten Switch source updater."""

import asyncio
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.update.nix import _build_fetch_from_github_call
from lib.update.paths import REPO_ROOT

COMMIT = "b" * 40


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/baseten-switch/updater.py",
        "baseten_switch_updater_dedicated_test",
    )


def _run(coro):
    return asyncio.run(coro)


def test_source_expression_tracks_the_immutable_upstream_commit() -> None:
    """Hash the resolved source commit rather than a mutable release tag."""
    module = _load_module()

    assert_nix_ast_equal(
        module.BasetenSwitchUpdater._src_expr(COMMIT),
        _build_fetch_from_github_call(
            "basetenlabs",
            "baseten-switch",
            rev=COMMIT,
            fetch_submodules=False,
        ),
    )


def test_package_serializes_go_packages_with_timing_sensitive_probe_tests() -> None:
    """The 20 ms health-probe fixture must not compete with parallel packages."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    check_phase = expect_instance(
        expect_binding(
            expect_instance(derivation.argument, AttributeSet).values,
            "checkPhase",
        ).value,
        IndentedString,
    )

    shell = parse_shell(indented_string_body(check_phase.rebuild()))
    assert command_texts(shell, "go") == ["go test -p 1 ./..."]


def test_package_applies_the_reviewed_nix_integration_patch() -> None:
    """Retain Nix integration without patches for fixes already shipped upstream."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    arguments = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(arguments.values, "patches").value,
        "[ ./nix-managed.patch ]",
    )


def test_package_embeds_the_exact_cli_used_by_the_copied_app() -> None:
    """The GUI and exposed CLI must resolve to one signed package artifact."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/baseten-switch/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    install_phase = expect_instance(
        expect_binding(
            expect_instance(derivation.argument, AttributeSet).values,
            "installPhase",
        ).value,
        IndentedString,
    )

    shell = parse_shell(indented_string_body(install_phase.rebuild()))
    assert (
        'install -m0755 "$TMPDIR/baseten-switch-cli" "$bundled_cli"'
        in command_texts(shell, "install")
    )
    assert command_texts(shell, "ln") == [
        'ln -s "$bundled_cli" "$out/bin/baseten-switch"'
    ]
    assert command_texts(shell, "/usr/bin/lipo") == [
        '/usr/bin/lipo "$bundled_cli" -verify_arch arm64 x86_64'
    ]
    codesign_commands = {
        " ".join(command.replace("\\\n", " ").split())
        for command in command_texts(shell, "/usr/bin/codesign")
    }
    assert (
        "/usr/bin/codesign --force --deep --sign - "
        "--preserve-metadata=identifier,entitlements,flags,runtime "
        '"$app_bundle"'
    ) in codesign_commands


def test_fetch_latest_skips_drafts_and_accepts_beta_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Follow Baseten's published prereleases while the product remains beta."""
    module = _load_module()
    updater = module.BasetenSwitchUpdater()
    calls: list[tuple[str, int]] = []

    async def _fetch(_session, endpoint: str, *, config, per_page: int):
        assert config is updater.config
        calls.append((endpoint, per_page))
        return [
            {"tag_name": "v0.4.0", "draft": True, "prerelease": True},
            {"tag_name": "v0.3.0", "draft": False, "prerelease": True},
        ]

    monkeypatch.setattr(module, "fetch_github_api_paginated", _fetch)
    monkeypatch.setattr(
        updater,
        "_resolve_release_tag_commit",
        lambda _session, tag: asyncio.sleep(0, result=COMMIT),
    )

    result = _run(updater.fetch_latest(object()))

    assert result.version == "0.3.0"
    assert result.metadata == {"commit": COMMIT, "tag": "v0.3.0"}
    assert calls == [("repos/basetenlabs/baseten-switch/releases", 100)]


def test_fetch_latest_rejects_non_mapping_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed release-list entries before selecting a version."""
    module = _load_module()
    updater = module.BasetenSwitchUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=["invalid"]),
    )

    with pytest.raises(TypeError, match="Unexpected release payload type: str"):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_requires_release_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject published release entries without a usable tag."""
    module = _load_module()
    updater = module.BasetenSwitchUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=[{"tag_name": "", "draft": False, "prerelease": True}],
        ),
    )

    with pytest.raises(RuntimeError, match="Missing tag_name in release payload"):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_requires_published_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report a clear failure when the API exposes drafts only."""
    module = _load_module()
    updater = module.BasetenSwitchUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api_paginated",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=[{"tag_name": "v0.4.0", "draft": True}],
        ),
    )

    with pytest.raises(RuntimeError, match="No published releases found"):
        _run(updater.fetch_latest(object()))
