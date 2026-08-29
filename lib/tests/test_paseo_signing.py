"""Signing contracts for the Nix-owned Paseo application bundle."""

import plistlib
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import (
    command_texts,
    indented_string_body,
    iter_nodes,
    node_text,
    parse_shell,
)
from lib.tests._updater_helpers import load_repo_module
from lib.update.paths import REPO_ROOT

_PACKAGE_DIR = REPO_ROOT / "packages/paseo"
_EXPECTED_ENTITLEMENTS = {
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    "com.apple.security.cs.disable-library-validation": True,
    "com.apple.security.device.audio-input": True,
}
_EXPECTED_FRAMEWORKS = (
    "Contents/Frameworks/Electron Framework.framework",
    "Contents/Frameworks/Mantle.framework",
    "Contents/Frameworks/ReactiveObjC.framework",
    "Contents/Frameworks/Squirrel.framework",
)
_EXPECTED_HELPERS = (
    "Contents/Frameworks/Paseo Helper (GPU).app",
    "Contents/Frameworks/Paseo Helper (Plugin).app",
    "Contents/Frameworks/Paseo Helper (Renderer).app",
    "Contents/Frameworks/Paseo Helper.app",
)
_EXPECTED_ENTITLED_MACHOS = (
    "Contents/Frameworks/Paseo Helper (GPU).app/Contents/MacOS/Paseo Helper (GPU)",
    "Contents/Frameworks/Paseo Helper (Plugin).app/Contents/MacOS/Paseo Helper (Plugin)",
    "Contents/Frameworks/Paseo Helper (Renderer).app/Contents/MacOS/Paseo Helper (Renderer)",
    "Contents/Frameworks/Paseo Helper.app/Contents/MacOS/Paseo Helper",
    "Contents/MacOS/Paseo",
)
_EXPECTED_UNENTITLED_EXECUTABLE_MACHOS = (
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Helpers/chrome_crashpad_handler",
    "Contents/Frameworks/Squirrel.framework/Versions/A/Resources/ShipIt",
    "Contents/Resources/app.asar.unpacked/node_modules/@esbuild/darwin-arm64/bin/esbuild",
    "Contents/Resources/app.asar.unpacked/node_modules/node-pty/build/Release/spawn-helper",
)
_EXPECTED_EXECUTABLE_MACHOS = (
    *_EXPECTED_ENTITLED_MACHOS,
    *_EXPECTED_UNENTITLED_EXECUTABLE_MACHOS,
)


def _load_signature_validator() -> ModuleType:
    return load_repo_module(
        "packages/paseo/validate_signatures.py",
        "paseo_signature_validator_test",
    )


def _reviewed_entitlements() -> bytes:
    return plistlib.dumps(_EXPECTED_ENTITLEMENTS)


def _signature_details(*, flags: str = "adhoc,runtime") -> str:
    return (
        "Executable=/fixture\n"
        "Identifier=fixture\n"
        "Signature=adhoc\n"
        "TeamIdentifier=not set\n"
        f"CodeDirectory v=20500 size=500 flags=0x10002({flags}) "
        "hashes=3+7 location=embedded\n"
    )


def _materialize_reviewed_bundle(tmp_path: Path) -> tuple[Path, set[str], set[str]]:
    app = tmp_path / "Paseo.app"
    for relative_path in (*_EXPECTED_FRAMEWORKS, *_EXPECTED_HELPERS):
        (app / relative_path).mkdir(parents=True)

    executable_paths = {str(app / path) for path in _EXPECTED_EXECUTABLE_MACHOS}
    entitled_paths = {
        str(app),
        *(str(app / path) for path in _EXPECTED_HELPERS),
        *(str(app / path) for path in _EXPECTED_ENTITLED_MACHOS),
    }
    for path in executable_paths:
        candidate = Path(path)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"fixture executable Mach-O\n")
    native_root = app / "Contents/Resources/reviewed-native"
    native_root.mkdir(parents=True)
    for index in range(97):
        candidate = native_root / f"native-{index:03d}"
        candidate.write_bytes(b"fixture Mach-O\n")
    return app, executable_paths, entitled_paths


def _successful_codesign_runner(
    *,
    executable_paths: set[str],
    entitled_paths: set[str],
) -> tuple[
    Callable[[list[str]], subprocess.CompletedProcess[bytes]],
    list[tuple[str, ...]],
]:
    calls: list[tuple[str, ...]] = []

    def run(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
        calls.append(tuple(arguments))
        candidate = arguments[-1]
        if arguments[0] == "/usr/bin/file":
            description = (
                b"Mach-O 64-bit executable arm64\n"
                if candidate in executable_paths
                else b"Mach-O 64-bit dynamically linked shared library arm64\n"
            )
            return subprocess.CompletedProcess(arguments, 0, description, b"")
        if "--entitlements" in arguments:
            entitlements = (
                _reviewed_entitlements() if candidate in entitled_paths else b""
            )
            return subprocess.CompletedProcess(arguments, 0, entitlements, b"")
        if "--verbose=4" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                b"",
                _signature_details().encode(),
            )
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    return run, calls


