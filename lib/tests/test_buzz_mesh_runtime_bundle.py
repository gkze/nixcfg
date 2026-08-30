"""Semantic contracts for Buzz's repo-owned Mesh native runtime bundle."""

import hashlib
import json
import os
import runpy
import sys
from collections.abc import Callable
from functools import cache
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING, cast

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._buzz_native_lock import (
    buzz_native_lock_string,
    render_buzz_native_lock_interpolations,
)
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.scope import Scope

_BUNDLE_PATH = REPO_ROOT / "packages/buzz/native/mesh-runtime-bundle.nix"
_BUZZ_PACKAGE_PATH = REPO_ROOT / "packages/buzz/package.nix"
_MESH_VERSION = buzz_native_lock_string("meshLlm", "version")
_MESH_COMMIT = buzz_native_lock_string("meshLlm", "commit")
_SKIPPY_ABI = buzz_native_lock_string("meshLlm", "skippyAbi")
_LLAMA_COMMIT = buzz_native_lock_string("llamaCpp", "commit")
_PACKAGING_PATHS = (
    "scripts/build-llama.sh",
    "scripts/package-native-runtime.sh",
    "scripts/prepare-llama.sh",
    "third_party/llama.cpp/upstream.txt",
)


@cache
def _bundle_package() -> tuple[FunctionDefinition, FunctionCall]:
    package = expect_instance(
        parse_nix_expr(_BUNDLE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        output = output.body
    return package, expect_instance(output, FunctionCall)


def _package_scope() -> Scope:
    package, _derivation = _bundle_package()
    output = package.output
    while isinstance(output, Assertion):
        if output.scope:
            return output.scope
        output = output.body
    raise AssertionError("expected bundle package let-bindings")


def _assertion_conditions() -> list[object]:
    package, _derivation = _bundle_package()
    conditions: list[object] = []
    output = package.output
    while isinstance(output, Assertion):
        conditions.append(output.expression)
        output = output.body
    return conditions


def _derivation_arguments() -> AttributeSet:
    _package, derivation = _bundle_package()
    return expect_instance(derivation.argument, AttributeSet)


def _bundle_script() -> str:
    script = expect_instance(
        expect_binding(_package_scope(), "bundleScript").value,
        IndentedString,
    )
    return render_buzz_native_lock_interpolations(
        dedent(indented_string_body(script.rebuild()))
    )


@cache
def _buzz_package_scope() -> Scope:
    package = expect_instance(
        parse_nix_expr(_BUZZ_PACKAGE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    asserted = expect_instance(package.output, Assertion)
    return asserted.body.scope


@cache
def _buzz_expected_contracts() -> AttributeSet:
    return expect_instance(
        expect_binding(_buzz_package_scope(), "expectedNativeContracts").value,
        AttributeSet,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_text(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_mesh_fixture(root: Path) -> tuple[Path, Path]:
    source = root / "mesh-source"
    contents = {
        "scripts/build-llama.sh": "build llama from the pinned source\n",
        "scripts/package-native-runtime.sh": "package the native runtime manifest\n",
        "scripts/prepare-llama.sh": "prepare the pinned patch queue\n",
        "third_party/llama.cpp/upstream.txt": f"{_LLAMA_COMMIT}\n",
        "third_party/llama.cpp/patches/0001-skippy.patch": "skippy ABI patch\n",
        "third_party/llama.cpp/patches/0002-metal.patch": "Metal patch\n",
    }
    for relative_path, content in contents.items():
        _write_text(source, relative_path, content)

    provenance = {
        "schemaVersion": 1,
        "meshLlm": {"version": _MESH_VERSION, "commit": _MESH_COMMIT},
        "llamaCpp": {
            "upstreamPin": _LLAMA_COMMIT,
            "patches": [
                {
                    "name": path.name,
                    "sha256": _sha256(path),
                }
                for path in sorted(
                    (source / "third_party/llama.cpp/patches").glob("*.patch")
                )
            ],
        },
        "packagingInputs": [
            {"path": relative_path, "sha256": _sha256(source / relative_path)}
            for relative_path in _PACKAGING_PATHS
        ],
    }
    provenance_path = root / "mesh-provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return source, provenance_path


def _library_payload(
    *,
    name: str,
    dependencies: list[str] | None = None,
    symbols: list[str] | None = None,
    architecture: str = "arm64",
    signature: str = "adhoc",
    install_id: str | None = None,
) -> dict[str, object]:
    return {
        "architecture": architecture,
        "dependencies": dependencies or [],
        "install_id": install_id or f"@rpath/{name}",
        "signature": signature,
        "symbols": symbols or [],
    }


def _write_library(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _read_library(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_llama_fixture(root: Path) -> tuple[Path, Path, Path]:
    llama = root / "llama"
    dependency = llama / "lib/libdependency.1.dylib"
    plugin = llama / "lib/plugins/libplugin.dylib"
    primary = llama / "lib/libengine.1.dylib"
    _write_library(
        dependency,
        _library_payload(name=dependency.name),
    )
    _write_library(
        plugin,
        _library_payload(name=plugin.name),
    )
    _write_library(
        primary,
        _library_payload(
            name=primary.name,
            dependencies=[
                "/usr/lib/libSystem.B.dylib",
                "/System/Library/Frameworks/Metal.framework/Versions/A/Metal",
                "@loader_path/libdependency.dylib",
                "@rpath/libdependency.dylib",
                "@rpath/plugins/libplugin.dylib",
            ],
            symbols=["_skippy_abi_version"],
        ),
    )
    (llama / "lib/libdependency.dylib").symlink_to(dependency.name)
    (llama / "lib/libengine.dylib").symlink_to(primary.name)
    return llama, dependency, primary


@pytest.fixture(scope="module")
def bundle_program(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Materialize one script path so coverage combines all branch outcomes."""
    script = tmp_path_factory.mktemp("mesh-runtime-bundle") / "bundle.py"
    script.write_text(_bundle_script(), encoding="utf-8")
    return script


@pytest.fixture(scope="module")
def embedded_program(bundle_program: Path) -> dict[str, object]:
    """Load helper functions without executing the command-line entrypoint."""
    return runpy.run_path(str(bundle_program), run_name="buzz_mesh_runtime_bundle")


def _embedded_function(
    program: dict[str, object],
    name: str,
) -> Callable[..., object]:
    return cast("Callable[..., object]", program[name])


@pytest.fixture(scope="module")
def fake_macho_tools(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Provide deterministic command boundaries without inspecting fake bytes."""
    root = tmp_path_factory.mktemp("mesh-runtime-tools")
    dispatcher = root / "macho-tool"
    dispatcher.write_text(
        f"#!{sys.executable}\n"
        + dedent(
            """\
            import json
            import sys
            from pathlib import Path

            tool = Path(sys.argv[0]).name
            library = Path(sys.argv[-1])
            payload = json.loads(library.read_text(encoding="utf-8"))

            if tool == "lipo":
                print(payload["architecture"])
            elif tool == "otool" and "-D" in sys.argv:
                print(f"{library}:")
                print(payload["install_id"])
            elif tool == "otool" and "-L" in sys.argv:
                print(f"{library}:")
                print()
                for dependency in [payload["install_id"], *payload["dependencies"]]:
                    print(f"    {dependency} (compatibility version 0.0.0, current version 0.0.0)")
            elif tool == "nm":
                print("\\n".join(payload["symbols"]))
            elif tool == "codesign" and "--verify" in sys.argv:
                if payload["signature"] == "invalid":
                    print("invalid signature", file=sys.stderr)
                    raise SystemExit(1)
            elif tool == "codesign":
                print(f'Signature={payload["signature"]}', file=sys.stderr)
            else:
                print(f"unsupported fake invocation: {sys.argv}", file=sys.stderr)
                raise SystemExit(2)
            """
        ),
        encoding="utf-8",
    )
    dispatcher.chmod(0o755)
    tools: dict[str, Path] = {}
    for name in ("codesign", "lipo", "nm", "otool"):
        path = root / name
        path.symlink_to(dispatcher.name)
        tools[name] = path
    return tools


def _run_bundle(
    monkeypatch: pytest.MonkeyPatch,
    program: Path,
    mesh_source: Path,
    mesh_provenance: Path,
    llama_root: Path,
    output: Path,
    tools: dict[str, Path],
    *,
    resource_subpaths: list[str] | None = None,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(program),
            str(mesh_source),
            str(mesh_provenance),
            str(llama_root),
            str(output),
            "lib",
            json.dumps(resource_subpaths or []),
            str(tools["lipo"]),
            str(tools["otool"]),
            str(tools["nm"]),
            str(tools["codesign"]),
        ],
    )
    runpy.run_path(str(program), run_name="__main__")


def test_bundle_constructs_exact_sources_from_url_qualified_hashes() -> None:
    """Callers provide hashes, while the module owns both pinned source builders."""
    package, derivation = _bundle_package()
    scope = _package_scope()
    conditions = _assertion_conditions()

    assert {
        expect_instance(argument, Identifier).name for argument in package.argument_set
    } == {
        "cctools",
        "cmake",
        "fetchFromGitHub",
        "gitMinimal",
        "lib",
        "llamaCppSrcHash",
        "meshLlmSrcHash",
        "nativeLock",
        "ninja",
        "python3",
        "stdenv",
        "stdenvNoCC",
    }
    assert_nix_ast_equal(derivation.name, "stdenvNoCC.mkDerivation")
    assert len(conditions) == 11
    assert_nix_ast_equal(
        conditions[0],
        'stdenvNoCC.hostPlatform.system == "aarch64-darwin"',
    )
    assert_nix_ast_equal(
        expect_binding(scope, "expectedMeshContract").value,
        """{
          kind = "mesh-llm";
          version = meshLlmVersion;
          commit = meshLlmCommit;
          sdkFeatures = [ "client" "serving" ];
          hostRuntimeFeatures = [ "dynamic-native-runtime" ];
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "expectedLlamaContract").value,
        """{
          kind = "llama.cpp";
          commit = llamaCppCommit;
          target = "aarch64-apple-darwin";
          backend = "metal";
          linkMode = "dynamic";
          buildType = "Release";
          ggmlNative = false;
          cmakeOptions = {
            BUILD_SHARED_LIBS = true;
            GGML_METAL = true;
            LLAMA_BUILD_APP = false;
            LLAMA_BUILD_EXAMPLES = false;
            LLAMA_BUILD_SERVER = false;
            LLAMA_BUILD_TESTS = false;
            LLAMA_CURL = false;
            LLAMA_OPENSSL = false;
          };
        }""",
    )
    expected_conditions = [
        "builtins.isString meshLlmVersion",
        'builtins.isString meshLlmCommit && builtins.match "[0-9a-f]{40}" meshLlmCommit != null',
        'builtins.isString skippyAbi && builtins.match "[0-9]+\\\\.[0-9]+\\\\.[0-9]+" skippyAbi != null',
        'builtins.isString llamaCppCommit && builtins.match "[0-9a-f]{40}" llamaCppCommit != null',
        "(meshLlm.passthru.buzzNativeContract or null) == expectedMeshContract",
        '(meshLlm.sourceSubdir or null) == "share/mesh-llm/source"',
        '(meshLlm.provenanceSubpath or null) == "share/mesh-llm/provenance.json"',
        "(llamaCpp.passthru.buzzNativeContract or null) == expectedLlamaContract",
        '(llamaCpp.libSubdir or null) == "lib"',
        "builtins.isList (llamaCpp.resourceSubpaths or null)",
    ]
    for condition, expected in zip(conditions[1:], expected_conditions, strict=True):
        assert_nix_ast_equal(condition, expected)

    assert_nix_ast_equal(
        expect_binding(scope, "meshLlm").value,
        """import ./mesh-llm.nix {
          inherit fetchFromGitHub lib nativeLock python3 stdenvNoCC;
          srcHash = meshLlmSrcHash;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "llamaCpp").value,
        """import ./llama-cpp.nix {
          inherit cctools cmake fetchFromGitHub gitMinimal lib nativeLock ninja stdenv;
          meshSrcHash = meshLlmSrcHash;
          srcHash = llamaCppSrcHash;
        }""",
    )


def test_bundle_contract_independently_satisfies_the_package_gate() -> None:
    """The emitted digest manifest must satisfy the package's exact contract."""
    scope = _package_scope()
    attrs = _derivation_arguments()
    passthru = expect_instance(
        expect_binding(attrs.values, "passthru").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(scope, "implementedBundleContract").value,
        """{
          kind = "mesh-native-runtime-bundle";
          meshVersion = meshLlmVersion;
          inherit skippyAbi;
          target = "aarch64-apple-darwin";
          platform = {
            os = "macos";
            arch = "aarch64";
          };
          backend = "metal";
          sourceInputs = [ "meshLlm" "llamaCpp" ];
          manifestHasFileDigests = true;
          releaseArchiveAllowed = false;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(
            _buzz_expected_contracts().values,
            "meshRuntimeBundle",
        ).value,
        """{
          kind = "mesh-native-runtime-bundle";
          meshVersion = expectedMeshLlmVersion;
          skippyAbi = expectedSkippyAbi;
          target = "aarch64-apple-darwin";
          platform = {
            os = "macos";
            arch = "aarch64";
          };
          backend = "metal";
          sourceInputs = [ "meshLlm" "llamaCpp" ];
          manifestHasFileDigests = true;
          releaseArchiveAllowed = false;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "buzzNativeContract").value,
        "implementedBundleContract",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "runtimeId").value,
        '"meshllm-native-runtime-darwin-aarch64-metal"',
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "manifestSubpath").value,
        '"manifest.json"',
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "runtimeId").value,
        "runtimeId",
    )


def test_package_supplies_exact_source_hashes_to_the_internal_bundle_builders() -> None:
    """Only URL-qualified hash entries may unlock the incomplete bundle artifact."""
    scope = _buzz_package_scope()
    assert_nix_ast_equal(
        expect_binding(scope, "meshRuntimeBundleNative").value,
        """if meshLlmSrcHashEntry == null || llamaCppSrcHashEntry == null then
          null
        else
          import ./native/mesh-runtime-bundle.nix {
            inherit cctools fetchFromGitHub lib nativeLock python3 stdenv stdenvNoCC;
            inherit (pkgs) cmake gitMinimal ninja;
            meshLlmSrcHash = meshLlmSrcHashEntry.hash;
            llamaCppSrcHash = llamaCppSrcHashEntry.hash;
          }""",
    )


def test_install_phase_is_an_unpacked_offline_python_boundary() -> None:
    """The derivation must create a local directory, never an archive or download."""
    attrs = _derivation_arguments()
    for name in (
        "dontUnpack",
        "dontConfigure",
        "dontBuild",
        "dontFixup",
        "strictDeps",
    ):
        assert_nix_ast_equal(expect_binding(attrs.values, name).value, "true")
    assert_nix_ast_equal(
        expect_binding(attrs.values, "nativeBuildInputs").value,
        "[ cctools python3 ]",
    )

    install_phase = expect_instance(
        expect_binding(attrs.values, "installPhase").value,
        IndentedString,
    )
    shell = parse_shell(dedent(indented_string_body(install_phase.rebuild())))
    assert command_texts(shell, "__NIX_INTERP__/bin/python3") == [
        "__NIX_INTERP__/bin/python3 -c __NIX_INTERP__ __NIX_INTERP__ "
        '__NIX_INTERP__ __NIX_INTERP__ "$out" lib __NIX_INTERP__ '
        "__NIX_INTERP__/bin/lipo __NIX_INTERP__/bin/otool __NIX_INTERP__/bin/nm "
        "/usr/bin/codesign"
    ]
    for prohibited in ("curl", "git", "tar", "wget", "zip"):
        assert command_texts(shell, prohibited) == []


def test_bundle_is_complete_deterministic_and_dependency_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bundle_program: Path,
    fake_macho_tools: dict[str, Path],
) -> None:
    """All physical dylibs load once, aliases remain intact, and bytes are hashed."""
    mesh_source, mesh_provenance = _write_mesh_fixture(tmp_path)
    llama_root, dependency, primary = _write_llama_fixture(tmp_path)
    first_output = tmp_path / "first-output"
    second_output = tmp_path / "second-output"

    _run_bundle(
        monkeypatch,
        bundle_program,
        mesh_source,
        mesh_provenance,
        llama_root,
        first_output,
        fake_macho_tools,
    )
    _run_bundle(
        monkeypatch,
        bundle_program,
        mesh_source,
        mesh_provenance,
        llama_root,
        second_output,
        fake_macho_tools,
    )

    assert (first_output / "manifest.json").read_bytes() == (
        second_output / "manifest.json"
    ).read_bytes()
    manifest = json.loads((first_output / "manifest.json").read_text(encoding="utf-8"))
    expected_files = {
        "lib/libdependency.1.dylib": _sha256(dependency),
        "lib/libdependency.dylib": _sha256(dependency),
        "lib/libengine.1.dylib": _sha256(primary),
        "lib/libengine.dylib": _sha256(primary),
        "lib/plugins/libplugin.dylib": _sha256(
            llama_root / "lib/plugins/libplugin.dylib"
        ),
    }
    assert manifest == {
        "runtime": {
            "backend": {"kind": "metal"},
            "files": expected_files,
            "id": "meshllm-native-runtime-darwin-aarch64-metal",
            "libraries": [
                "lib/libdependency.1.dylib",
                "lib/plugins/libplugin.dylib",
                "lib/libengine.1.dylib",
            ],
            "mesh_version": _MESH_VERSION,
            "platform": {
                "arch": "aarch64",
                "os": "macos",
                "target": "aarch64-apple-darwin",
            },
            "rank": 0,
            "skippy_abi": _SKIPPY_ABI,
        }
    }
    assert (first_output / "lib/libdependency.dylib").readlink() == Path(
        dependency.name
    )
    assert (first_output / "lib/libengine.dylib").readlink() == Path(primary.name)
    assert (first_output / "lib/libdependency.1.dylib").read_bytes() == (
        dependency.read_bytes()
    )
    assert (first_output / "lib/libengine.1.dylib").read_bytes() == primary.read_bytes()
    assert not list(first_output.glob("*.tar*"))
    assert not list(first_output.glob("*.zip"))


def _apply_runtime_drift(case: str, dependency: Path, primary: Path) -> None:
    dependency_payload = _read_library(dependency)
    primary_payload = _read_library(primary)
    if case == "architecture":
        dependency_payload["architecture"] = "arm64 x86_64"
    elif case == "install-id":
        dependency_payload["install_id"] = "/nix/store/unrelocatable.dylib"
    elif case == "missing-install-id":
        dependency_payload["install_id"] = ""
    elif case == "absolute-dependency":
        primary_payload["dependencies"] = ["/nix/store/unowned/libforeign.dylib"]
    elif case == "unresolved-dependency":
        primary_payload["dependencies"] = ["@loader_path/libmissing.dylib"]
    elif case == "signature":
        dependency_payload["signature"] = "Developer ID"
    elif case == "invalid-signature":
        dependency_payload["signature"] = "invalid"
    elif case == "missing-abi":
        primary_payload["symbols"] = []
    elif case == "multiple-abi":
        dependency_payload["symbols"] = ["_skippy_abi_version"]
    elif case == "cycle":
        dependency_payload["dependencies"] = ["@loader_path/libengine.dylib"]
    elif case == "primary-is-dependency":
        primary_payload["dependencies"] = []
        dependency_payload["dependencies"] = ["@loader_path/libengine.dylib"]
    else:
        message = f"unknown runtime drift case: {case}"
        raise AssertionError(message)
    _write_library(dependency, dependency_payload)
    _write_library(primary, primary_payload)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("architecture", "must be arm64-only"),
        ("install-id", "unexpected install ID"),
        ("missing-install-id", "expected one install ID"),
        ("absolute-dependency", "unsupported dependency"),
        ("unresolved-dependency", "unresolved local dependency"),
        ("signature", "must use an ad-hoc signature"),
        ("invalid-signature", "code signature verification failed"),
        ("missing-abi", "exactly one physical dylib must export"),
        ("multiple-abi", "exactly one physical dylib must export"),
        ("cycle", "dependency cycle"),
    ],
)
def test_bundle_rejects_invalid_macho_or_loader_graphs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bundle_program: Path,
    fake_macho_tools: dict[str, Path],
    case: str,
    message: str,
) -> None:
    """Architecture, signatures, relocation, and loader order all fail closed."""
    mesh_source, mesh_provenance = _write_mesh_fixture(tmp_path)
    llama_root, dependency, primary = _write_llama_fixture(tmp_path)
    _apply_runtime_drift(case, dependency, primary)

    with pytest.raises(SystemExit, match=message):
        _run_bundle(
            monkeypatch,
            bundle_program,
            mesh_source,
            mesh_provenance,
            llama_root,
            tmp_path / "output",
            fake_macho_tools,
        )

    assert not (tmp_path / "output/manifest.json").exists()


def test_bundle_keeps_the_abi_library_last_when_earlier_libraries_depend_on_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bundle_program: Path,
    fake_macho_tools: dict[str, Path],
) -> None:
    """Dyld resolves loader-relative dependencies while Mesh probes the final entry."""
    mesh_source, mesh_provenance = _write_mesh_fixture(tmp_path)
    llama_root, dependency, primary = _write_llama_fixture(tmp_path)
    _apply_runtime_drift("primary-is-dependency", dependency, primary)
    output = tmp_path / "output"

    _run_bundle(
        monkeypatch,
        bundle_program,
        mesh_source,
        mesh_provenance,
        llama_root,
        output,
        fake_macho_tools,
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    libraries = manifest["runtime"]["libraries"]
    assert libraries.index("lib/libdependency.1.dylib") < libraries.index(
        "lib/libengine.1.dylib"
    )
    assert libraries[-1] == "lib/libengine.1.dylib"


def _apply_provenance_drift(
    case: str,
    mesh_source: Path,
    mesh_provenance: Path,
) -> None:
    provenance = json.loads(mesh_provenance.read_text(encoding="utf-8"))
    if case == "schema":
        provenance["schemaVersion"] = 2
    elif case == "mesh-identity":
        provenance["meshLlm"]["commit"] = "0" * 40
    elif case == "llama-pin":
        provenance["llamaCpp"]["upstreamPin"] = "0" * 40
    elif case == "upstream-source":
        (mesh_source / "third_party/llama.cpp/upstream.txt").write_text(
            f"{_LLAMA_COMMIT}\nextra\n",
            encoding="utf-8",
        )
    elif case == "patch-directory":
        (mesh_source / "third_party/llama.cpp/patches").rename(
            mesh_source / "third_party/llama.cpp/moved-patches"
        )
    elif case == "empty-patches":
        for path in (mesh_source / "third_party/llama.cpp/patches").iterdir():
            path.unlink()
    elif case == "patch-entry":
        _write_text(
            mesh_source,
            "third_party/llama.cpp/patches/README",
            "not a patch\n",
        )
    elif case == "patch-inventory":
        provenance["llamaCpp"]["patches"].pop()
    elif case == "patch-schema":
        provenance["llamaCpp"]["patches"][0]["unexpected"] = True
    elif case == "patch-digest":
        provenance["llamaCpp"]["patches"][0]["sha256"] = "0" * 64
    elif case == "packaging-inventory":
        provenance["packagingInputs"].pop()
    elif case == "packaging-schema":
        provenance["packagingInputs"][0]["unexpected"] = True
    elif case == "digest-schema":
        provenance["packagingInputs"][0]["sha256"] = "not-a-digest"
    elif case == "source-hash":
        provenance["packagingInputs"][0]["sha256"] = "0" * 64
    elif case == "missing-source":
        (mesh_source / "scripts/build-llama.sh").unlink()
    elif case == "invalid-json":
        mesh_provenance.write_text("{", encoding="utf-8")
        return
    elif case == "missing-provenance":
        mesh_provenance.rename(mesh_provenance.with_suffix(".missing"))
        return
    else:
        message = f"unknown provenance drift case: {case}"
        raise AssertionError(message)
    mesh_provenance.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _apply_inventory_drift(case: str, llama_root: Path) -> list[str]:
    if case == "undeclared-resource":
        _write_text(llama_root, "undeclared.metallib", "unproven\n")
    elif case == "undeclared-directory":
        (llama_root / "undeclared-directory").mkdir()
    elif case == "unsafe-symlink":
        (llama_root / "lib/libescape.dylib").symlink_to("../../outside.dylib")
    elif case == "broken-symlink":
        (llama_root / "lib/libbroken.dylib").symlink_to("missing.dylib")
    elif case == "symlink-to-non-dylib":
        _write_text(llama_root, "lib/payload.bin", "not a dylib\n")
        (llama_root / "lib/aaa.dylib").symlink_to("payload.bin")
    elif case == "unsafe-resource-path":
        return ["../ggml-metal.metal"]
    elif case == "duplicate-resource":
        return ["share/resource.metallib", "share/resource.metallib"]
    elif case == "resource-in-lib":
        return ["lib/libengine.1.dylib"]
    elif case == "missing-library-dir":
        (llama_root / "lib").rename(llama_root / "moved-lib")
    elif case == "non-dylib-library":
        _write_text(llama_root, "lib/README", "not a dylib\n")
    elif case == "no-physical-library":
        for path in sorted(
            (llama_root / "lib").rglob("*"),
            key=lambda entry: len(entry.parts),
            reverse=True,
        ):
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
    elif case == "duplicate-basename":
        original = llama_root / "lib/libdependency.1.dylib"
        duplicate = llama_root / "lib/nested/libdependency.1.dylib"
        _write_library(duplicate, _read_library(original))
    elif case == "resource-symlink":
        (llama_root / "share").mkdir()
        (llama_root / "share/resource.metallib").symlink_to("missing-resource")
        return ["share/resource.metallib"]
    elif case == "unsupported-entry":
        os.mkfifo(llama_root / "lib/runtime-pipe.dylib")
    else:
        message = f"unknown inventory drift case: {case}"
        raise AssertionError(message)
    return []


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("schema", "provenance schema"),
        ("mesh-identity", "identity does not match"),
        ("llama-pin", "pin does not match"),
        ("upstream-source", "upstream pin is not one exact line"),
        ("patch-directory", "no regular llama.cpp patch directory"),
        ("empty-patches", "contains no llama.cpp patches"),
        ("patch-entry", "patch inventory contains an unsupported entry"),
        ("source-hash", "packaging input digest"),
        ("patch-inventory", "patch inventory"),
        ("patch-schema", "patch record has an unexpected schema"),
        ("patch-digest", "patch digest does not match"),
        ("packaging-inventory", "packaging input inventory is not exact"),
        ("packaging-schema", "packaging input record has an unexpected schema"),
        ("digest-schema", "not a lowercase SHA-256 digest"),
        ("missing-source", "packaging input is not a regular file"),
        ("invalid-json", "invalid Mesh provenance JSON"),
        ("missing-provenance", "Mesh provenance is not a regular file"),
    ],
)
def test_bundle_revalidates_every_mesh_provenance_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bundle_program: Path,
    fake_macho_tools: dict[str, Path],
    case: str,
    message: str,
) -> None:
    """Every claimed Mesh source identity and digest is checked against bytes."""
    mesh_source, mesh_provenance = _write_mesh_fixture(tmp_path)
    llama_root, _dependency, _primary = _write_llama_fixture(tmp_path)
    _apply_provenance_drift(case, mesh_source, mesh_provenance)

    with pytest.raises(SystemExit, match=message):
        _run_bundle(
            monkeypatch,
            bundle_program,
            mesh_source,
            mesh_provenance,
            llama_root,
            tmp_path / "output",
            fake_macho_tools,
        )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("undeclared-resource", "undeclared llama.cpp output"),
        ("undeclared-directory", "undeclared llama.cpp output directory"),
        ("unsafe-symlink", "unsafe library symlink"),
        ("broken-symlink", "unsafe library symlink"),
        ("symlink-to-non-dylib", "unsafe library symlink"),
        ("unsafe-resource-path", "not a normalized relative path"),
        ("duplicate-resource", "must be sorted and unique"),
        ("resource-in-lib", "must be outside the library closure"),
        ("missing-library-dir", "no regular library directory"),
        ("non-dylib-library", "non-dylib file"),
        ("no-physical-library", "no physical dynamic libraries"),
        ("duplicate-basename", "duplicate physical dylib basenames"),
        ("resource-symlink", "undeclared llama.cpp output"),
        ("unsupported-entry", "unsupported llama.cpp output entry"),
    ],
)
def test_bundle_rejects_undeclared_or_unsafe_llama_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bundle_program: Path,
    fake_macho_tools: dict[str, Path],
    case: str,
    message: str,
) -> None:
    """The complete llama closure is libraries plus only named regular resources."""
    mesh_source, mesh_provenance = _write_mesh_fixture(tmp_path)
    llama_root, _dependency, _primary = _write_llama_fixture(tmp_path)
    resource_subpaths = _apply_inventory_drift(case, llama_root)

    with pytest.raises(SystemExit, match=message):
        _run_bundle(
            monkeypatch,
            bundle_program,
            mesh_source,
            mesh_provenance,
            llama_root,
            tmp_path / "output",
            fake_macho_tools,
            resource_subpaths=resource_subpaths,
        )


