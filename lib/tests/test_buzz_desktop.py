"""Semantic contracts for Buzz's unsigned Tauri desktop build leaf."""

import os
import subprocess
from functools import cache
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    parse_nix_expr,
)
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.scope import Scope

_DESKTOP_PATH = REPO_ROOT / "packages/buzz/native/desktop.nix"
_BUZZ_PACKAGE_PATH = REPO_ROOT / "packages/buzz/package.nix"
_TARGET = "aarch64-apple-darwin"
_SIDECAR_NAMES = (
    f"buzz-acp-{_TARGET}",
    f"buzz-agent-{_TARGET}",
    f"buzz-backend-kubernetes-{_TARGET}",
    f"buzz-dev-mcp-{_TARGET}",
    f"git-credential-nostr-{_TARGET}",
    f"buzz-{_TARGET}",
)


@cache
def _desktop_package() -> tuple[FunctionDefinition, FunctionCall]:
    package = expect_instance(
        parse_nix_expr(_DESKTOP_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        output = output.body
    return package, expect_instance(output, FunctionCall)


def _derivation_arguments() -> AttributeSet:
    _package, derivation = _desktop_package()
    return expect_instance(derivation.argument, AttributeSet)


def _assertion_conditions() -> list[object]:
    package, _derivation = _desktop_package()
    conditions: list[object] = []
    output = package.output
    while isinstance(output, Assertion):
        conditions.append(output.expression)
        output = output.body
    return conditions


def _package_scope() -> Scope:
    package, _derivation = _desktop_package()
    output = package.output
    while isinstance(output, Assertion):
        if output.scope:
            return output.scope
        output = output.body
    raise AssertionError("expected desktop package let-bindings")


@cache
def _buzz_package_scope() -> Scope:
    package = expect_instance(
        parse_nix_expr(_BUZZ_PACKAGE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        if output.scope:
            return output.scope
        output = output.body
    return output.scope


def _staging_script(sidecars: Path) -> str:
    script = expect_instance(
        expect_binding(_package_scope(), "stagingScript").value,
        IndentedString,
    )
    body = dedent(indented_string_body(script.rebuild()))
    assert body.count("${sidecars}") == 1
    return body.replace("${sidecars}", str(sidecars), 1)


def _write_sidecars(root: Path, names: tuple[str, ...] = _SIDECAR_NAMES) -> Path:
    sidecars = root / "sidecars"
    binary_root = sidecars / "bin"
    binary_root.mkdir(parents=True)
    for name in names:
        binary = binary_root / name
        binary.write_bytes(f"fixture:{name}\n".encode())
        binary.chmod(0o755)
    return sidecars


def _run_staging_script(
    tmp_path: Path,
    *,
    extra_source: str | None = None,
    precreate_destination: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    sidecars = _write_sidecars(tmp_path)
    if extra_source is not None:
        unexpected = sidecars / "bin" / extra_source
        unexpected.write_bytes(b"unexpected\n")
        unexpected.chmod(0o755)

    source = tmp_path / "source"
    (source / "desktop/src-tauri").mkdir(parents=True)
    destination = source / "desktop/src-tauri/binaries"
    if precreate_destination:
        destination.mkdir()

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = subprocess.run(
        ["/bin/bash", "-eu"],
        input=_staging_script(sidecars),
        capture_output=True,
        text=True,
        check=False,
        cwd=source,
        env=os.environ | {"TMPDIR": str(scratch)},
    )
    return result, destination


def test_desktop_leaf_has_a_narrow_repo_owned_interface_and_contract() -> None:
    """The unsigned leaf must accept artifacts, not caller-authored provenance."""
    package, derivation = _desktop_package()
    assert {
        expect_instance(argument, Identifier).name for argument in package.argument_set
    } == {
        "cargo-tauri",
        "cmake",
        "lib",
        "makeRustPlatform",
        "nativeLock",
        "nodejs_24",
        "patchedBuzzSource",
        "patchedDesktopCargoDeps",
        "pkg-config",
        "pnpm",
        "pnpmConfigHook",
        "pnpmDeps",
        "rustToolchain",
        "sherpaOnnx",
        "sidecars",
        "stdenv",
        "version",
    }
    assert_nix_ast_equal(
        derivation.name,
        "desktopRustPlatform.buildRustPackage",
    )

    conditions = _assertion_conditions()
    expected_conditions = (
        'stdenv.hostPlatform.system == "aarch64-darwin"',
        "builtins.isString buzzVersion",
        'builtins.isString buzzCommit && builtins.match "[0-9a-f]{40}" buzzCommit != null',
        "builtins.isString rustVersion",
        "builtins.isString pnpmVersion",
        "builtins.isString sherpaVersion",
        'builtins.isString sherpaCommit && builtins.match "[0-9a-f]{40}" sherpaCommit != null',
        "version == buzzVersion",
        "pnpm.version == pnpmVersion",
        "lib.isDerivation pnpmDeps",
        "(rustToolchain.passthru.buzzNativeContract or null) == expectedRustContract",
        "(patchedBuzzSource.passthru.buzzNativeContract or null) == expectedSourceContract",
        "(patchedBuzzSource.passthru.desktopCargoDeps or null) == patchedDesktopCargoDeps",
        "(sherpaOnnx.passthru.buzzNativeContract or null) == expectedSherpaContract",
        "(sidecars.passthru.buzzNativeContract or null) == expectedSidecarsContract",
    )
    assert len(conditions) == len(expected_conditions)
    # The platform assertion is syntactically outside the leaf's `let`, while
    # the remaining provenance assertions share the scoped contract literals.
    for actual, expected in zip(conditions, expected_conditions, strict=True):
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
    assert "macApp" not in binding_map(passthru.values)
    scope = _package_scope()
    for name, expected in {
        "buzzLock": "nativeLock.buzz or { }",
        "pnpmLock": "nativeLock.pnpm or { }",
        "sherpaLock": "nativeLock.sherpaOnnx or { }",
        "buzzCommit": "buzzLock.commit or null",
        "buzzVersion": "buzzLock.version or null",
        "rustVersion": "buzzLock.rustVersion or null",
        "pnpmVersion": "pnpmLock.version or null",
        "sherpaCommit": "sherpaLock.commit or null",
        "sherpaVersion": "sherpaLock.version or null",
    }.items():
        assert_nix_ast_equal(expect_binding(scope, name).value, expected)
    assert_nix_ast_equal(
        expect_binding(scope, "implementedContract").value,
        """{
          kind = "buzz-desktop-unsigned";
          commit = buzzCommit;
          version = buzzVersion;
          target = "aarch64-apple-darwin";
          inherit rustVersion pnpmVersion;
          cargoRoot = "desktop/src-tauri";
          buildAndTestSubdir = "desktop";
          cargoOffline = true;
          cargoFrozen = true;
          frontendBuildCommand = "pnpm build";
          cargoFeatures = [ "mesh-llm" ];
          sidecars = [
            "buzz-acp-aarch64-apple-darwin"
            "buzz-agent-aarch64-apple-darwin"
            "buzz-backend-kubernetes-aarch64-apple-darwin"
            "buzz-dev-mcp-aarch64-apple-darwin"
            "git-credential-nostr-aarch64-apple-darwin"
            "buzz-aarch64-apple-darwin"
          ];
          updaterEnabled = false;
          sherpaOnnxVersion = sherpaVersion;
          minimumMacosVersion = "14.0";
          appSigned = false;
          runtimeBundleEmbedded = false;
        }""",
    )


def test_desktop_leaf_uses_rust_1_95_and_patched_offline_dependencies() -> None:
    """Tauri, Cargo, pnpm, Sherpa, and Mesh must cross one explicit boundary."""
    attrs = _derivation_arguments()

    assert_nix_ast_equal(
        expect_binding(_package_scope(), "desktopRustPlatform").value,
        """makeRustPlatform {
          cargo = rustToolchain;
          rustc = rustToolchain;
        }""",
    )
    for name, expected in {
        "src": "patchedBuzzSource",
        "cargoDeps": "patchedDesktopCargoDeps",
        "pnpmDeps": "pnpmDeps",
        "cargoRoot": '"desktop/src-tauri"',
        "buildAndTestSubdir": '"desktop"',
        "strictDeps": "true",
        "tauriBundleType": '"app"',
        "doCheck": "false",
    }.items():
        assert_nix_ast_equal(expect_binding(attrs.values, name).value, expected)

    assert_nix_ast_equal(
        expect_binding(attrs.values, "nativeBuildInputs").value,
        "[ cargo-tauri.hook cmake nodejs_24 pkg-config pnpm pnpmConfigHook ]",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "buildInputs").value,
        "[ sherpaOnnx ]",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cargoBuildFlags").value,
        '[ "--frozen" ]',
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cargoBuildFeatures").value,
        '[ "mesh-llm" ]',
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "tauriBuildFlags").value,
        '[ "--no-sign" "--verbose" ]',
    )
    pre_build = expect_binding(attrs.values, "preBuild").value
    pre_build_shell = parse_shell(indented_string_body(pre_build.rebuild()))
    assert command_texts(pre_build_shell, "export") == [
        'export PATH="__NIX_INTERP__/bin:$PWD/node_modules/.bin:$PATH"'
    ]
    assert command_texts(pre_build_shell, "__NIX_INTERP__") == ["__NIX_INTERP__"]

    environment = expect_instance(
        expect_binding(attrs.values, "env").value,
        AttributeSet,
    )
    expected_environment = {
        "CARGO_NET_OFFLINE": '"true"',
        "CI": '"true"',
        "MACOSX_DEPLOYMENT_TARGET": '"14.0"',
        "CMAKE_OSX_DEPLOYMENT_TARGET": '"14.0"',
        "npm_config_manage_package_manager_versions": '"false"',
        "SHERPA_ONNX_LIB_DIR": '"${lib.getLib sherpaOnnx}/lib"',
        "BUZZ_UPDATER_ENDPOINT": '""',
        "BUZZ_UPDATER_PUBLIC_KEY": '""',
    }
    assert set(binding_map(environment.values)) == set(expected_environment)
    for name, expected in expected_environment.items():
        assert_nix_ast_equal(expect_binding(environment.values, name).value, expected)


def test_desktop_leaf_independently_attests_native_input_contracts() -> None:
    """Changing an input contract cannot silently widen the desktop trust seam."""
    scope = _package_scope()
    assert_nix_ast_equal(
        expect_binding(scope, "expectedRustContract").value,
        """{
          kind = "rust-toolchain";
          channel = rustVersion;
          profile = "default";
          target = "aarch64-apple-darwin";
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "expectedSourceContract").value,
        """{
          kind = "buzz-runtime-policy-source";
          commit = buzzCommit;
          meshFeature = "dynamic-native-runtime";
          runtimeBundleEnvironment = "MESH_LLM_NATIVE_RUNTIME_BUNDLE_DIR";
          runtimeCacheEnvironment = "MESH_LLM_NATIVE_RUNTIME_CACHE_DIR";
          manifestUrlEnvironment = "MESH_LLM_NATIVE_RUNTIME_MANIFEST_URL";
          requiresBothRuntimeEnvironmentValues = true;
          manifestUrlEnvironmentAllowed = false;
          allowDefaultManifestUrl = false;
          allowDownload = false;
          keyringProbePolicy = {
            preUiInteractionAllowed = false;
            interactionGuard = "security-framework-raii";
            interactionGuardScope = "tauri-setup";
            guardFailure = "keyring-locked";
            unexpectedReadFailure = "unreachable";
            managedAgentSecretLoadsAllowedInRecovery = false;
            identityResolutionUsesReadonlyLoad = true;
            identityResolutionLegacyMigrationAllowed = false;
            postUiInteractionAllowed = true;
            postUiRetryCommand = "retry_keyring_identity";
            postUiRetryUsesExistingIdentity = true;
            postUiRetryMutationAllowed = false;
            postUiRetrySerializedBy = "identity_mutation";
            postUiRetryRequiresRelaunch = true;
          };
          sherpaOnnxTtsEnabled = false;
          sherpaOnnxStaticLinkLibraries = [
            "sherpa-onnx-c-api"
            "sherpa-onnx-core"
            "kaldi-decoder-core"
            "sherpa-onnx-kaldifst-core"
            "sherpa-onnx-fstfar"
            "sherpa-onnx-fst"
            "kaldi-native-fbank-core"
            "kissfft-float"
            "onnxruntime"
            "ssentencepiece_core"
          ];
          updaterRequiresBothEnvironmentValues = true;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "expectedSherpaContract").value,
        """{
          kind = "sherpa-onnx";
          version = sherpaVersion;
          commit = sherpaCommit;
          target = "aarch64-apple-darwin";
          linkMode = "static";
          usePreinstalledOnnxRuntime = true;
          precompiledReleaseArchivesAllowed = false;
          cmakeOptions = {
            BUILD_SHARED_LIBS = false;
            SHERPA_ONNX_ENABLE_BINARY = false;
            SHERPA_ONNX_ENABLE_C_API = true;
            SHERPA_ONNX_ENABLE_GPU = false;
            SHERPA_ONNX_ENABLE_TESTS = false;
            SHERPA_ONNX_ENABLE_TTS = false;
          };
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "expectedSidecarsContract").value,
        """{
          kind = "buzz-sidecars";
          commit = buzzCommit;
          target = "aarch64-apple-darwin";
          profile = "release";
          cargoOffline = true;
          cargoFrozen = true;
          sidecars = [
            { package = "buzz-acp"; binary = "buzz-acp"; }
            { package = "buzz-agent"; binary = "buzz-agent"; }
            { package = "buzz-backend-kubernetes"; binary = "buzz-backend-kubernetes"; }
            { package = "buzz-dev-mcp"; binary = "buzz-dev-mcp"; }
            { package = "git-credential-nostr"; binary = "git-credential-nostr"; }
            { package = "buzz-cli"; binary = "buzz"; }
          ];
          installedBinaries = [
            "buzz-acp-aarch64-apple-darwin"
            "buzz-agent-aarch64-apple-darwin"
            "buzz-backend-kubernetes-aarch64-apple-darwin"
            "buzz-dev-mcp-aarch64-apple-darwin"
            "git-credential-nostr-aarch64-apple-darwin"
            "buzz-aarch64-apple-darwin"
          ];
          binaryFormat = "Mach-O 64-bit executable arm64";
          dylibPolicy = "system-or-loader-relative";
          signature = "adhoc-after-fixup";
        }""",
    )


def test_desktop_leaf_stages_exact_target_qualified_sidecars(tmp_path: Path) -> None:
    """The pre-build seam must copy the exact six executable inputs."""
    result, destination = _run_staging_script(tmp_path)
    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in destination.iterdir()) == sorted(_SIDECAR_NAMES)
    for name in _SIDECAR_NAMES:
        installed = destination / name
        assert installed.read_bytes() == f"fixture:{name}\n".encode()
        assert installed.stat().st_mode & 0o777 == 0o755

    shell = parse_shell(_staging_script(tmp_path / "sidecars"))
    assert command_texts(shell, "/usr/bin/codesign") == []


@pytest.mark.parametrize(
    ("extra_source", "precreate_destination", "message"),
    [
        ("undeclared-aarch64-apple-darwin", False, "inventory is not exact"),
        (None, True, "destination already exists"),
    ],
)
def test_desktop_leaf_rejects_ambiguous_sidecar_staging(
    tmp_path: Path,
    extra_source: str | None,
    precreate_destination: bool,
    message: str,
) -> None:
    """Extra input bytes or a pre-populated destination must stop the build."""
    result, _destination = _run_staging_script(
        tmp_path,
        extra_source=extra_source,
        precreate_destination=precreate_destination,
    )
    assert result.returncode != 0
    assert message in result.stderr


def test_unsigned_leaf_has_no_signing_or_runtime_assembly_phase() -> None:
    """Generic fixup may run, but app signing and Mesh embedding belong later."""
    package, _derivation = _desktop_package()
    attrs = _derivation_arguments()
    assert Identifier(name="meshRuntimeBundle") not in package.argument_set
    assert {
        "installPhase",
        "postInstall",
        "postFixup",
        "fixupPhase",
    }.isdisjoint(binding_map(attrs.values))


def test_buzz_package_exposes_unsigned_desktop_only_as_an_audit_leaf() -> None:
    """The build leaf must remain outside foundation readiness and app routing."""
    scope = _buzz_package_scope()
    assert_nix_ast_equal(
        expect_binding(scope, "desktopUnsignedNative").value,
        """if sherpaOnnxNative == null then
          null
        else
          import ./native/desktop.nix {
            inherit lib nativeLock nodejs_24 pnpm pnpmDeps stdenv version;
            inherit (pkgs) cargo-tauri cmake makeRustPlatform pkg-config pnpmConfigHook;
            patchedBuzzSource = buzzRuntimePolicySource;
            patchedDesktopCargoDeps =
              buzzRuntimePolicySource.passthru.desktopCargoDeps;
            rustToolchain = rustToolchainNative;
            sherpaOnnx = sherpaOnnxNative;
            sidecars = sidecarsNative;
          }""",
    )

    foundation_slots = expect_instance(
        expect_binding(scope, "nativeFoundationSlots").value,
        AttributeSet,
    )
    assert "desktopUnsigned" not in binding_map(foundation_slots.values)

    passthru = expect_instance(
        expect_binding(scope, "commonPassthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "desktopUnsigned").value,
        "desktopUnsignedNative",
    )


def test_buzz_package_exposes_signed_candidate_only_as_an_audit_leaf() -> None:
    """Assembly must stay outside foundation readiness and app routing."""
    scope = _buzz_package_scope()
    assert_nix_ast_equal(
        expect_binding(scope, "desktopCandidateNative").value,
        """if desktopUnsignedNative == null || meshRuntimeBundleNative == null then
          null
        else
          import ./native/desktop-candidate.nix {
            inherit cctools lib nativeLock python3 stdenv version;
            desktopUnsigned = desktopUnsignedNative;
            meshRuntimeBundle = meshRuntimeBundleNative;
            patchedBuzzSource = buzzRuntimePolicySource;
          }""",
    )

    foundation_slots = expect_instance(
        expect_binding(scope, "nativeFoundationSlots").value,
        AttributeSet,
    )
    assert "desktopCandidate" not in binding_map(foundation_slots.values)

    passthru = expect_instance(
        expect_binding(scope, "commonPassthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "desktopCandidate").value,
        "desktopCandidateNative",
    )


def test_buzz_candidate_identity_is_derived_and_validation_is_executable() -> None:
    """Candidate identity must not be copied into a self-staling lock record."""
    scope = _buzz_package_scope()
    assert_nix_ast_equal(
        expect_binding(scope, "expectedMacApp").value,
        """
        {
          bundleId = "xyz.block.buzz.app";
          bundleName = "Buzz.app";
          bundleRelPath = "Applications/Buzz.app";
          installMode = "copy";
        }
        """,
    )
    assert_nix_ast_equal(
        expect_binding(scope, "desktopCandidateWired").value,
        "lib.isDerivation desktopCandidateNative",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "desktopCandidateIdentity").value,
        """if desktopCandidateWired then {
          derivationPath = builtins.unsafeDiscardStringContext desktopCandidateNative.drvPath;
          outputPath = builtins.unsafeDiscardStringContext desktopCandidateNative.outPath;
        } else null""",
    )
    scope_bindings = binding_map(scope)
    assert "desktopBundleValidationEvidence" not in scope_bindings
    assert "expectedDesktopBundleValidation" not in scope_bindings
    assert "desktopBundleValidationComplete" not in scope_bindings
    assert_nix_ast_equal(
        expect_binding(scope, "desktopCandidateExportReady").value,
        """desktopCandidateWired
          && (desktopCandidateNative.passthru.buzzNativeContract.exportReady or false)
          && (desktopCandidateNative.passthru.macApp or null) == expectedMacApp""",
    )
    export_gate = expect_instance(
        expect_binding(scope, "desktopExportGate").value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        export_gate.name,
        "lib.optional (!desktopCandidateExportReady)",
    )
    export_message = expect_instance(export_gate.argument, IndentedString)
    assert_nix_ast_equal(
        export_message,
        """''
        Buzz desktop export is disabled. The exact candidate derivation, its
        install-check contract, and its macOS app metadata must be wired before app
        routing can be enabled.
        ''""",
    )
    gates = expect_instance(
        expect_binding(scope, "unresolvedBuildGates").value,
        BinaryExpression,
    )
    gate_leaves: list[object] = []
    pending: list[object] = [gates]
    while pending:
        gate = pending.pop(0)
        if isinstance(gate, BinaryExpression) and gate.operator.name == "++":
            pending[0:0] = [gate.left, gate.right]
        else:
            gate_leaves.append(gate)
    assert_nix_ast_equal(gate_leaves[-1], "desktopExportGate")

    passthru = expect_instance(
        expect_binding(scope, "commonPassthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "macApp").value,
        "expectedMacApp",
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "buzzDesktopCandidateStatus").value,
        """{
          wired = desktopCandidateWired;
          identity = desktopCandidateIdentity;
          exportReady = desktopCandidateExportReady;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "validatedPackage").value,
        """desktopCandidateNative.overrideAttrs (old: {
          passthru = (old.passthru or { }) // commonPassthru;
        })""",
    )
