"""Semantic contracts for Buzz's pristine Mesh source derivation."""

import json
import runpy
import sys
from functools import cache
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._buzz_native_lock import buzz_native_lock_string
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    nix_attrset_call,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.paths import REPO_ROOT

_MESH_SOURCE_PATH = REPO_ROOT / "packages/buzz/native/mesh-llm.nix"
_MESH_VERSION = buzz_native_lock_string("meshLlm", "version")
_MESH_COMMIT = buzz_native_lock_string("meshLlm", "commit")
_LLAMA_COMMIT = buzz_native_lock_string("llamaCpp", "commit")

if TYPE_CHECKING:
    from nix_manipulator.expressions.scope import Scope


@cache
def _mesh_source_package() -> tuple[FunctionDefinition, FunctionCall]:
    package = expect_instance(
        parse_nix_expr(_MESH_SOURCE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        output = output.body
    return package, expect_instance(output, FunctionCall)


def _derivation_arguments() -> AttributeSet:
    _package, derivation = _mesh_source_package()
    return expect_instance(derivation.argument, AttributeSet)


def _package_scope() -> Scope:
    package, _derivation = _mesh_source_package()
    output = package.output
    while isinstance(output, Assertion):
        if output.scope:
            return output.scope
        output = output.body
    raise AssertionError("expected Mesh source let-bindings")


def _inventory_script() -> str:
    script = expect_instance(
        expect_binding(_package_scope(), "inventoryScript").value,
        IndentedString,
    )
    return dedent(indented_string_body(script.rebuild()))


def _write_fixture(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_inventory(
    monkeypatch: pytest.MonkeyPatch,
    script: Path,
    source: Path,
    output: Path,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            str(source),
            str(output),
            _MESH_VERSION,
            _MESH_COMMIT,
        ],
    )
    runpy.run_path(str(script), run_name="__main__")


@pytest.fixture(scope="module")
def inventory_program(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Materialize one script path so coverage combines both branch outcomes."""
    script = tmp_path_factory.mktemp("mesh-inventory") / "mesh_inventory.py"
    script.write_text(_inventory_script(), encoding="utf-8")
    return script


def test_mesh_source_fetch_is_exact_and_does_not_fetch_submodules() -> None:
    """The foundation must fetch only the audited pristine Mesh revision."""
    package, derivation = _mesh_source_package()

    assert {
        expect_instance(argument, Identifier).name for argument in package.argument_set
    } == {
        "fetchFromGitHub",
        "lib",
        "python3",
        "srcHash",
        "stdenvNoCC",
        "nativeLock",
    }
    assert_nix_ast_equal(
        expect_binding(_package_scope(), "version").value,
        "nativeLock.meshLlm.version or null",
    )
    assert_nix_ast_equal(
        expect_binding(_package_scope(), "commit").value,
        "nativeLock.meshLlm.commit or null",
    )
    assert_nix_ast_equal(
        expect_binding(_package_scope(), "src").value,
        nix_attrset_call(
            Identifier(name="fetchFromGitHub"),
            owner="Mesh-LLM",
            repo="mesh-llm",
            rev=Identifier(name="commit"),
            hash=Identifier(name="srcHash"),
            fetchSubmodules=False,
        ),
    )
    assert_nix_ast_equal(derivation.name, "stdenvNoCC.mkDerivation")


def test_mesh_source_contract_exactly_matches_the_package_gate() -> None:
    """Only the audited Mesh identity and feature graph can satisfy the slot."""
    passthru = expect_instance(
        expect_binding(_derivation_arguments().values, "passthru").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(passthru.values, "buzzNativeContract").value,
        """{
          kind = "mesh-llm";
          inherit version commit;
          sdkFeatures = [ "client" "serving" ];
          hostRuntimeFeatures = [ "dynamic-native-runtime" ];
        }""",
    )


def test_inventory_is_sorted_complete_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    inventory_program: Path,
) -> None:
    """Provenance should bind patch and packaging bytes to stable JSON."""
    source = tmp_path / "source"
    output = tmp_path / "result/provenance.json"
    for relative_path, content in {
        "third_party/llama.cpp/patches/z-last.patch": "last patch\n",
        "third_party/llama.cpp/patches/a-first.patch": "first patch\n",
        "scripts/build-llama.sh": "build script\n",
        "scripts/package-native-runtime.sh": "package script\n",
        "scripts/prepare-llama.sh": "prepare script\n",
        "third_party/llama.cpp/upstream.txt": f"{_LLAMA_COMMIT}\n",
    }.items():
        _write_fixture(source, relative_path, content)

    _run_inventory(monkeypatch, inventory_program, source, output)

    expected = {
        "llamaCpp": {
            "patches": [
                {
                    "name": "a-first.patch",
                    "sha256": (
                        "819eeed79f56367a4ade2118eda37e1839b23859f92614776d3a2e14e6fd8dc5"
                    ),
                },
                {
                    "name": "z-last.patch",
                    "sha256": (
                        "51e3c30143010c37035bd401537671c12f1879fedc03b58ce9f6a2c811cf63cd"
                    ),
                },
            ],
            "upstreamPin": _LLAMA_COMMIT,
        },
        "meshLlm": {
            "commit": _MESH_COMMIT,
            "version": _MESH_VERSION,
        },
        "packagingInputs": [
            {
                "path": "scripts/build-llama.sh",
                "sha256": (
                    "10155fbb97ea84315fc9c2827b6148802312fa3b39573d6b17baa0db241ff00e"
                ),
            },
            {
                "path": "scripts/package-native-runtime.sh",
                "sha256": (
                    "25f035fd6a0b837a6daee3324188a5431715128175dfc0dbe0653c5d9c49773b"
                ),
            },
            {
                "path": "scripts/prepare-llama.sh",
                "sha256": (
                    "aab67ee51df00a2050b8199ac0e19b9ea11aec8678923ac09c3bf346885ba2b0"
                ),
            },
            {
                "path": "third_party/llama.cpp/upstream.txt",
                "sha256": (
                    "bac5d6f06e193dff7866055e4c25daf800d33b46964870cf942659f478e2042f"
                ),
            },
        ],
        "schemaVersion": 1,
    }
    assert json.loads(output.read_text(encoding="utf-8")) == expected
    assert output.read_text(encoding="utf-8") == (
        json.dumps(expected, indent=2, sort_keys=True) + "\n"
    )


def test_inventory_fails_closed_when_mesh_has_no_llama_patches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    inventory_program: Path,
) -> None:
    """An empty ABI patch inventory must never attest a usable Mesh source."""
    source = tmp_path / "source"
    output = tmp_path / "result/provenance.json"
    for relative_path in (
        "scripts/build-llama.sh",
        "scripts/package-native-runtime.sh",
        "scripts/prepare-llama.sh",
        "third_party/llama.cpp/upstream.txt",
    ):
        _write_fixture(source, relative_path, f"fixture for {relative_path}\n")

    with pytest.raises(SystemExit, match="Mesh source contains no llama.cpp patches"):
        _run_inventory(monkeypatch, inventory_program, source, output)

    assert not output.exists()


def test_install_phase_only_copies_pristine_source_and_writes_provenance() -> None:
    """The source foundation must not patch or compile the Mesh workspace."""
    _package, _derivation = _mesh_source_package()
    arguments = _derivation_arguments()
    for name in (
        "dontUnpack",
        "dontConfigure",
        "dontBuild",
        "dontFixup",
        "strictDeps",
    ):
        assert_nix_ast_equal(expect_binding(arguments.values, name).value, "true")
    assert_nix_ast_equal(
        expect_binding(arguments.values, "nativeBuildInputs").value,
        "[ python3 ]",
    )
    native_inputs = expect_instance(
        expect_binding(arguments.values, "nativeBuildInputs").value,
        NixList,
    )
    assert [expect_instance(item, Identifier).name for item in native_inputs.value] == [
        "python3"
    ]

    install_phase = expect_instance(
        expect_binding(arguments.values, "installPhase").value,
        IndentedString,
    )
    shell = parse_shell(dedent(indented_string_body(install_phase.rebuild())))
    assert command_texts(shell, "cp") == ['cp -R "$src"/. "$sourceOutput"']
    assert command_texts(shell, "mkdir") == ['mkdir -p "$sourceOutput"']
    assert command_texts(shell, "__NIX_INTERP__") == [
        '__NIX_INTERP__ -c __NIX_INTERP__ "$sourceOutput" '
        '"$provenanceOutput" __NIX_INTERP__ __NIX_INTERP__'
    ]
    for prohibited_command in ("curl", "git", "make", "cmake", "patch"):
        assert command_texts(shell, prohibited_command) == []

    passthru = expect_instance(
        expect_binding(arguments.values, "passthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(_package_scope(), "sourceSubdir").value,
        '"share/mesh-llm/source"',
    )
    assert_nix_ast_equal(
        expect_binding(_package_scope(), "provenanceSubpath").value,
        '"share/mesh-llm/provenance.json"',
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "sourceSubdir").value,
        "sourceSubdir",
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "provenanceSubpath").value,
        "provenanceSubpath",
    )