def test_embedded_command_boundary_rejects_malformed_arguments(
    tmp_path: Path,
    embedded_program: dict[str, object],
) -> None:
    """The standalone builder rejects malformed argv and JSON before filesystem work."""
    main = _embedded_function(embedded_program, "main")
    with pytest.raises(SystemExit, match="exactly ten arguments"):
        main([])
    arguments = [str(tmp_path)] * 10
    arguments[5] = "{"
    with pytest.raises(SystemExit, match="invalid llama.cpp resourceSubpaths JSON"):
        main(arguments)


def test_embedded_inventory_boundary_rejects_wrong_layout_types(
    tmp_path: Path,
    embedded_program: dict[str, object],
) -> None:
    """Direct callers cannot replace either declared passthru layout shape."""
    llama_root, _dependency, _primary = _write_llama_fixture(tmp_path)
    validate = _embedded_function(embedded_program, "validate_llama_inventory")
    with pytest.raises(SystemExit, match="unexpected llama.cpp library subdirectory"):
        validate(llama_root, "libraries", [])
    with pytest.raises(SystemExit, match="resourceSubpaths must be a list"):
        validate(llama_root, "lib", {})


def test_embedded_manifest_rejects_an_empty_file_map(
    tmp_path: Path,
    embedded_program: dict[str, object],
) -> None:
    """The generated manifest cannot checksum itself or be the only output."""
    output = tmp_path / "empty-runtime"
    output.mkdir()
    (output / "manifest.json").write_text("{}\n", encoding="utf-8")
    collect = _embedded_function(embedded_program, "manifest_files")

    with pytest.raises(SystemExit, match="runtime files manifest is empty"):
        collect(output)


