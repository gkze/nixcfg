"""Contracts for Buzz's validated desktop application candidate."""

import ctypes
import hashlib
import json
import os
import plistlib
import pwd
import runpy
import shlex
import subprocess
import sys
from collections.abc import Callable
from functools import cache
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock, call

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.import_utils import load_module_from_path
from lib.tests._assertions import expect_instance
from lib.tests._buzz_native_lock import (
    buzz_native_lock_string,
    render_buzz_native_lock_interpolations,
)
from lib.tests._macho import build_version, macho, string_command
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.scope import Scope

_CANDIDATE_PATH = REPO_ROOT / "packages/buzz/native/desktop-candidate.nix"
validate_entitlements = load_module_from_path(
    _CANDIDATE_PATH.with_name("validate_entitlements.py"), "buzz_validate_entitlements"
)
validate_rpaths = load_module_from_path(
    _CANDIDATE_PATH.with_name("validate_rpaths.py"), "buzz_validate_rpaths"
)
validate_runtime = load_module_from_path(
    _CANDIDATE_PATH.with_name("validate_runtime.py"), "buzz_validate_runtime"
)
validate_runtime_load = load_module_from_path(
    _CANDIDATE_PATH.with_name("validate_runtime_load.py"), "buzz_validate_runtime_load"
)
_LAUNCHER_PATH = REPO_ROOT / "packages/buzz/native/buzz-launcher.c"
_APP_EXECUTABLES = (
    "buzz-desktop",
    "buzz-desktop.real",
    "buzz-acp",
    "buzz-agent",
    "buzz-backend-kubernetes",
    "buzz-dev-mcp",
    "git-credential-nostr",
    "buzz",
)
_SIDECARS = _APP_EXECUTABLES[2:]
_RUNTIME_ID = "meshllm-native-runtime-darwin-aarch64-metal"
_BUZZ_VERSION = buzz_native_lock_string("buzz", "version")
_MESH_VERSION = buzz_native_lock_string("meshLlm", "version")
_SKIPPY_ABI = buzz_native_lock_string("meshLlm", "skippyAbi")
_REQUIRED_ENTITLEMENTS = {
    "com.apple.security.cs.disable-library-validation": True,
    "com.apple.security.device.audio-input": True,
    "com.apple.security.device.camera": True,
}


