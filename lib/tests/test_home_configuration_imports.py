"""Regression checks for George's home-manager import wiring."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT

_NIX_ANTIQUOTATION = re.compile(r"\$\{\s*(.*?)\s*\}", re.DOTALL)
_VSCODE_SETTINGS_HOME_FILE_KEY = (
    '"${config.home.homeDirectory}/Library/Application Support/'
    '${config.programs.vscode.nameShort or "Code - Insiders"}/User/settings.json"'
)


def _normalize_nix_antiquotation_whitespace(value: object) -> str:
    return _NIX_ANTIQUOTATION.sub(
        lambda match: "${" + " ".join(match.group(1).split()) + "}",
        str(value),
    )


def _expect_binding_by_normalized_name(bindings, name: str) -> Binding:
    expected_name = _normalize_nix_antiquotation_whitespace(name)
    for binding in bindings:
        if not isinstance(binding, Binding):
            continue
        if _normalize_nix_antiquotation_whitespace(binding.name) == expected_name:
            return binding
    message = f"missing binding {name}"
    raise AssertionError(message)


@cache
def _configuration_output() -> AttributeSet:
    """Parse George's home configuration module and return its output attrset."""
    expr = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "home/george/configuration.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


def test_george_home_configuration_imports_canonical_exported_modules() -> None:
    """George's home config should consume the canonical exported home modules."""
    actual = expect_binding(_configuration_output().values, "imports").value

    assert_nix_ast_equal(
        actual,
        """
[
  {
    darwin = ./darwin.nix;
    linux = ./nixos.nix;
  }
  .${slib.kernel system}
  outputs.homeModules.nixcfgLanguageBun
  outputs.homeModules.nixcfgGit
  outputs.homeModules.nixcfgLanguageGo
  ./nixvim.nix
  ./zed.nix
  outputs.homeModules.nixcfgOpencode
  outputs.homeModules.nixcfgPackages
  outputs.homeModules.nixcfgZen
  outputs.homeModules.nixcfgLanguagePython
  outputs.homeModules.nixcfgLanguageRust
  outputs.homeModules.nixcfgStylix
  outputs.homeModules.nixcfgZsh
  inputs.catppuccin.homeModules.catppuccin
]
""",
    )


def test_george_home_configuration_drops_opencode_electron_state_bridge() -> None:
    """OpenCode Electron should use the normalized app id without a side bridge."""
    home = expect_instance(
        expect_binding(_configuration_output().values, "home").value,
        AttributeSet,
    )
    activation = expect_instance(
        expect_binding(home.values, "activation").value,
        AttributeSet,
    )

    assert "opencodeElectronStateLinks" not in {
        binding.name for binding in activation.values
    }


def test_george_home_configuration_materializes_vscode_settings_after_link_generation() -> (
    None
):
    """VS Code settings should be copied into place after Home Manager links settle."""
    home = expect_instance(
        expect_binding(_configuration_output().values, "home").value,
        AttributeSet,
    )
    activation = expect_instance(
        expect_binding(home.values, "activation").value,
        AttributeSet,
    )
    materialize = expect_binding(activation.values, "materializeVscodeSettings").value
    materialize_text = str(materialize)
    settings_key = expect_scope_binding(materialize, "vscodeSettingsHomeFileKey").value

    assert 'lib.hm.dag.entryAfter [ "linkGeneration" ]' in materialize_text
    assert _normalize_nix_antiquotation_whitespace(
        settings_key
    ) == _normalize_nix_antiquotation_whitespace(_VSCODE_SETTINGS_HOME_FILE_KEY)
    assert 'run cp "${vscodeSettingsSource}" "$tmp_settings"' in materialize_text
    assert 'run mv "$tmp_settings" "$settings_path"' in materialize_text


def test_george_home_configuration_disables_direct_vscode_settings_symlink() -> None:
    """VS Code settings should opt out of direct Home Manager file ownership."""
    home = expect_instance(
        expect_binding(_configuration_output().values, "home").value,
        AttributeSet,
    )
    files = expect_instance(expect_binding(home.values, "file").value, AttributeSet)
    settings_entry = expect_instance(
        _expect_binding_by_normalized_name(
            files.values,
            _VSCODE_SETTINGS_HOME_FILE_KEY,
        ).value,
        AttributeSet,
    )
    assert_nix_ast_equal(expect_binding(settings_entry.values, "enable").value, "false")
