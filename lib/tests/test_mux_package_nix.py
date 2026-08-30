"""Behavioral contracts for the mux package's offline dependency cache."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.tests._shell_ast import indented_string_body


def _lock_files_install_phase() -> str:
    package = expect_instance(
        nix_file_expr("packages/mux/default.nix"),
        FunctionDefinition,
    )
    offline_cache = expect_instance(
        expect_binding(package.output.scope, "offlineCache").value,
        FunctionCall,
    )
    offline_cache_args = expect_instance(offline_cache.argument, AttributeSet)
    lock_files = expect_instance(
        expect_binding(offline_cache_args.values, "src").value,
        FunctionCall,
    )
    lock_files_args = expect_instance(lock_files.argument, AttributeSet)
    install_phase = expect_instance(
        expect_binding(lock_files_args.values, "installPhase").value,
        IndentedString,
    )
    return indented_string_body(install_phase.rebuild())


def test_mux_electron_lock_comes_from_updater_metadata() -> None:
    """The derivation consumes the updater-owned lock instead of embedding it."""
    package = expect_instance(
        nix_file_expr("packages/mux/default.nix"),
        FunctionDefinition,
    )

    assert_nix_ast_equal(
        expect_binding(package.output.scope, "electronVersion").value,
        "selfSource.pins.electronVersion",
    )


@pytest.mark.parametrize("has_patches", [False, True])
def test_mux_lock_files_stage_optional_bun_patches(
    tmp_path: Path,
    *,
    has_patches: bool,
) -> None:
    """The staged Bun inputs must preserve patches without requiring them."""
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    (source / "package.json").write_text("{}\n", encoding="utf-8")
    (source / "bun.lock").write_text("lockfileVersion = 1\n", encoding="utf-8")
    if has_patches:
        patch = source / "patches/@ai-sdk%2Fxai@4.0.37.patch"
        patch.parent.mkdir()
        patch.write_text("patched\n", encoding="utf-8")

    bash = shutil.which("bash")
    assert bash is not None
    subprocess.run(  # noqa: S603
        [bash, "-euo", "pipefail", "-c", _lock_files_install_phase()],
        check=True,
        env={**os.environ, "src": str(source), "out": str(output)},
    )

    assert (output / "package.json").read_bytes() == b"{}\n"
    assert (output / "bun.lock").read_bytes() == b"lockfileVersion = 1\n"
    staged_patch = output / "patches/@ai-sdk%2Fxai@4.0.37.patch"
    assert staged_patch.exists() is has_patches
    if has_patches:
        assert staged_patch.read_bytes() == b"patched\n"