@cache
def _candidate_package() -> tuple[FunctionDefinition, FunctionCall]:
    package = expect_instance(
        parse_nix_expr(_CANDIDATE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        output = output.body
    return package, expect_instance(output, FunctionCall)


def _derivation_arguments() -> AttributeSet:
    _package, derivation = _candidate_package()
    return expect_instance(derivation.argument, AttributeSet)


def _assertion_conditions() -> list[object]:
    package, _derivation = _candidate_package()
    conditions: list[object] = []
    output = package.output
    while isinstance(output, Assertion):
        conditions.append(output.expression)
        output = output.body
    return conditions


def _scope_string(name: str) -> str:
    value = expect_instance(
        expect_binding(_candidate_scope(), name).value,
        IndentedString,
    )
    return render_buzz_native_lock_interpolations(
        dedent(indented_string_body(value.rebuild()))
    )


def _candidate_scope() -> Scope:
    package, derivation = _candidate_package()
    output = package.output
    while isinstance(output, Assertion):
        if output.scope:
            return output.scope
        output = output.body
    return derivation.scope


def _assembly_script() -> str:
    script = _scope_string("assemblyScript")
    return _expand_validation_commands(script)


def _install_check_script() -> str:
    script = _expand_validation_commands(_scope_string("installCheckPhase"))
    replacements = {
        "${python3}/bin/python3": '"$PYTHON_TOOL"',
        "${cctools}/bin/lipo": '"$LIPO_TOOL"',
        "${cctools}/bin/otool": '"$OTOOL_TOOL"',
        "/usr/bin/codesign": '"$CODESIGN_TOOL"',
        "/usr/bin/file": '"$FILE_TOOL"',
        "/usr/libexec/PlistBuddy": '"$PLISTBUDDY_TOOL"',
    }
    for original, replacement in replacements.items():
        script = script.replace(original, replacement)
    return "runHook() { :; }\n" + script


def _expand_validation_commands(script: str) -> str:
    if "${launcherSmokeScript}" in script:
        script = script.replace(
            "${launcherSmokeScript}",
            _scope_string("launcherSmokeScript"),
        )
    native = _CANDIDATE_PATH.parent
    commands = {
        "runtimeValidationCommand": [
            str(native / "validate_runtime.py"),
            _MESH_VERSION,
            _SKIPPY_ABI,
        ],
        "entitlementsValidationCommand": [str(native / "validate_entitlements.py")],
    }
    for name, arguments in commands.items():
        script = script.replace(
            "${" + name + "}", '"$PYTHON_TOOL" ' + shlex.join(arguments)
        )
    script = script.replace(
        "${rpathValidationCommand}", '"$PYTHON_TOOL" "$RPATH_VALIDATOR"'
    )
    return script.replace(
        "${runtimeLoadValidationCommand}", '"$RUNTIME_LOAD_VALIDATOR"'
    )


def _write_executable(path: Path, body: bytes = b"fixture\n") -> None:
    path.write_bytes(body)
    path.chmod(0o755)


def _write_tool(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _desktop_fixture(root: Path) -> Path:
    desktop = root / "desktop"
    app = desktop / "Applications/Buzz.app"
    macos = app / "Contents/MacOS"
    resources = app / "Contents/Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    for name in ("buzz-desktop", *_SIDECARS):
        _write_executable(macos / name, f"unsigned:{name}\n".encode())
    with (app / "Contents/Info.plist").open("wb") as plist_file:
        plistlib.dump(
            {
                "CFBundleExecutable": "buzz-desktop",
                "CFBundleIdentifier": "xyz.block.buzz.app",
                "CFBundleName": "Buzz",
                "CFBundleShortVersionString": _BUZZ_VERSION,
                "CFBundleVersion": _BUZZ_VERSION,
                "LSMinimumSystemVersion": "10.13",
            },
            plist_file,
        )
    return desktop


def _runtime_fixture(root: Path) -> Path:
    runtime = root / "mesh-runtime"
    library = runtime / "lib/libmesh.dylib"
    resource = runtime / "share/mesh-runtime.txt"
    library.parent.mkdir(parents=True)
    resource.parent.mkdir(parents=True)
    library.write_bytes(b"signed mesh library\n")
    resource.write_bytes(b"mesh resource\n")
    files = {
        "lib/libmesh.dylib": hashlib.sha256(library.read_bytes()).hexdigest(),
        "share/mesh-runtime.txt": hashlib.sha256(resource.read_bytes()).hexdigest(),
    }
    (runtime / "manifest.json").write_text(
        json.dumps(
            {
                "runtime": {
                    "id": _RUNTIME_ID,
                    "mesh_version": _MESH_VERSION,
                    "skippy_abi": _SKIPPY_ABI,
                    "platform": {
                        "os": "macos",
                        "arch": "aarch64",
                        "target": "aarch64-apple-darwin",
                    },
                    "backend": {"kind": "metal"},
                    "rank": 0,
                    "libraries": ["lib/libmesh.dylib"],
                    "files": files,
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return runtime


def _patched_source_fixture(
    root: Path,
    *,
    extra_entitlement: bool = False,
) -> Path:
    source = root / "patched-source"
    entitlements = source / "desktop/src-tauri/Entitlements.plist"
    entitlements.parent.mkdir(parents=True)
    entitlement_values = dict(_REQUIRED_ENTITLEMENTS)
    if extra_entitlement:
        entitlement_values["com.apple.security.network.client"] = True
    with entitlements.open("wb") as plist_file:
        plistlib.dump(entitlement_values, plist_file)
    return source


def _candidate_tools(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    tools = root / "tools"
    tools.mkdir()
    codesign = tools / "codesign"
    install_name_tool = tools / "install_name_tool"
    otool = tools / "otool"
    xattr = tools / "xattr"
    sign_log = root / "codesign.log"
    _write_tool(
        codesign,
        r"""printf '%s\n' "$*" >> "$SIGN_LOG"
target=
for argument in "$@"; do
  target="$argument"
done
case "${FAKE_CODESIGN_MUTATE_RUNTIME:-0}:$target" in
  1:*.app)
    printf 'mutated after signing\n' >> \
      "$target/Contents/Resources/mesh-runtime/lib/libmesh.dylib"
    ;;
esac""",
    )
    _write_tool(
        install_name_tool,
        'printf \'%s\\n\' "$*" >> "$INSTALL_NAME_TOOL_LOG"',
    )
    _write_tool(
        otool,
        r"""test "$1" = -L
printf '%s:\n' "$2"
printf '\t%s\n' \
  '/nix/store/00000000000000000000000000000000-libiconv-115.100.1/lib/libiconv.2.dylib (compatibility version 7.0.0, current version 7.0.0)' """,
    )
    _write_tool(xattr, 'printf \'%s\\n\' "$*" >> "$XATTR_LOG"')
    return codesign, install_name_tool, otool, xattr, sign_log


def _install_check_tools(
    root: Path,
    *,
    extra_dumped_entitlement: bool = False,
    fail_inventory_find: bool = False,
    invalid_macho: bool = False,
    fail_runtime_find: bool = False,
    macho_case: str | None = None,
) -> dict[str, str]:
    tools = root / "install-check-tools"
    tools.mkdir()
    file_tool = tools / "file"
    lipo_tool = tools / "lipo"
    plistbuddy_tool = tools / "PlistBuddy"
    codesign_tool = tools / "codesign"
    runtime_load_validator = tools / "runtime-load-validator"
    _write_tool(file_tool, "printf '%s\\n' 'Mach-O 64-bit executable arm64'")
    macho_key = macho_case or ""
    architectures = "arm64 x86_64" if macho_key == "universal" else "arm64"
    _write_tool(lipo_tool, f"printf '%s\\n' {shlex.quote(architectures)}")
    dependencies: dict[str, str] = {
        "dependency-traversal": "@loader_path/../../../../nix/store/libbad.dylib",
        "resolved-loader": "@loader_path/buzz-acp",
        "resolved-rpath": "@rpath/buzz-acp",
        "unresolved-rpath": "@rpath/libmissing.dylib",
    }
    dependency = dependencies.get(macho_key, "/usr/lib/libSystem.B.dylib")
    version_cases: dict[str, list[str]] = {
        "minos-newer": ["14.1"],
        "minos-missing": [],
        "minos-ambiguous": ["14.0", "13.0"],
    }
    minimum_versions = version_cases.get(macho_key, ["14.0"])
    platform = "2" if macho_key == "wrong-platform" else "1"
    rpath_cases: dict[str, str] = {
        "rpath-absolute": "/nix/store/unsafe/lib",
        "rpath-traversal": "@loader_path/../../../../nix/store/unsafe/lib",
        "resolved-rpath": "@loader_path",
    }
    rpath = rpath_cases.get(macho_key)
    commands = [
        build_version(
            int(value.split(".")[0]) << 16 | int(value.split(".")[1]) << 8,
            int(platform),
        )
        for value in minimum_versions
    ]
    commands.append(string_command(0xC, dependency))
    if rpath is not None:
        commands.append(string_command(0x8000001C, rpath))
    binary = tools / "inspection.macho"
    binary.write_bytes(b"invalid" if invalid_macho else macho(commands))
    validator = tools / "validate-rpaths.py"
    # Shell launcher fixtures remain executable scripts; only their native
    # metadata source is substituted. The parser consumes real Mach-O bytes.
    validator.write_text(
        "import sys, runpy\nfrom pathlib import Path\nimport lib.macho\n"
        f"metadata = lib.macho.read_macho(Path({str(binary)!r}))\n"
        "lib.macho.read_macho = lambda path: metadata\n"
        f"runpy.run_path({str(_CANDIDATE_PATH.with_name('validate_rpaths.py'))!r}, run_name='__main__')\n"
    )

    plistbuddy_tool.write_text(
        f"""#!{sys.executable}
import plistlib
import sys

command = sys.argv[sys.argv.index("-c") + 1]
key = command.removeprefix("Print :")
with open(sys.argv[-1], "rb") as plist_file:
    value = plistlib.load(plist_file)[key]
print(value)
""",
        encoding="utf-8",
    )
    plistbuddy_tool.chmod(0o755)

    dumped_entitlements = dict(_REQUIRED_ENTITLEMENTS)
    if extra_dumped_entitlement:
        dumped_entitlements["com.apple.security.network.client"] = True
    codesign_tool.write_text(
        f"""#!{sys.executable}
import plistlib
import sys

arguments = sys.argv[1:]
if "--entitlements" in arguments and "-d" in arguments:
    sys.stdout.buffer.write(plistlib.dumps({dumped_entitlements!r}))
elif "-dv" in arguments:
    print("Signature=adhoc", file=sys.stderr)
""",
        encoding="utf-8",
    )
    codesign_tool.chmod(0o755)
    _write_tool(runtime_load_validator, ":")

    path = os.environ["PATH"]
    if fail_inventory_find or fail_runtime_find:
        find_tool = tools / "find"
        _write_tool(
            find_tool,
            """case "$*" in
  *"-exec basename"*)
    if [ "$FAIL_INVENTORY_FIND" != 1 ]; then
      exec /usr/bin/find "$@"
    fi
    printf '%s\\n' buzz buzz-acp buzz-agent buzz-backend-kubernetes \\
      buzz-desktop buzz-desktop.real buzz-dev-mcp git-credential-nostr
    exit 48
    ;;
  *"-name *.dylib"*)
    if [ "$FAIL_RUNTIME_FIND" = 1 ]; then
      printf '%s\\n' "$1/libmesh.dylib"
      exit 50
    fi
    ;;
esac
exec /usr/bin/find "$@"
""",
        )
        path = f"{tools}:{path}"

    return {
        "CODESIGN_TOOL": str(codesign_tool),
        "FAIL_INVENTORY_FIND": "1" if fail_inventory_find else "0",
        "FAIL_RUNTIME_FIND": "1" if fail_runtime_find else "0",
        "FILE_TOOL": str(file_tool),
        "LIPO_TOOL": str(lipo_tool),
        "PATH": path,
        "PLISTBUDDY_TOOL": str(plistbuddy_tool),
        "PYTHON_TOOL": sys.executable,
        "RUNTIME_LOAD_VALIDATOR": str(runtime_load_validator),
        "RPATH_VALIDATOR": str(validator),
        "PYTHONPATH": str(REPO_ROOT),
    }


def _run_assembly(
    tmp_path: Path,
    *,
    extra_source_entitlement: bool = False,
    fail_inventory_find: bool = False,
    fail_unsupported_find: bool = False,
    manifest_mutator: Callable[[dict[str, object]], None] | None = None,
    mutate_runtime_while_signing: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    desktop = _desktop_fixture(tmp_path)
    runtime = _runtime_fixture(tmp_path)
    if manifest_mutator is not None:
        manifest_path = runtime / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_mutator(manifest)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    patched_source = _patched_source_fixture(
        tmp_path,
        extra_entitlement=extra_source_entitlement,
    )
    launcher = tmp_path / "buzz-launcher"
    _write_executable(launcher, b"native launcher\n")
    codesign, install_name_tool, otool, xattr, sign_log = _candidate_tools(tmp_path)
    output = tmp_path / "result"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    environment = os.environ | {
        "BUZZ_LAUNCHER": str(launcher),
        "CODESIGN_TOOL": str(codesign),
        "DESKTOP_UNSIGNED": str(desktop),
        "FAKE_CODESIGN_MUTATE_RUNTIME": ("1" if mutate_runtime_while_signing else "0"),
        "INSTALL_NAME_TOOL": str(install_name_tool),
        "INSTALL_NAME_TOOL_LOG": str(tmp_path / "install-name-tool.log"),
        "MESH_RUNTIME_BUNDLE": str(runtime),
        "OTOOL_TOOL": str(otool),
        "PATCHED_BUZZ_SOURCE": str(patched_source),
        "PLISTBUDDY_TOOL": "/usr/libexec/PlistBuddy",
        "PYTHON_TOOL": sys.executable,
        "SIGN_LOG": str(sign_log),
        "TMPDIR": str(scratch),
        "XATTR_LOG": str(tmp_path / "xattr.log"),
        "XATTR_TOOL": str(xattr),
        "out": str(output),
    }
    if fail_inventory_find or fail_unsupported_find:
        find_tool = codesign.parent / "find"
        _write_tool(
            find_tool,
            """case "$*" in
  *"! -type f"*)
    if [ "$FAIL_UNSUPPORTED_FIND" = 1 ]; then
      exit 51
    fi
    ;;
  *"-exec basename"*)
    if [ "$FAIL_INVENTORY_FIND" != 1 ]; then
      exec /usr/bin/find "$@"
    fi
    printf '%s\\n' buzz buzz-acp buzz-agent buzz-backend-kubernetes \\
      buzz-desktop buzz-desktop.real buzz-dev-mcp git-credential-nostr
    exit 48
    ;;
esac
exec /usr/bin/find "$@"
""",
        )
        environment["FAIL_INVENTORY_FIND"] = "1" if fail_inventory_find else "0"
        environment["FAIL_UNSUPPORTED_FIND"] = "1" if fail_unsupported_find else "0"
        environment["PATH"] = f"{codesign.parent}:{environment['PATH']}"
    result = subprocess.run(
        ["/bin/bash", "-eu"],
        input=_assembly_script(),
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    return result, output, runtime, sign_log


def _run_install_check(
    tmp_path: Path,
    output: Path,
    *,
    broken_launcher: bool = False,
    extra_dumped_entitlement: bool = False,
    fail_inventory_find: bool = False,
    invalid_macho: bool = False,
    fail_runtime_find: bool = False,
    macho_case: str | None = None,
) -> subprocess.CompletedProcess[str]:
    launcher = output / "Applications/Buzz.app/Contents/MacOS/buzz-desktop"
    launcher.write_text(
        (
            "#!/bin/sh\nexit 72\n"
            if broken_launcher
            else """#!/bin/sh
set -eu
macos=${0%/*}
contents=${macos%/*}
export MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR="$contents/Resources/mesh-runtime"
export MESH_LLM_NATIVE_RUNTIME_CACHE_DIR="$HOME/Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes"
unset MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL
exec "$macos/buzz-desktop.real" "$@"
"""
        ),
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    tools = _install_check_tools(
        tmp_path,
        extra_dumped_entitlement=extra_dumped_entitlement,
        fail_inventory_find=fail_inventory_find,
        invalid_macho=invalid_macho,
        fail_runtime_find=fail_runtime_find,
        macho_case=macho_case,
    )
    scratch = tmp_path / "install-check-scratch"
    scratch.mkdir()
    environment = (
        os.environ
        | tools
        | {
            "TMPDIR": str(scratch),
            "out": str(output),
        }
    )
    return subprocess.run(
        ["/bin/bash", "-eu"],
        input=_install_check_script(),
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def _compile_launcher(app: Path) -> Path:
    launcher = app / "Contents/MacOS/buzz-desktop"
    launcher.parent.mkdir(parents=True)
    result = subprocess.run(  # noqa: S603 -- Compiles the repository-owned fixture.
        [
            "/usr/bin/clang",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(_LAUNCHER_PATH),
            "-o",
            str(launcher),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return launcher


def test_candidate_has_narrow_provenance_checked_interface() -> None:
    """Assembly must consume three attested artifacts and expose the validated app."""
    package, derivation = _candidate_package()
    assert {
        expect_instance(argument, Identifier).name for argument in package.argument_set
    } == {
        "cctools",
        "desktopUnsigned",
        "lib",
        "meshRuntimeBundle",
        "nativeLock",
        "patchedBuzzSource",
        "python3",
        "stdenv",
        "version",
    }
    assert_nix_ast_equal(derivation.name, "stdenv.mkDerivation")
    assert len(_assertion_conditions()) == 14
    for actual, expected in zip(
        _assertion_conditions(),
        (
            'stdenv.hostPlatform.system == "aarch64-darwin"',
            "builtins.isString buzzVersion",
            'builtins.isString buzzCommit && builtins.match "[0-9a-f]{40}" buzzCommit != null',
            "builtins.isString rustVersion",
            "builtins.isString pnpmVersion",
            "builtins.isString sherpaVersion",
            "builtins.isString meshLlmVersion",
            'builtins.isString skippyAbi && builtins.match "[0-9]+\\\\.[0-9]+\\\\.[0-9]+" skippyAbi != null',
            "version == buzzVersion",
            "(desktopUnsigned.passthru.buzzNativeContract or null) == expectedDesktopContract",
            "(meshRuntimeBundle.passthru.buzzNativeContract or null) == expectedRuntimeContract",
            '(meshRuntimeBundle.passthru.manifestSubpath or null) == "manifest.json"',
            '(meshRuntimeBundle.passthru.runtimeId or null) == "meshllm-native-runtime-darwin-aarch64-metal"',
            "(patchedBuzzSource.passthru.buzzNativeContract or null) == expectedSourceContract",
        ),
        strict=True,
    ):
        assert_nix_ast_equal(actual, expected)

    attrs = _derivation_arguments()
    passthru = expect_instance(
        expect_binding(attrs.values, "passthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "buzzNativeContract").value,
        "implementedContract",
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "macApp").value,
        """
        {
          bundleId = "xyz.block.buzz.app";
          bundleName = "Buzz.app";
          bundleRelPath = "Applications/Buzz.app";
          installMode = "copy";
        }
        """,
    )
    meta = expect_instance(expect_binding(attrs.values, "meta").value, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(meta.values, "description").value,
        '"Source-built Buzz desktop app with an embedded offline Mesh runtime"',
    )
    assert_nix_ast_equal(
        expect_binding(meta.values, "homepage").value,
        '"https://github.com/block/buzz"',
    )
    assert_nix_ast_equal(
        expect_binding(meta.values, "license").value,
        "lib.licenses.asl20",
    )


def test_candidate_rejects_every_reference_from_the_final_output() -> None:
    """The final application bundle must not retain any Nix store reference."""
    attrs = _derivation_arguments()
    assert_nix_ast_equal(
        expect_binding(attrs.values, "__structuredAttrs").value,
        "true",
    )
    output_checks = expect_instance(
        expect_binding(attrs.values, "outputChecks").value,
        AttributeSet,
    )
    final_output = expect_instance(
        expect_binding(output_checks.values, "out").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(final_output.values, "allowedReferences").value,
        "[ ]",
    )


def test_candidate_contract_records_finder_runtime_and_signing_policy() -> None:
    """The public candidate metadata must state every launch-time invariant."""
    assert_nix_ast_equal(
        expect_binding(_candidate_scope(), "implementedContract").value,
        """{
          kind = "buzz-desktop-candidate";
          commit = buzzCommit;
          version = buzzVersion;
          target = "aarch64-apple-darwin";
          minimumMacosVersion = "14.0";
          app = {
            bundleName = "Buzz.app";
            identifier = "xyz.block.buzz.app";
            launcherExecutable = "buzz-desktop";
            payloadExecutable = "buzz-desktop.real";
            sidecars = [
              "buzz-acp"
              "buzz-agent"
              "buzz-backend-kubernetes"
              "buzz-dev-mcp"
              "git-credential-nostr"
              "buzz"
            ];
          };
          launcher = {
            language = "c11";
            source = "buzz-launcher.c";
            handoff = "execv";
            runtimeBundleSubpath = "Contents/Resources/mesh-runtime";
            runtimeCacheSubpath = "Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes";
            runtimeBundleEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
            runtimeCacheEnvironment = "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR";
            manifestUrlEnvironment = "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";
            manifestUrlUnset = true;
            createsCacheDirectory = false;
            installCheckSmoke = true;
          };
          signing = {
            identity = "adhoc";
            deepSign = false;
            runtimeResigned = false;
            entitlementsSource = "patched-buzz-source";
          };
          appSigned = true;
          runtimeBundleEmbedded = true;
          exportReady = true;
        }""",
    )


def test_candidate_compiles_launcher_then_disables_all_generic_fixup() -> None:
    """No generic phase may mutate the manifest-covered runtime after copying."""
    attrs = _derivation_arguments()
    for name, expected in {
        "strictDeps": "true",
        "dontUnpack": "true",
        "dontConfigure": "true",
        "dontFixup": "true",
        "doInstallCheck": "true",
    }.items():
        assert_nix_ast_equal(expect_binding(attrs.values, name).value, expected)
    assert_nix_ast_equal(
        expect_binding(attrs.values, "nativeBuildInputs").value,
        "[ cctools python3 ]",
    )

    build = parse_shell(_scope_string("buildPhase"))
    compiler_commands = command_texts(build, '"$CC"')
    assert len(compiler_commands) == 1
    assert "-std=c11" in compiler_commands[0]
    assert "-Werror" in compiler_commands[0]
    assert "__NIX_INTERP__" in compiler_commands[0]

    assembly = parse_shell(_assembly_script())
    signing_commands = command_texts(assembly, '"$CODESIGN_TOOL"')
    assert len(signing_commands) == 9
    assert all("--deep" not in command for command in signing_commands)
    assert command_texts(assembly, '"$XATTR_TOOL"') == ['"$XATTR_TOOL" -cr "$app"']


@pytest.mark.parametrize(
    ("command_name", "invocation"),
    [
        (
            "runtimeValidationCommand",
            "${./validate_runtime.py} ${lib.escapeShellArg meshLlmVersion} ${lib.escapeShellArg skippyAbi}",
        ),
        (
            "runtimeLoadValidationCommand",
            "${./validate_runtime_load.py} ${lib.escapeShellArg skippyAbi}",
        ),
        ("entitlementsValidationCommand", "${./validate_entitlements.py}"),
        ("rpathValidationCommand", "${./validate_rpaths.py}"),
    ],
)
def test_validator_command_prefix_keeps_call_site_arguments_on_same_command(
    command_name: str,
    invocation: str,
) -> None:
    """Nix must call the standalone validator with its pinned arguments."""
    command = expect_instance(
        expect_binding(_candidate_scope(), command_name).value,
        StringPrimitive,
    )
    prefix = (
        "PYTHONPATH=${inspectionSource} ${inspectionPython}/bin/python3 "
        if command_name == "rpathValidationCommand"
        else '\\"$PYTHON_TOOL\\" '
    )
    assert command.value == prefix + invocation


def test_assembly_embeds_runtime_last_and_signs_only_mutable_app_code(
    tmp_path: Path,
) -> None:
    """The app copy must get a launcher, immutable runtime, and ordered signatures."""
    result, output, runtime_source, sign_log = _run_assembly(tmp_path)
    assert result.returncode == 0, result.stderr
    app = output / "Applications/Buzz.app"
    macos = app / "Contents/MacOS"
    assert sorted(path.name for path in macos.iterdir()) == sorted(_APP_EXECUTABLES)
    assert (macos / "buzz-desktop").read_bytes() == b"native launcher\n"
    assert (macos / "buzz-desktop.real").read_bytes() == b"unsigned:buzz-desktop\n"
    with (app / "Contents/Info.plist").open("rb") as plist_file:
        assert plistlib.load(plist_file)["LSMinimumSystemVersion"] == "14.0"

    embedded = app / "Contents/Resources/mesh-runtime"
    assert (embedded / "manifest.json").read_bytes() == (
        runtime_source / "manifest.json"
    ).read_bytes()
    assert (embedded / "lib/libmesh.dylib").read_bytes() == (
        runtime_source / "lib/libmesh.dylib"
    ).read_bytes()

    signing = sign_log.read_text(encoding="utf-8").splitlines()
    assert len(signing) == 9
    assert signing[0].endswith("Contents/MacOS/buzz-desktop.real")
    assert [Path(line.rsplit(" ", 1)[1]).name for line in signing[1:7]] == list(
        _SIDECARS
    )
    assert signing[7].endswith("Contents/MacOS/buzz-desktop")
    assert signing[8].endswith("Applications/Buzz.app")
    assert all("--deep" not in line for line in signing)
    assert all("mesh-runtime" not in line for line in signing)


def test_assembly_relocates_the_payload_sdk_iconv_edge_before_signing(
    tmp_path: Path,
) -> None:
    """The app payload must use macOS's ABI-compatible system libiconv."""
    result, output, _runtime, _sign_log = _run_assembly(tmp_path)
    assert result.returncode == 0, result.stderr
    payload = output / "Applications/Buzz.app/Contents/MacOS/buzz-desktop.real"
    assert (tmp_path / "install-name-tool.log").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "-change "
        "/nix/store/00000000000000000000000000000000-libiconv-115.100.1/"
        "lib/libiconv.2.dylib /usr/lib/libiconv.2.dylib "
        f"{payload}"
    ]


def test_assembly_revalidates_runtime_after_outer_app_signing(tmp_path: Path) -> None:
    """Any signing-time mutation of a manifest-covered byte must fail the build."""
    result, _output, _runtime, _sign_log = _run_assembly(
        tmp_path,
        mutate_runtime_while_signing=True,
    )
    assert result.returncode != 0
    assert "runtime digest mismatch" in result.stderr


def test_assembly_rejects_extra_source_entitlement_before_signing(
    tmp_path: Path,
) -> None:
    """The reviewed three-key source plist is an exact signing allowlist."""
    result, _output, _runtime, sign_log = _run_assembly(
        tmp_path,
        extra_source_entitlement=True,
    )
    assert result.returncode != 0
    assert "source entitlement contract differs" in result.stderr
    assert not sign_log.exists()


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("runtime-id", "runtime.id differs"),
        ("mesh-version", "runtime.mesh_version differs"),
        ("skippy-abi", "runtime.skippy_abi differs"),
        ("platform", "runtime.platform differs"),
        ("backend", "runtime.backend differs"),
        ("rank", "runtime.rank differs"),
        ("rank-type", "runtime.rank differs"),
        ("top-level-key", "manifest top-level schema differs"),
        ("runtime-key", "runtime schema differs"),
        ("empty-libraries", "runtime.libraries is not a nonempty string list"),
        ("duplicate-libraries", "runtime.libraries contains duplicates"),
    ],
)
def test_assembly_rejects_runtime_manifest_contract_drift(
    tmp_path: Path,
    case: str,
    expected_error: str,
) -> None:
    """Assembly must independently attest loader identity and compatibility."""

    def mutate(manifest: dict[str, object]) -> None:
        runtime: dict[str, object] = expect_instance(manifest["runtime"], dict)
        if case == "runtime-id":
            runtime["id"] = "unreviewed-runtime"
        elif case == "mesh-version":
            runtime["mesh_version"] = "999.0.0"
        elif case == "skippy-abi":
            runtime["skippy_abi"] = "999.0.0"
        elif case == "platform":
            runtime["platform"] = {
                "os": "macos",
                "arch": "x86_64",
                "target": "x86_64-apple-darwin",
            }
        elif case == "backend":
            runtime["backend"] = {"kind": "cpu"}
        elif case == "rank":
            runtime["rank"] = 1
        elif case == "rank-type":
            runtime["rank"] = False
        elif case == "top-level-key":
            manifest["download"] = {"url": "https://example.invalid/runtime"}
        elif case == "runtime-key":
            runtime["download_url"] = "https://example.invalid/runtime"
        elif case == "empty-libraries":
            runtime["libraries"] = []
        else:
            runtime["libraries"] = [
                "lib/libmesh.dylib",
                "lib/libmesh.dylib",
            ]

    result, _output, _runtime, _sign_log = _run_assembly(
        tmp_path,
        manifest_mutator=mutate,
    )
    assert result.returncode != 0
    assert expected_error in result.stderr


def test_assembly_propagates_inventory_enumerator_failure(tmp_path: Path) -> None:
    """A valid-looking partial find stream must not satisfy exact inventory."""
    result, _output, _runtime, _sign_log = _run_assembly(
        tmp_path,
        fail_inventory_find=True,
    )
    assert result.returncode != 0
    assert "failed to enumerate MacOS inventory" in result.stderr


def test_assembly_propagates_unsupported_entry_enumerator_failure(
    tmp_path: Path,
) -> None:
    """A failed non-file scan must not be mistaken for an empty result."""
    result, _output, _runtime, _sign_log = _run_assembly(
        tmp_path,
        fail_unsupported_find=True,
    )
    assert result.returncode != 0
    assert "failed to inspect unsupported MacOS entries" in result.stderr


def test_install_check_accepts_exact_candidate(tmp_path: Path) -> None:
    """The install audit accepts the exact assembled candidate contract."""
    assembly, output, _runtime, _sign_log = _run_assembly(tmp_path)
    assert assembly.returncode == 0, assembly.stderr
    result = _run_install_check(tmp_path, output)
    assert result.returncode == 0, result.stderr
    smoke_root = tmp_path / "install-check-scratch/buzz-candidate-launcher-smoke"
    assert (smoke_root / "record").read_text(encoding="utf-8").splitlines() == [
        str(smoke_root / "Buzz Smoke.app/Contents/Resources/mesh-runtime"),
        str(
            smoke_root
            / "home/Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes"
        ),
        "probe argument",
    ]
    assert not (smoke_root / "home/Library").exists()


def test_install_check_rejects_a_launcher_that_cannot_start(tmp_path: Path) -> None:
    """The install audit must execute the exact packaged launcher."""
    assembly, output, _runtime, _sign_log = _run_assembly(tmp_path)
    assert assembly.returncode == 0, assembly.stderr

    result = _run_install_check(tmp_path, output, broken_launcher=True)

    assert result.returncode == 72


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach-O dlopen is Darwin-only")
@pytest.mark.parametrize(("abi_patch", "accepted"), [(35, True), (36, False)])
def test_runtime_load_validator_dlopens_the_manifest_and_attests_skippy_abi(
    tmp_path: Path,
    abi_patch: int,
    accepted: bool,
) -> None:
    """AST checks cannot prove that the signed Mach-O runtime really loads."""
    runtime = _runtime_fixture(tmp_path)
    library = runtime / "lib/libmesh.dylib"
    source = tmp_path / "runtime.c"
    source.write_text(
        f"""#include <stdint.h>
struct AbiVersion {{ uint32_t major; uint32_t minor; uint32_t patch; }};
__attribute__((visibility("default")))
struct AbiVersion skippy_abi_version(void) {{
  return (struct AbiVersion){{0, 1, {abi_patch}}};
}}
""",
        encoding="utf-8",
    )
    compiled = subprocess.run(  # noqa: S603 -- Compiles the owned fixture.
        [
            "/usr/bin/clang",
            "-dynamiclib",
            "-mmacosx-version-min=14.0",
            "-Wl,-install_name,@rpath/libmesh.dylib",
            str(source),
            "-o",
            str(library),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr

    manifest_path = runtime / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime_manifest: dict[str, object] = expect_instance(manifest["runtime"], dict)
    files: dict[str, str] = expect_instance(runtime_manifest["files"], dict)
    files["lib/libmesh.dylib"] = hashlib.sha256(library.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(  # noqa: S603 -- Executes the repository-owned validator.
        [
            sys.executable,
            str(_CANDIDATE_PATH.parent / "validate_runtime_load.py"),
            _SKIPPY_ABI,
            str(runtime),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted, result.stderr
    if not accepted:
        assert f"Skippy ABI differs from {_SKIPPY_ABI}" in result.stderr

    assert command_texts(
        parse_shell(_install_check_script()), '"$RUNTIME_LOAD_VALIDATOR"'
    ) == ['"$RUNTIME_LOAD_VALIDATOR" "$runtime"']


@pytest.mark.parametrize("macho_case", ["resolved-loader", "resolved-rpath"])
def test_install_check_accepts_resolved_app_local_dynamic_edges(
    tmp_path: Path,
    macho_case: str,
) -> None:
    """Loader-relative and rpath edges pass only when their app target exists."""
    assembly, output, _runtime, _sign_log = _run_assembly(tmp_path)
    assert assembly.returncode == 0, assembly.stderr
    result = _run_install_check(tmp_path, output, macho_case=macho_case)
    assert result.returncode == 0, result.stderr


def test_install_check_rejects_non_file_macos_entry(tmp_path: Path) -> None:
    """Final inventory rejects a directory or symlink hidden from file-only find."""
    assembly, output, _runtime, _sign_log = _run_assembly(tmp_path)
    assert assembly.returncode == 0, assembly.stderr
    app = output / "Applications/Buzz.app"
    (app / "Contents/MacOS/unreviewed").mkdir()
    result = _run_install_check(tmp_path, output)
    assert result.returncode != 0
    assert "contains a non-file MacOS entry" in result.stderr


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CFBundleName", "Unreviewed Buzz"),
        ("CFBundleVersion", f"{_BUZZ_VERSION}-unreviewed"),
    ],
)
def test_install_check_rejects_app_identity_drift(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    """Both display identity and build version are candidate invariants."""
    assembly, output, _runtime, _sign_log = _run_assembly(tmp_path)
    assert assembly.returncode == 0, assembly.stderr
    info_plist = output / "Applications/Buzz.app/Contents/Info.plist"
    with info_plist.open("rb") as plist_file:
        info = plistlib.load(plist_file)
    info[key] = value
    with info_plist.open("wb") as plist_file:
        plistlib.dump(info, plist_file)
    result = _run_install_check(tmp_path, output)
    assert result.returncode != 0


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("malformed-macho", "invalid Mach-O"),
        ("rpath-absolute", "forbidden LC_RPATH"),
        ("rpath-traversal", "LC_RPATH escapes Buzz.app"),
        ("dependency-traversal", "dynamic-library edge escapes Buzz.app"),
        ("unresolved-rpath", "unresolved @rpath dynamic-library edge"),
        ("universal", "architectures differ"),
        ("minos-newer", "requires macOS newer than 14.0"),
        ("minos-missing", "has no unique macOS deployment target"),
        ("minos-ambiguous", "has no unique macOS deployment target"),
        ("wrong-platform", "is not a macOS executable"),
        ("entitlements", "final entitlement contract differs"),
        ("inventory-find", "failed to enumerate MacOS inventory"),
        ("runtime-find", "failed to enumerate runtime dylibs"),
    ],
)
def test_install_check_rejects_failed_or_ambiguous_audit(
    tmp_path: Path,
    failure: str,
    expected_error: str,
) -> None:
    """Audit-tool failure and extra privileges must close the candidate gate."""
    assembly, output, _runtime, _sign_log = _run_assembly(tmp_path)
    assert assembly.returncode == 0, assembly.stderr
    result = _run_install_check(
        tmp_path,
        output,
        extra_dumped_entitlement=failure == "entitlements",
        fail_inventory_find=failure == "inventory-find",
        invalid_macho=failure == "malformed-macho",
        fail_runtime_find=failure == "runtime-find",
        macho_case=failure,
    )
    assert result.returncode != 0
    assert expected_error in result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="Buzz launcher is Darwin-only")
def test_launcher_overrides_hostile_environment_and_execs_payload(
    tmp_path: Path,
) -> None:
    """A Finder-style launch must derive paths from the app, not its parent env."""
    app = tmp_path / "Fake Buzz.app"
    launcher = _compile_launcher(app)
    runtime = app / "Contents/Resources/mesh-runtime"
    runtime.mkdir(parents=True)
    (runtime / "manifest.json").write_text("{}\n", encoding="utf-8")

    record = tmp_path / "launch-record"
    payload = app / "Contents/MacOS/buzz-desktop.real"
    payload.write_text(
        """#!/bin/sh
set -eu
{
  printf 'bundle=%s\\n' "$MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR"
  printf 'cache=%s\\n' "$MESH_LLM_NATIVE_RUNTIME_CACHE_DIR"
  if [ "${MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL+set}" = set ]; then
    printf 'manifest=set\\n'
  else
    printf 'manifest=unset\\n'
  fi
  for argument in "$@"; do
    printf 'arg=%s\\n' "$argument"
  done
} > "$BUZZ_LAUNCHER_TEST_RECORD"
""",
        encoding="utf-8",
    )
    payload.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ | {
        "BUZZ_LAUNCHER_TEST_RECORD": str(record),
        "HOME": str(home),
        "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR": "/hostile/bundle",
        "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR": "relative-cache",
        "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL": "https://example.invalid/runtime.json",
    }
    result = subprocess.run(  # noqa: S603 -- Executes the compiled fixture.
        [str(launcher), "--probe", "two words"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert record.read_text(encoding="utf-8").splitlines() == [
        f"bundle={runtime.resolve()}",
        "cache="
        f"{home.resolve()}/Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes",
        "manifest=unset",
        "arg=--probe",
        "arg=two words",
    ]
    assert not (
        home / "Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes"
    ).exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="Buzz launcher is Darwin-only")
def test_launcher_uses_passwd_home_when_environment_home_is_relative(
    tmp_path: Path,
) -> None:
    """Finder fallback must copy the account home before releasing passwd storage."""
    app = tmp_path / "Passwd Fallback.app"
    launcher = _compile_launcher(app)
    runtime = app / "Contents/Resources/mesh-runtime"
    runtime.mkdir(parents=True)
    (runtime / "manifest.json").write_text("{}\n", encoding="utf-8")
    record = tmp_path / "passwd-record"
    payload = app / "Contents/MacOS/buzz-desktop.real"
    payload.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$MESH_LLM_NATIVE_RUNTIME_CACHE_DIR" > \
  "$BUZZ_LAUNCHER_TEST_RECORD"
""",
        encoding="utf-8",
    )
    payload.chmod(0o755)
    result = subprocess.run(  # noqa: S603 -- Executes the compiled fixture.
        [str(launcher)],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ
        | {
            "BUZZ_LAUNCHER_TEST_RECORD": str(record),
            "HOME": "relative-home-must-not-be-used",
        },
    )
    assert result.returncode == 0, result.stderr
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    assert record.read_text(encoding="utf-8").strip() == (
        f"{account_home}/Library/Caches/xyz.block.buzz.app/mesh-llm/native-runtimes"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="Buzz launcher is Darwin-only")
@pytest.mark.parametrize("missing", ["manifest", "payload"])
def test_launcher_rejects_incomplete_app_layout(tmp_path: Path, missing: str) -> None:
    """The launcher must fail before handoff when assembly is incomplete."""
    app = tmp_path / "Incomplete.app"
    launcher = _compile_launcher(app)
    runtime = app / "Contents/Resources/mesh-runtime"
    runtime.mkdir(parents=True)
    payload = app / "Contents/MacOS/buzz-desktop.real"
    if missing != "manifest":
        (runtime / "manifest.json").write_text("{}\n", encoding="utf-8")
    if missing != "payload":
        _write_executable(payload, b"#!/bin/sh\nexit 0\n")
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(  # noqa: S603 -- Executes the compiled fixture.
        [str(launcher)],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"HOME": str(home)},
    )
    assert result.returncode != 0
    assert result.stderr.startswith("Buzz launcher:")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"id": "other"}, "runtime.id"),
        ({"mesh_version": "other"}, "runtime.mesh_version"),
        ({"skippy_abi": "other"}, "runtime.skippy_abi"),
        ({"platform": {}}, "runtime.platform"),
        ({"backend": {}}, "runtime.backend"),
        ({"rank": True}, "runtime.rank"),
        ({"rank": 1}, "runtime.rank"),
        ({"extra": 1}, "runtime schema"),
        ({"files": []}, "runtime.files"),
        ({"files": {}}, "runtime.files"),
        ({"libraries": None}, "runtime.libraries"),
        ({"libraries": []}, "runtime.libraries"),
        ({"libraries": [""]}, "runtime.libraries"),
        ({"libraries": [1]}, "runtime.libraries"),
        ({"libraries": ["lib/libmesh.dylib"] * 2}, "duplicates"),
        ({"libraries": ["unlisted"]}, "not all covered"),
        ({"files": {"": "a" * 64}}, "invalid file path"),
        ({"files": {"/absolute": "a" * 64}}, "not normalized"),
        ({"files": {"lib//file": "a" * 64}}, "not normalized"),
        ({"files": {"../file": "a" * 64}}, "unsafe"),
        ({"files": {"manifest.json": "a" * 64}}, "unsafe"),
        ({"files": {"lib/libmesh.dylib": 1}}, "digest is invalid"),
        ({"files": {"lib/libmesh.dylib": "bad"}}, "digest is invalid"),
        ({"files": {"lib/libmesh.dylib": "a" * 64}}, "digest mismatch"),
        ({"files": {"missing": "a" * 64}}, "file is missing"),
    ],
)
def test_runtime_validator_rejects_manifest_contract_drift_directly(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    runtime = _runtime_fixture(tmp_path)
    manifest_path = runtime / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["runtime"].update(change)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SystemExit, match=message):
        validate_runtime.validate(runtime, _MESH_VERSION, _SKIPPY_ABI)


@pytest.mark.parametrize("validator", ["validate_runtime", "validate_runtime_load"])
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing-root", "root is not a regular directory"),
        ("symlink-root", "root is not a regular directory"),
        ("missing-manifest", "manifest.json is not a regular file"),
        ("symlink-manifest", "manifest.json is not a regular file"),
        ("invalid-json", "invalid manifest.json"),
        ("invalid-unicode", "invalid manifest.json"),
        ("manifest-list", "schema|runtime.libraries"),
        ("runtime-list", "no runtime object|runtime.libraries"),
    ],
)
def test_runtime_validators_reject_unreadable_bundle(
    tmp_path: Path, validator: str, case: str, message: str
) -> None:
    runtime = _runtime_fixture(tmp_path)
    manifest_path = runtime / "manifest.json"
    match case:
        case "missing-root":
            runtime = tmp_path / "missing"
        case "symlink-root":
            link = tmp_path / "link"
            link.symlink_to(runtime, target_is_directory=True)
            runtime = link
        case "missing-manifest":
            manifest_path.unlink()
        case "symlink-manifest":
            target = tmp_path / "manifest.json"
            manifest_path.rename(target)
            manifest_path.symlink_to(target)
        case "invalid-json":
            manifest_path.write_text("{")
        case "invalid-unicode":
            manifest_path.write_bytes(b"\xff")
        case "manifest-list":
            manifest_path.write_text("[]")
        case "runtime-list":
            manifest_path.write_text('{"runtime": []}')
    if validator == "validate_runtime":
        with pytest.raises(SystemExit, match=message):
            validate_runtime.validate(runtime, _MESH_VERSION, _SKIPPY_ABI)
    else:
        with pytest.raises(SystemExit, match=message):
            validate_runtime_load.validate(runtime, _SKIPPY_ABI)


@pytest.mark.parametrize("case", ["extra-file", "escaping-symlink"])
def test_runtime_validator_checks_inventory_and_resolved_paths(
    tmp_path: Path, case: str
) -> None:
    runtime = _runtime_fixture(tmp_path)
    if case == "extra-file":
        (runtime / "extra").write_text("unlisted")
        message = "file inventory differs"
    else:
        library = runtime / "lib/libmesh.dylib"
        target = tmp_path / "outside.dylib"
        library.rename(target)
        library.symlink_to(target)
        message = "file escapes"
    with pytest.raises(SystemExit, match=message):
        validate_runtime.validate(runtime, _MESH_VERSION, _SKIPPY_ABI)


def test_runtime_validator_cli_accepts_complete_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime_fixture(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["validate_runtime.py", _MESH_VERSION, _SKIPPY_ABI, str(runtime)]
    )
    runpy.run_path(
        str(_CANDIDATE_PATH.parent / "validate_runtime.py"), run_name="__main__"
    )


@pytest.mark.parametrize(
    ("libraries", "message"),
    [
        (None, "nonempty string list"),
        ([], "nonempty string list"),
        ([""], "nonempty string list"),
        ([1], "nonempty string list"),
        (["/absolute"], "not normalized"),
        (["lib//mesh"], "not normalized"),
        (["../outside"], "unsafe"),
        (["missing"], "escapes the bundle"),
        (["lib"], "not a file"),
    ],
)
def test_runtime_load_rejects_invalid_library_inventory(
    tmp_path: Path, libraries: object, message: str
) -> None:
    runtime = _runtime_fixture(tmp_path)
    (runtime / "manifest.json").write_text(
        json.dumps({"runtime": {"libraries": libraries}})
    )
    with pytest.raises(SystemExit, match=message):
        validate_runtime_load.validate(runtime, _SKIPPY_ABI)


@pytest.mark.parametrize(
    "case", ["success", "load-error", "missing-symbol", "wrong-abi"]
)
def test_runtime_load_attests_the_exported_abi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    runtime = _runtime_fixture(tmp_path)
    manifest_path = runtime / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["runtime"]["libraries"].append("share/mesh-runtime.txt")
    manifest_path.write_text(json.dumps(manifest))
    abi = [int(part) for part in _SKIPPY_ABI.split(".")]
    if case == "wrong-abi":
        abi[-1] += 1
    function = Mock(return_value=validate_runtime_load.AbiVersion(*abi))
    handles = [SimpleNamespace(skippy_abi_version=function), SimpleNamespace()]
    if case == "missing-symbol":
        handles = [SimpleNamespace(), SimpleNamespace()]
    loader = Mock(
        side_effect=OSError("load failed") if case == "load-error" else handles
    )
    monkeypatch.setattr(validate_runtime_load.ctypes, "CDLL", loader)
    if case == "success":
        validate_runtime_load.validate(runtime, _SKIPPY_ABI)
        assert function.restype is validate_runtime_load.AbiVersion
        assert loader.call_args_list == [
            call(str((runtime / library).resolve()), mode=ctypes.RTLD_GLOBAL)
            for library in manifest["runtime"]["libraries"]
        ]
    else:
        expected = {
            "load-error": "could not load",
            "missing-symbol": "symbol not found",
            "wrong-abi": "Skippy ABI differs",
        }
        with pytest.raises(SystemExit, match=expected[case]):
            validate_runtime_load.validate(runtime, _SKIPPY_ABI)


@pytest.mark.parametrize("case", ["valid", "extra", "invalid", "missing"])
def test_entitlement_validator_exact_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    path = tmp_path / "entitlements.plist"
    if case != "missing":
        value = dict(_REQUIRED_ENTITLEMENTS)
        if case == "extra":
            value["unreviewed"] = True
        path.write_bytes(b"invalid" if case == "invalid" else plistlib.dumps(value))
    if case == "valid":
        monkeypatch.setattr(sys, "argv", ["validate_entitlements.py", str(path), "app"])
        runpy.run_path(
            str(_CANDIDATE_PATH.parent / "validate_entitlements.py"),
            run_name="__main__",
        )
    else:
        with pytest.raises(SystemExit, match="app entitlement"):
            validate_entitlements.validate(path, "app")


def _rpath_fixture(root: Path) -> tuple[Path, Path]:
    app = root / "Buzz.app"
    executable = app / "Contents/MacOS/buzz"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(macho([build_version()]))
    framework = app / "Contents/Frameworks"
    framework.mkdir()
    (framework / "libmesh.dylib").touch()
    return app, executable


@pytest.mark.parametrize("origin", ["@loader_path", "@executable_path"])
@pytest.mark.parametrize("indirect", [False, True])
def test_rpath_validator_resolves_app_local_libraries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str, *, indirect: bool
) -> None:
    app, executable = _rpath_fixture(tmp_path)
    commands = [build_version(), string_command(0xC, "/usr/lib/libSystem.B.dylib")]
    edge = f"{origin}/../Frameworks/libmesh.dylib"
    if indirect:
        commands.append(string_command(0x8000001C, f"{origin}/../Frameworks"))
        edge = "@rpath/libmesh.dylib"
    commands.append(string_command(0xC, edge))
    executable.write_bytes(macho(commands))
    monkeypatch.setattr(sys, "argv", ["validate_rpaths.py", str(app), str(executable)])
    runpy.run_path(
        str(_CANDIDATE_PATH.with_name("validate_rpaths.py")), run_name="__main__"
    )


@pytest.mark.parametrize(
    ("commands", "message"),
    [
        ([], "no unique macOS deployment target"),
        ([build_version(), build_version()], "no unique macOS deployment target"),
        ([build_version(platform=2)], "not a macOS executable"),
        ([build_version(0xE0100)], "requires macOS newer"),
        (
            [build_version(), string_command(0x8000001C, "/usr/lib")],
            "forbidden LC_RPATH",
        ),
        (
            [build_version(), string_command(0x8000001C, "@loader_path/buzz")],
            "not an app-local directory",
        ),
        (
            [build_version(), string_command(0x8000001C, "@loader_path/missing")],
            "escapes Buzz.app",
        ),
    ],
)
def test_rpath_validator_rejects_invalid_load_commands(
    tmp_path: Path, commands: list[bytes], message: str
) -> None:
    app, executable = _rpath_fixture(tmp_path)
    executable.write_bytes(macho(commands))
    with pytest.raises(SystemExit, match=message):
        validate_rpaths.validate(app, executable)


def test_runtime_load_cli_reports_native_loader_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime_fixture(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["validate_runtime_load.py", _SKIPPY_ABI, str(runtime)]
    )
    monkeypatch.setattr(ctypes, "CDLL", Mock(side_effect=OSError("invalid library")))
    with pytest.raises(SystemExit, match="could not load lib/libmesh.dylib"):
        runpy.run_path(
            str(_CANDIDATE_PATH.parent / "validate_runtime_load.py"),
            run_name="__main__",
        )


@pytest.mark.parametrize(
    ("edge", "message"),
    [
        ("/usr/lib/../evil.dylib", "forbidden dynamic-library edge"),
        ("/usr//lib/evil.dylib", "forbidden dynamic-library edge"),
        ("/opt/evil.dylib", "forbidden dynamic-library edge"),
        ("@loader_path/missing", "escapes Buzz.app"),
        ("@executable_path", "not an app-local file"),
        ("@rpath/missing", "unresolved @rpath"),
        ("@rpath", "unresolved @rpath"),
        ("@rpath/..", "unresolved @rpath"),
    ],
)
def test_rpath_validator_rejects_unresolved_or_external_libraries(
    tmp_path: Path, edge: str, message: str
) -> None:
    paths = _rpath_fixture(tmp_path)
    paths[1].write_bytes(
        macho([
            build_version(),
            string_command(0xC, edge),
            string_command(0x8000001C, "@loader_path"),
        ])
    )
    with pytest.raises(SystemExit, match=message):
        validate_rpaths.validate(*paths)


def test_rpath_validator_rejects_symlink_escape(tmp_path: Path) -> None:
    paths = _rpath_fixture(tmp_path)
    outside = tmp_path / "outside.dylib"
    outside.touch()
    (paths[1].parent / "escape.dylib").symlink_to(outside)
    paths[1].write_bytes(
        macho([
            build_version(),
            string_command(0xC, "@rpath/escape.dylib"),
            string_command(0x8000001C, "@loader_path"),
        ])
    )
    with pytest.raises(SystemExit, match="dynamic-library edge escapes Buzz.app"):
        validate_rpaths.validate(*paths)


@pytest.mark.parametrize("outside", [False, True])
def test_rpath_validator_rejects_unreadable_or_unscoped_input(
    tmp_path: Path, *, outside: bool
) -> None:
    app, executable = _rpath_fixture(tmp_path)
    if outside:
        executable = tmp_path / "outside"
        executable.touch()
        message = "executable escapes Buzz.app"
    else:
        executable.write_bytes(b"invalid")
        message = "invalid Mach-O"
    with pytest.raises(SystemExit, match=message):
        validate_rpaths.validate(app, executable)