def test_embedded_manifest_rejects_a_library_without_a_digest(
    embedded_program: dict[str, object],
) -> None:
    """Every dependency-ordered loader entry must be checksum-covered."""
    validate = _embedded_function(embedded_program, "validate_manifest_files")

    with pytest.raises(
        SystemExit,
        match=r"runtime files manifest is missing libraries: lib/libprimary\.dylib",
    ):
        validate(
            {"lib/libdependency.dylib": "0" * 64},
            ["lib/libdependency.dylib", "lib/libprimary.dylib"],
        )


def test_bundle_refuses_to_merge_into_an_existing_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bundle_program: Path,
    fake_macho_tools: dict[str, Path],
) -> None:
    """The package builder must never merge with stale or caller-owned bytes."""
    mesh_source, mesh_provenance = _write_mesh_fixture(tmp_path)
    llama_root, _dependency, _primary = _write_llama_fixture(tmp_path)
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(SystemExit, match="runtime output already exists"):
        _run_bundle(
            monkeypatch,
            bundle_program,
            mesh_source,
            mesh_provenance,
            llama_root,
            output,
            fake_macho_tools,
        )


@pytest.mark.parametrize("value", [None, "", "/absolute", "a/../b", "a//b"])
def test_embedded_relative_path_validator_rejects_unsafe_values(
    embedded_program: dict[str, object],
    value: object,
) -> None:
    """Manifest and dependency paths must remain normalized and relative."""
    validate = _embedded_function(embedded_program, "safe_relative_path")
    with pytest.raises(SystemExit, match="relative path|normalized relative path"):
        validate(value, "fixture path")


def test_bundle_copies_and_rehashes_only_explicitly_declared_runtime_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bundle_program: Path,
    fake_macho_tools: dict[str, Path],
) -> None:
    """A proven resource seam may add bytes without broad directory discovery."""
    mesh_source, mesh_provenance = _write_mesh_fixture(tmp_path)
    llama_root, _dependency, _primary = _write_llama_fixture(tmp_path)
    resource = _write_text(
        llama_root,
        "share/llama.cpp/proven-resource.metallib",
        "authenticated resource bytes\n",
    )
    output = tmp_path / "output"

    _run_bundle(
        monkeypatch,
        bundle_program,
        mesh_source,
        mesh_provenance,
        llama_root,
        output,
        fake_macho_tools,
        resource_subpaths=["share/llama.cpp/proven-resource.metallib"],
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    copied = output / "share/llama.cpp/proven-resource.metallib"
    assert copied.read_bytes() == resource.read_bytes()
    assert _sha256(copied) == _sha256(resource)
    files = manifest["runtime"]["files"]
    assert files["share/llama.cpp/proven-resource.metallib"] == _sha256(resource)
    assert set(manifest["runtime"]["libraries"]) <= set(files)
    assert "manifest.json" not in files
