"""Tests for the OpenCode Desktop updater module."""

import json
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.paths import REPO_ROOT
from lib.update.updaters import VersionInfo


def _load_updater_module() -> ModuleType:
    """Load the updater module under test."""
    return load_repo_module(
        "packages/opencode-desktop/updater.py",
        "opencode_desktop_updater_test",
    )


def test_opencode_desktop_darwin_build_exposes_only_system_codesign() -> None:
    """Upstream prepare scripts should find Apple's signer without broadening PATH."""
    package = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/opencode-desktop/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    version_assertion = package.output
    runtime_assertion = version_assertion.body
    derivation = expect_instance(runtime_assertion.body, FunctionCall)
    arguments = expect_instance(derivation.argument, AttributeSet)
    build_phase = expect_instance(
        expect_binding(arguments.values, "buildPhase").value,
        IfExpression,
    )
    shell = parse_shell(indented_string_body(build_phase.consequence.rebuild()))

    assert command_texts(shell, "ln") == [
        'ln -s /usr/bin/codesign "$codesignPath/codesign"'
    ]
    assert 'export PATH="$codesignPath:$PATH"' in command_texts(shell, "export")
    assert 'export PATH="/usr/bin:$PATH"' not in command_texts(shell, "export")


def test_opencode_desktop_updater_tracks_all_supported_platform_hashes() -> None:
    """The updater should preserve the full persisted platform hash matrix."""
    updater_cls = _load_updater_module().OpencodeDesktopUpdater
    payload = json.loads(
        (REPO_ROOT / "packages/opencode-desktop/sources.json").read_text(
            encoding="utf-8"
        )
    )

    assert updater_cls.input_name == "opencode"
    assert updater_cls.hash_type == "nodeModulesHash"
    assert updater_cls.platform_specific is True
    assert updater_cls.native_only is False
    hashes = payload.get("hashes")
    assert isinstance(hashes, list)
    assert len(hashes) == 4


def test_opencode_desktop_platform_targets_dedupes_current_platform() -> None:
    """The current platform should not be duplicated when already supported."""
    updater = _load_updater_module().OpencodeDesktopUpdater()

    assert updater._platform_targets("x86_64-linux") == (
        "x86_64-linux",
        "aarch64-darwin",
        "x86_64-darwin",
        "aarch64-linux",
    )


@pytest.mark.parametrize(
    ("base_latest", "current", "expected"),
    [
        pytest.param(False, None, False, id="superclass-false"),
        pytest.param(True, None, False, id="missing-entry"),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": [
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        "platform": "aarch64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                        "platform": "x86_64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                        "platform": "aarch64-linux",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
                        "platform": "x86_64-linux",
                    },
                    {
                        "hashType": "sha256",
                        "hash": "sha256-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE=",
                        "platform": "x86_64-linux",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF=",
                    },
                ],
            }),
            True,
            id="entries-match-supported-platforms",
        ),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": {
                    "aarch64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    "x86_64-darwin": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                    "aarch64-linux": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                    "x86_64-linux": "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
                },
            }),
            True,
            id="mapping-match-supported-platforms",
        ),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": [
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        "platform": "aarch64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                        "platform": "x86_64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                        "platform": "aarch64-linux",
                    },
                ],
            }),
            False,
            id="entries-mismatch-missing-platform",
        ),
    ],
)
def test_opencode_desktop_is_latest_validates_platform_hash_coverage(
    monkeypatch: pytest.MonkeyPatch,
    base_latest: bool,
    current: SourceEntry | None,
    expected: bool,
) -> None:
    """Latest checks require a base match and a complete supported-platform set."""
    module = _load_updater_module()
    updater = module.OpencodeDesktopUpdater()

    async def _base_is_latest(self, context, info):
        _ = (self, context, info)
        return base_latest

    monkeypatch.setattr(module.FlakeInputHashUpdater, "_is_latest", _base_is_latest)

    assert _run(updater._is_latest(current, VersionInfo(version="1.2.3"))) is expected