def _real_package_arguments() -> AttributeSet:
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    real_package = expect_instance(
        expect_binding(final.scope, "realPackage").value,
        FunctionCall,
    )
    return expect_instance(real_package.argument, AttributeSet)


def test_paseo_owns_the_exact_reviewed_runtime_entitlements() -> None:
    """The app must not inherit an incomplete upstream development plist."""
    entitlements = plistlib.loads((_PACKAGE_DIR / "Entitlements.plist").read_bytes())

    assert entitlements == _EXPECTED_ENTITLEMENTS


def test_paseo_signature_policy_honors_each_code_paths_entitlement_requirement() -> (
    None
):
    """The caller decides which exact code paths receive the reviewed entitlement set."""
    module = _load_signature_validator()

    module.validate_signature_evidence(
        label="main executable",
        entitlements_payload=_reviewed_entitlements(),
        details=_signature_details(),
        require_entitlements=True,
        strict_verified=True,
    )
    module.validate_signature_evidence(
        label="standalone executable",
        entitlements_payload=b"",
        details=_signature_details(),
        require_entitlements=False,
        strict_verified=True,
    )


@pytest.mark.parametrize(
    ("entitlements", "details", "require_entitlements", "strict", "error"),
    [
        (
            _reviewed_entitlements(),
            _signature_details(),
            True,
            False,
            "strict verification",
        ),
        (
            b"not a plist",
            _signature_details(),
            True,
            True,
            "invalid Paseo entitlements",
        ),
        (
            _reviewed_entitlements(),
            _signature_details(),
            False,
            True,
            "code .* unexpectedly has entitlements",
        ),
        (
            plistlib.dumps({"unexpected": True}),
            _signature_details(),
            True,
            True,
            "unexpected Paseo entitlements",
        ),
        (b"", _signature_details(), True, True, "lacks required entitlements"),
        (
            _reviewed_entitlements(),
            _signature_details().replace("Signature=adhoc", "Signature=Developer ID"),
            True,
            True,
            "not exactly ad hoc",
        ),
        (
            _reviewed_entitlements(),
            _signature_details().replace("TeamIdentifier=not set\n", ""),
            True,
            True,
            "has a Team ID",
        ),
        (
            _reviewed_entitlements(),
            _signature_details() + _signature_details().splitlines()[-1] + "\n",
            True,
            True,
            "2 CodeDirectory lines",
        ),
        (
            _reviewed_entitlements(),
            _signature_details(flags="adhoc"),
            True,
            True,
            "lacks required flags",
        ),
    ],
)
def test_paseo_signature_policy_rejects_drifted_evidence(
    entitlements: bytes,
    details: str,
    require_entitlements: bool,
    strict: bool,
    error: str,
) -> None:
    """Every signature-policy dimension must fail closed with its own reason."""
    module = _load_signature_validator()

    with pytest.raises(ValueError, match=error):
        module.validate_signature_evidence(
            label="drifted code",
            entitlements_payload=entitlements,
            details=details,
            require_entitlements=require_entitlements,
            strict_verified=strict,
        )


def test_paseo_signature_validator_accepts_only_the_reviewed_bundle_inventory(
    tmp_path: Path,
) -> None:
    """The realized bundle must cross every audited count and nested-signature gate."""
    module = _load_signature_validator()
    app, executable_paths, entitled_paths = _materialize_reviewed_bundle(tmp_path)
    runner, calls = _successful_codesign_runner(
        executable_paths=executable_paths,
        entitled_paths=entitled_paths,
    )

    module.validate_bundle(app, runner=runner)

    file_calls = [call for call in calls if call[0] == "/usr/bin/file"]
    codesign_calls = [call for call in calls if call[0] == "/usr/bin/codesign"]
    assert len(file_calls) == 106
    macho_paths = {call[-1] for call in file_calls}
    assert macho_paths & entitled_paths == {
        str(app / path) for path in _EXPECTED_ENTITLED_MACHOS
    }
    assert executable_paths - entitled_paths == {
        str(app / path) for path in _EXPECTED_UNENTITLED_EXECUTABLE_MACHOS
    }
    assert codesign_calls[-1] == (
        "/usr/bin/codesign",
        "--verify",
        "--deep",
        "--strict",
        str(app),
    )


