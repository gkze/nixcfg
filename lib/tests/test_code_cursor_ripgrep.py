"""Cursor 3.30 moved ripgrep; the patch phase must follow the binary."""

import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
from nix_manipulator.expressions.expression import NixExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_binding_expr
from lib.tests._shell_ast import command_texts, parse_shell
from lib.update.paths import REPO_ROOT

_SCRIPT = REPO_ROOT / "overlays/code-cursor/chmod-bundled-ripgrep.sh"
_KNOWN_LAYOUTS = (
    "Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg",
    "Contents/Resources/app/node_modules.asar.unpacked/@vscode/ripgrep/bin/rg",
    "Contents/Resources/app/node_modules/@vscode/ripgrep-universal/bin/darwin-arm64/rg",
    "Contents/Resources/app/node_modules.asar.unpacked/@vscode/ripgrep-universal/bin/linux-x64/rg",
    "Contents/Resources/app/node_modules/@cursor/ripgrep-darwin-arm64/bin/rg",
    "resources/app/node_modules/@cursor/ripgrep-linux-x64/bin/rg",
)


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 -- owned ripgrep layout script
        [bash, str(_SCRIPT), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_binary(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def _unwrap(expr: NixExpression) -> NixExpression:
    while isinstance(expr, Parenthesis):
        expr = expect_instance(expr.value, NixExpression)
    return expr


def _applied_arguments(
    expr: NixExpression,
) -> tuple[NixExpression, list[NixExpression]]:
    arguments: list[NixExpression] = []
    current = expr
    while isinstance(current, FunctionCall):
        argument = current.argument
        assert argument is not None
        arguments.append(_unwrap(argument))
        name = current.name
        assert isinstance(name, NixExpression)
        current = name
    return current, list(reversed(arguments))


def _nix_interpolations(text: str) -> list[str]:
    found: list[str] = []
    index = 0
    while index < len(text):
        if not text.startswith("${", index):
            index += 1
            continue
        depth = 1
        cursor = index + 2
        start = cursor
        while cursor < len(text) and depth > 0:
            if text.startswith("${", cursor):
                depth += 1
                cursor += 2
                continue
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
                if depth == 0:
                    found.append(text[start:cursor])
            cursor += 1
        index = cursor
    return found


def _expected_interpolations(link: bool) -> list[str]:
    script = "./code-cursor/chmod-bundled-ripgrep.sh"
    if link:
        return [script, "prev.ripgrep"]
    return [script]


def _replacement_script(branch: NixExpression, *, link: bool) -> None:
    """Require the platform branch to replace one nixpkgs ripgrep line."""
    function, arguments = _applied_arguments(_unwrap(branch))
    selector = expect_instance(function, Select)
    assert selector.attribute == "replaceStringsExact"
    assert_nix_ast_equal(selector.expression, "final")
    assert len(arguments) == 3
    anchors = expect_instance(arguments[0], NixList)
    replacements = expect_instance(arguments[1], NixList)
    assert len(anchors.value) == 1
    assert len(replacements.value) == 1
    anchor = expect_instance(anchors.value[0], StringPrimitive).value
    script = expect_instance(replacements.value[0], IndentedString)
    commands = command_texts(parse_shell(textwrap.dedent(script.value).strip()))
    interpolations = _nix_interpolations(script.value)
    if link:
        assert anchor == "rm resources/app/node_modules/@vscode/ripgrep/bin/rg"
        assert commands == [
            "bash __NIX_INTERP__ --link-to __NIX_INTERP__/bin/rg",
            "mkdir -p resources/app/node_modules/@vscode/ripgrep/bin",
            "rm -f resources/app/node_modules/@vscode/ripgrep/bin/rg",
        ]
    else:
        assert anchor == (
            "chmod +x Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg"
        )
        assert commands == ["bash __NIX_INTERP__"]
    for interpolation, expected in zip(
        interpolations, _expected_interpolations(link), strict=True
    ):
        assert_nix_ast_equal(interpolation, expected)
    assert_nix_ast_equal(arguments[2], 'old.postPatch or ""')


@pytest.mark.parametrize("relative", _KNOWN_LAYOUTS)
def test_chmod_marks_each_known_ripgrep_layout(tmp_path: Path, relative: str) -> None:
    """DMG unpack can drop +x; every layout Cursor or VS Code has shipped is marked."""
    binary = tmp_path / relative
    _write_binary(binary, b"rg", 0o644)
    decoy = tmp_path / "other" / "bin" / "rg"
    _write_binary(decoy, b"no", 0o644)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(binary.stat().st_mode) == 0o755
    assert stat.S_IMODE(decoy.stat().st_mode) == 0o644


def test_missing_ripgrep_layout_fails(tmp_path: Path) -> None:
    """A new location is a failed build, not a skipped chmod."""
    result = _run(tmp_path)

    assert result.returncode != 0
    assert "known layout" in result.stderr


def test_link_retargets_bundled_binaries_without_replacing_nixpkgs(
    tmp_path: Path,
) -> None:
    """Linux keeps nixpkgs ripgrep at the path Cursor's resolver executes."""
    replacement = tmp_path / "nix-rg"
    _write_binary(replacement, b"nix", 0o755)
    root = tmp_path / "app"
    bundled = (
        root / "resources/app/node_modules/@cursor/ripgrep-linux-x64/bin/rg",
        root / "resources/app/node_modules/@vscode/ripgrep/bin/rg",
    )
    for binary in bundled:
        _write_binary(binary, b"bundled", 0o755)

    result = _run(root, "--link-to", str(replacement))

    assert result.returncode == 0, result.stderr
    for binary in bundled:
        assert binary.is_symlink()
        assert binary.read_bytes() == b"nix"
    assert replacement.read_bytes() == b"nix"
    assert not replacement.is_symlink()


def test_link_rejects_a_missing_replacement(tmp_path: Path) -> None:
    """A bad nixpkgs ripgrep path fails before any bundled binary is replaced."""
    binary = tmp_path / "resources/app/node_modules/@cursor/ripgrep-linux-x64/bin/rg"
    _write_binary(binary, b"bundled", 0o755)

    result = _run(tmp_path, "--link-to", str(tmp_path / "missing"))

    assert result.returncode != 0
    assert binary.read_bytes() == b"bundled"
    assert not binary.is_symlink()


def test_usage_requires_chmod_or_link(tmp_path: Path) -> None:
    """An unexpected argument does not chmod whatever happens to be in the tree."""
    result = _run(tmp_path, "--chmod")

    assert result.returncode != 0
    assert "usage:" in result.stderr


def test_code_cursor_overlay_rewrites_the_nixpkgs_ripgrep_phase() -> None:
    """Darwin chmods the real binary; Linux retargets it and keeps nixpkgs' link."""
    function, arguments = _applied_arguments(
        nix_file_binding_expr("overlays/default.nix", "code-cursor")
    )
    assert expect_instance(function, Identifier).name == "withManagedMacApp"
    assert len(arguments) == 2
    assert_nix_ast_equal(arguments[1], '"Cursor.app"')

    overridden, override_arguments = _applied_arguments(arguments[0])
    selector = expect_instance(overridden, Select)
    assert selector.attribute == "overrideAttrs"
    assert_nix_ast_equal(
        selector.expression,
        'final.mkSourceOverride "code-cursor" prev.code-cursor',
    )
    assert len(override_arguments) == 1
    definition = expect_instance(override_arguments[0], FunctionDefinition)
    assert expect_instance(definition.argument_set, Identifier).name == "old"
    body = expect_instance(_unwrap(definition.output), AttributeSet)
    phase = expect_instance(
        _unwrap(expect_binding(body.values, "postPatch").value),
        IfExpression,
    )
    assert_nix_ast_equal(phase.condition, "prev.stdenv.hostPlatform.isDarwin")
    _replacement_script(phase.consequence, link=False)
    _replacement_script(phase.alternative, link=True)
