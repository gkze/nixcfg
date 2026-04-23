"""Regression checks for George's home-manager import wiring."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


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


def test_george_home_configuration_preserves_opencode_settings_store_filename() -> None:
    """The shared opencode state links should keep the persisted settings filename."""
    home = expect_instance(
        expect_binding(_configuration_output().values, "home").value,
        AttributeSet,
    )
    activation = expect_instance(
        expect_binding(home.values, "activation").value,
        AttributeSet,
    )
    link_step = expect_instance(
        expect_binding(activation.values, "opencodeElectronStateLinks").value,
        FunctionCall,
    )

    assert (
        'link_state_file "$OPENCODE_TAURI_STATE_DIR/opencode.settings.dat" '
        '"$OPENCODE_ELECTRON_STATE_DIR/opencode.settings.dat"' in str(link_step)
    )


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

    assert 'lib.hm.dag.entryAfter [ "linkGeneration" ]' in materialize_text
    assert (
        '"${config.home.homeDirectory}/Library/Application Support/'
        '${config.programs.vscode.nameShort}/User/settings.json"' in materialize_text
    )
    assert 'run cp "${vscodeSettingsSource}" "$tmp_settings"' in materialize_text
    assert 'run mv "$tmp_settings" "$settings_path"' in materialize_text


def test_george_home_configuration_disables_direct_vscode_settings_symlink() -> None:
    """VS Code settings should opt out of direct Home Manager file ownership."""
    home = expect_instance(
        expect_binding(_configuration_output().values, "home").value,
        AttributeSet,
    )
    files = expect_instance(expect_binding(home.values, "file").value, AttributeSet)

    assert (
        '"${config.home.homeDirectory}/Library/Application Support/'
        '${config.programs.vscode.nameShort}/User/settings.json".enable = false;'
        in str(files)
    )