def test_paseo_signature_cli_uses_system_tools_and_ignores_non_macho_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package CLI accepts reviewed bundles containing ordinary resource files."""
    module = _load_signature_validator()
    app, executable_paths, entitled_paths = _materialize_reviewed_bundle(tmp_path)
    resource = app / "Contents/Resources/README.txt"
    resource.write_text("ordinary resource\n", encoding="utf-8")
    signing_runner, _ = _successful_codesign_runner(
        executable_paths=executable_paths,
        entitled_paths=entitled_paths,
    )
    system_calls: list[tuple[tuple[str, ...], bool, bool]] = []

    def system_runner(
        arguments: list[str],
        *,
        check: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        system_calls.append((tuple(arguments), check, capture_output))
        if arguments == ["/usr/bin/file", "-b", str(resource)]:
            return subprocess.CompletedProcess(arguments, 0, b"ASCII text\n", b"")
        return signing_runner(arguments)

    monkeypatch.setattr(module.subprocess, "run", system_runner)
    monkeypatch.setattr(sys, "argv", ["validate_signatures.py", str(app)])

    module.main()

    assert (("/usr/bin/file", "-b", str(resource)), False, True) in system_calls
    assert all(
        not check and capture_output for _, check, capture_output in system_calls
    )
    assert not any(
        arguments[0] == "/usr/bin/codesign" and arguments[-1] == str(resource)
        for arguments, _, _ in system_calls
    )


def test_paseo_signature_validator_rejects_wrong_counts_and_nested_inventories(
    tmp_path: Path,
) -> None:
    """The audited 106/9/97 and 4/4 shapes are exact, not lower bounds."""
    module = _load_signature_validator()

    count_app, count_executable, count_entitled = _materialize_reviewed_bundle(
        tmp_path / "count"
    )
    (count_app / "Contents/Resources/reviewed-native/native-096").unlink()
    count_runner, _ = _successful_codesign_runner(
        executable_paths=count_executable,
        entitled_paths=count_entitled,
    )
    with pytest.raises(ValueError, match="expected 106/9/97, got 105/9/96"):
        module.validate_bundle(count_app, runner=count_runner)

    entitlement_app, entitlement_executable, entitlement_paths = (
        _materialize_reviewed_bundle(tmp_path / "entitlement")
    )
    missing_entitled_macho = entitlement_app / _EXPECTED_ENTITLED_MACHOS[0]
    missing_entitled_macho.unlink()
    replacement_executable = (
        entitlement_app / "Contents/Resources/reviewed-native/standalone-replacement"
    )
    replacement_executable.write_bytes(b"fixture executable Mach-O\n")
    entitlement_executable.remove(str(missing_entitled_macho))
    entitlement_executable.add(str(replacement_executable))
    entitlement_runner, _ = _successful_codesign_runner(
        executable_paths=entitlement_executable,
        entitled_paths=entitlement_paths,
    )
    with pytest.raises(
        ValueError, match="entitlement inventory expected 5/101, got 4/102"
    ):
        module.validate_bundle(entitlement_app, runner=entitlement_runner)

    executable_app, executable_paths, executable_entitled = (
        _materialize_reviewed_bundle(tmp_path / "executable")
    )
    missing_executable = executable_app / _EXPECTED_UNENTITLED_EXECUTABLE_MACHOS[0]
    missing_executable.unlink()
    replacement_executable = (
        executable_app / "Contents/Resources/reviewed-native/unreviewed-executable"
    )
    replacement_executable.write_bytes(b"fixture executable Mach-O\n")
    executable_paths.remove(str(missing_executable))
    executable_paths.add(str(replacement_executable))
    executable_runner, _ = _successful_codesign_runner(
        executable_paths=executable_paths,
        entitled_paths=executable_entitled,
    )
    with pytest.raises(ValueError, match="unexpected Paseo executable inventory"):
        module.validate_bundle(executable_app, runner=executable_runner)

    framework_app, framework_executable, framework_entitled = (
        _materialize_reviewed_bundle(tmp_path / "framework")
    )
    (framework_app / "Contents/Frameworks/Unexpected.framework").mkdir()
    framework_runner, _ = _successful_codesign_runner(
        executable_paths=framework_executable,
        entitled_paths=framework_entitled,
    )
    with pytest.raises(ValueError, match="unexpected Paseo framework inventory"):
        module.validate_bundle(framework_app, runner=framework_runner)

    helper_app, helper_executable, helper_entitled = _materialize_reviewed_bundle(
        tmp_path / "helper"
    )
    (helper_app / "Contents/Frameworks/Unexpected Helper.app").mkdir()
    helper_runner, _ = _successful_codesign_runner(
        executable_paths=helper_executable,
        entitled_paths=helper_entitled,
    )
    with pytest.raises(ValueError, match="unexpected Paseo helper inventory"):
        module.validate_bundle(helper_app, runner=helper_runner)


def test_paseo_signature_validator_propagates_command_and_entitlement_failures(
    tmp_path: Path,
) -> None:
    """A failed strict check or missing executable entitlement aborts validation."""
    module = _load_signature_validator()
    app, executable_paths, entitled_paths = _materialize_reviewed_bundle(tmp_path)
    successful_runner, _ = _successful_codesign_runner(
        executable_paths=executable_paths,
        entitled_paths=entitled_paths,
    )

    def failing_runner(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
        if arguments[:3] == ["/usr/bin/codesign", "--verify", "--strict"]:
            return subprocess.CompletedProcess(arguments, 1, b"", b"invalid signature")
        return successful_runner(arguments)

    with pytest.raises(ValueError, match="command failed.*invalid signature"):
        module.validate_bundle(app, runner=failing_runner)

    missing_entitlement = str(app / _EXPECTED_ENTITLED_MACHOS[0])
    entitled_paths.remove(missing_entitlement)
    no_entitlement_runner, _ = _successful_codesign_runner(
        executable_paths=executable_paths,
        entitled_paths=entitled_paths,
    )
    with pytest.raises(ValueError, match="lacks required entitlements"):
        module.validate_bundle(app, runner=no_entitlement_runner)

    entitled_paths.add(missing_entitlement)
    entitled_paths.add(str(app / _EXPECTED_UNENTITLED_EXECUTABLE_MACHOS[0]))
    over_entitled_runner, _ = _successful_codesign_runner(
        executable_paths=executable_paths,
        entitled_paths=entitled_paths,
    )
    with pytest.raises(ValueError, match="unexpectedly has entitlements"):
        module.validate_bundle(app, runner=over_entitled_runner)


def test_paseo_signature_validator_requires_an_application_directory(
    tmp_path: Path,
) -> None:
    """Validation must reject an absent or non-directory outer bundle."""
    module = _load_signature_validator()

    with pytest.raises(ValueError, match="app is not a directory"):
        module.validate_bundle(tmp_path / "missing")


def test_paseo_package_signs_bottom_up_and_validates_before_runtime() -> None:
    """The derivation must encode the audited nested-code signing order."""
    package = expect_instance(
        parse_nix_expr((_PACKAGE_DIR / "package.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    final = expect_instance(package.output, IfExpression)
    assert_nix_ast_equal(
        expect_binding(final.scope, "paseoEntitlements").value,
        "./Entitlements.plist",
    )
    assert_nix_ast_equal(
        expect_binding(final.scope, "paseoSignatureValidator").value,
        "./validate_signatures.py",
    )

    arguments = _real_package_arguments()
    post_fixup = expect_instance(
        expect_binding(arguments.values, "postFixup").value,
        IndentedString,
    )
    shell = parse_shell(indented_string_body(post_fixup.rebuild()))
    sign_commands = command_texts(shell, "/usr/bin/codesign")
    assert len(sign_commands) == 4
    assert all("--deep" not in command for command in sign_commands)
    assert all(
        all(
            option in command
            for option in (
                "--force",
                "--timestamp=none",
                "--options runtime",
                "--sign -",
            )
        )
        for command in sign_commands
    )
    assert [command.split()[-1] for command in sign_commands] == [
        '"$macho"',
        '"$framework"',
        '"$helper"',
        '"$app"',
    ]
    assert "--entitlements" not in sign_commands[0]
    assert "--entitlements" not in sign_commands[1]
    assert "--entitlements __NIX_INTERP__" in sign_commands[2]
    assert "--entitlements __NIX_INTERP__" in sign_commands[3]

    conditionals = [
        node_text(node, shell.sanitized)
        for node in iter_nodes(shell.tree.root_node, "if_statement")
    ]
    count_four_gates = [
        conditional
        for conditional in conditionals
        if '"__NIX_INTERP__" -ne 4' in conditional
    ]
    assert len(count_four_gates) == 2

    install_check = expect_instance(
        expect_binding(arguments.values, "installCheckPhase").value,
        IndentedString,
    )
    install_shell = parse_shell(indented_string_body(install_check.rebuild()))
    install_commands = [
        node_text(node, install_shell.sanitized)
        for node in iter_nodes(install_shell.tree.root_node, "command")
    ]
    validator_index = next(
        index
        for index, command in enumerate(install_commands)
        if command == '__NIX_INTERP__ __NIX_INTERP__ "$app"'
    )
    electron_index = next(
        index
        for index, command in enumerate(install_commands)
        if command.startswith("env ") and "ELECTRON_RUN_AS_NODE=1" in command
    )
    assert validator_index < electron_index
