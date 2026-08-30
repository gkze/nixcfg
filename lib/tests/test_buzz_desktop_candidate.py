"""Contracts for Buzz's validated desktop application candidate."""

import hashlib
import json
import os
import plistlib
import pwd
import shlex
import subprocess
import sys
from collections.abc import Callable
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
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._buzz_native_lock import (
    buzz_native_lock_string,
    render_buzz_native_lock_interpolations,
)
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
    return _expand_embedded_validators(script)


def _install_check_script() -> str:
    script = _expand_embedded_validators(_scope_string("installCheckPhase"))
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


def _expand_embedded_validators(script: str) -> str:
    validator = _scope_string("runtimeValidator")
    validation_command = f'"$PYTHON_TOOL" -c {shlex.quote(validator)}'
    script = script.replace("${runtimeValidationCommand}", validation_command)
    if "${runtimeLoadValidationCommand}" in script:
        script = script.replace(
            "${runtimeLoadValidationCommand}", '"$RUNTIME_LOAD_VALIDATOR"'
        )
    if "${entitlementsValidationCommand}" in script:
        entitlements_validator = _scope_string("entitlementsValidator")
        entitlements_command = (
            f'"$PYTHON_TOOL" -c {shlex.quote(entitlements_validator)}'
        )
        script = script.replace(
            "${entitlementsValidationCommand}", entitlements_command
        )
    if "${rpathValidationCommand}" in script:
        rpath_validator = _scope_string("rpathValidator")
        rpath_command = f'"$PYTHON_TOOL" -c {shlex.quote(rpath_validator)}'
        script = script.replace("${rpathValidationCommand}", rpath_command)
    return script


def test_updater_lock_interpolation_preserves_attested_validator_text() -> None:
    """Externalized identities must not perturb the validated candidate derivation."""
    runtime_validator = _scope_string("runtimeValidator")
    assert "EXPECTED_MESH_VERSION" not in runtime_validator
    assert "EXPECTED_SKIPPY_ABI" not in runtime_validator
    assert f'if runtime.get("mesh_version") != "{_MESH_VERSION}":' in runtime_validator
    assert f'if runtime.get("skippy_abi") != "{_SKIPPY_ABI}":' in runtime_validator

    runtime_load_validator = _scope_string("runtimeLoadValidator")
    expected_abi = ", ".join(_SKIPPY_ABI.split("."))
    assert "EXPECTED_ABI_TEXT" not in runtime_load_validator
    assert f"EXPECTED_ABI = ({expected_abi})" in runtime_load_validator
    assert f'"Skippy ABI differs from {_SKIPPY_ABI}: "' in runtime_load_validator


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
    fail_otool: bool = False,
    fail_runtime_find: bool = False,
    macho_case: str | None = None,
) -> dict[str, str]:
    tools = root / "install-check-tools"
    tools.mkdir()
    file_tool = tools / "file"
    lipo_tool = tools / "lipo"
    otool_tool = tools / "otool"
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
    load_commands: list[str] = []
    for minimum_version in minimum_versions:
        load_commands.extend([
            f"Load command {len(load_commands)}",
            "          cmd LC_BUILD_VERSION",
            "      cmdsize 32",
            f"     platform {platform}",
            f"        minos {minimum_version}",
            "          sdk 15.0",
        ])
    if rpath is not None:
        load_commands.extend([
            f"Load command {len(load_commands)}",
            "          cmd LC_RPATH",
            "      cmdsize 48",
            f"         path {rpath} (offset 12)",
        ])
    otool_tool.write_text(
        f"""#!{sys.executable}
import sys

if {fail_otool!r}:
    raise SystemExit(47)
if sys.argv[1] == "-L":
    print(f"{{sys.argv[2]}}:")
    print({dependency!r} + " (compatibility version 1.0.0, current version 1.0.0)")
elif sys.argv[1] == "-l":
    print("\\n".join({load_commands!r}))
else:
    raise SystemExit(49)
""",
        encoding="utf-8",
    )
    otool_tool.chmod(0o755)

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
        "OTOOL_TOOL": str(otool_tool),
        "PATH": path,
        "PLISTBUDDY_TOOL": str(plistbuddy_tool),
        "PYTHON_TOOL": sys.executable,
        "RUNTIME_LOAD_VALIDATOR": str(runtime_load_validator),
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
    extra_dumped_entitlement: bool = False,
    fail_inventory_find: bool = False,
    fail_otool: bool = False,
    fail_runtime_find: bool = False,
    macho_case: str | None = None,
) -> subprocess.CompletedProcess[str]:
    tools = _install_check_tools(
        tmp_path,
        extra_dumped_entitlement=extra_dumped_entitlement,
        fail_inventory_find=fail_inventory_find,
        fail_otool=fail_otool,
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
    ("command_name", "validator_name"),
    [
        ("runtimeValidationCommand", "runtimeValidator"),
        ("runtimeLoadValidationCommand", "runtimeLoadValidator"),
        ("entitlementsValidationCommand", "entitlementsValidator"),
        ("rpathValidationCommand", "rpathValidator"),
    ],
)
def test_validator_command_prefix_keeps_call_site_arguments_on_same_command(
    command_name: str,
    validator_name: str,
) -> None:
    """A command prefix must not end with a newline before its positional args."""
    command = expect_instance(
        expect_binding(_candidate_scope(), command_name).value,
        StringPrimitive,
    )
    assert command.value == (
        f'\\"$PYTHON_TOOL\\" -c ${{lib.escapeShellArg {validator_name}}}'
    )


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
        [sys.executable, "-c", _scope_string("runtimeLoadValidator"), str(runtime)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted, result.stderr
    if not accepted:
        assert f"Skippy ABI differs from {_SKIPPY_ABI}" in result.stderr

    assert '${runtimeLoadValidationCommand} "$runtime"' in _scope_string(
        "installCheckPhase"
    )


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
        ("otool", "otool -L failed"),
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
        fail_otool=failure == "otool",
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
