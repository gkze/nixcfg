"""Semantic contracts for Buzz's source-built Rust sidecars."""

import os
import subprocess
from functools import cache
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.scope import Scope

_SIDECARS_PATH = REPO_ROOT / "packages/buzz/native/sidecars.nix"
_BUZZ_PACKAGE_PATH = REPO_ROOT / "packages/buzz/package.nix"


@cache
def _sidecars_package() -> tuple[FunctionDefinition, FunctionCall]:
    package = expect_instance(
        parse_nix_expr(_SIDECARS_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        output = output.body
    return package, expect_instance(output, FunctionCall)


@cache
def _buzz_package() -> tuple[FunctionDefinition, IfExpression]:
    package = expect_instance(
        parse_nix_expr(_BUZZ_PACKAGE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    output = package.output
    while isinstance(output, Assertion):
        output = output.body
    return package, expect_instance(output, IfExpression)


def _derivation_arguments() -> AttributeSet:
    _package, derivation = _sidecars_package()
    return expect_instance(derivation.argument, AttributeSet)


def _package_scope() -> Scope:
    package, _derivation = _sidecars_package()
    output = package.output
    while isinstance(output, Assertion):
        if output.scope:
            return output.scope
        output = output.body
    raise AssertionError("expected sidecar package let-bindings")


def _assertion_conditions() -> list[object]:
    package, _derivation = _sidecars_package()
    conditions: list[object] = []
    output = package.output
    while isinstance(output, Assertion):
        conditions.append(output.expression)
        output = output.body
    return conditions


def _validation_script() -> str:
    _package, derivation = _sidecars_package()
    script = expect_instance(
        expect_binding(derivation.scope, "validationScript").value,
        IndentedString,
    )
    return dedent(indented_string_body(script.rebuild()))


def _write_tool(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _sidecar_output(root: Path) -> Path:
    output = root / "result"
    binary_root = output / "bin"
    binary_root.mkdir(parents=True)
    for name in (
        "buzz-acp-aarch64-apple-darwin",
        "buzz-agent-aarch64-apple-darwin",
        "buzz-backend-kubernetes-aarch64-apple-darwin",
        "buzz-dev-mcp-aarch64-apple-darwin",
        "git-credential-nostr-aarch64-apple-darwin",
        "buzz-aarch64-apple-darwin",
    ):
        binary = binary_root / name
        binary.write_bytes(b"fixture")
        binary.chmod(0o755)
    return output


def _validator_tools(root: Path) -> dict[str, Path]:
    tools = root / "tools"
    tools.mkdir()
    paths = {name: tools / name for name in ("codesign", "file", "lipo", "otool")}
    _write_tool(
        paths["file"],
        'printf "%s: %s\\n" "$1" "${FAKE_FILE_FORMAT:-Mach-O 64-bit executable arm64}"',
    )
    _write_tool(
        paths["lipo"],
        'printf "%s\\n" "${FAKE_ARCHITECTURES:-arm64}"',
    )
    _write_tool(
        paths["otool"],
        """test "${FAKE_OTOOL_EXIT:-0}" -eq 0 || exit "$FAKE_OTOOL_EXIT"
printf "%s:\\n" "$2"
printf "\\t%s (compatibility version 1.0.0, current version 1.0.0)\\n" \\
  "${FAKE_DEPENDENCY:-/usr/lib/libSystem.B.dylib}"
""",
    )
    _write_tool(
        paths["codesign"],
        """case " $* " in
  *" --verify "*) exit "${FAKE_VERIFY_EXIT:-0}" ;;
  *" -dv "*) printf "Signature=%s\\n" "${FAKE_SIGNATURE:-adhoc}" >&2 ;;
  *) exit 64 ;;
esac""",
    )
    return paths


def _run_validator(
    tmp_path: Path,
    *,
    environment: dict[str, str] | None = None,
    extra_binary: str | None = None,
) -> subprocess.CompletedProcess[str]:
    output = _sidecar_output(tmp_path)
    if extra_binary is not None:
        unexpected = output / "bin" / extra_binary
        unexpected.write_bytes(b"unexpected")
        unexpected.chmod(0o755)
    tools = _validator_tools(tmp_path)
    env = os.environ | {
        "out": str(output),
        "CODESIGN_TOOL": str(tools["codesign"]),
        "FILE_TOOL": str(tools["file"]),
        "LIPO_TOOL": str(tools["lipo"]),
        "OTOOL_TOOL": str(tools["otool"]),
    }
    if environment is not None:
        env.update(environment)
    return subprocess.run(
        ["/bin/bash", "-eu"],
        input=_validation_script(),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_sidecar_contract_is_derived_from_updater_owned_identity() -> None:
    """The module must combine updater-owned identity with its exact outputs."""
    package, _derivation = _sidecars_package()
    assert {
        expect_instance(argument, Identifier).name for argument in package.argument_set
    } == {
        "cctools",
        "lib",
        "makeRustPlatform",
        "nativeLock",
        "patchedBuzzSource",
        "rootCargoDeps",
        "rustToolchain",
        "sidecarSpecs",
        "stdenv",
        "version",
    }

    contract = expect_binding(_derivation_arguments().values, "passthru").value
    contract = expect_instance(contract, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(contract.values, "buzzNativeContract").value,
        """implementedContract""",
    )

    _package, derivation = _sidecars_package()
    assert_nix_ast_equal(
        expect_binding(_package_scope(), "buzzCommit").value,
        "nativeLock.buzz.commit or null",
    )
    assert_nix_ast_equal(
        expect_binding(_package_scope(), "rustVersion").value,
        "nativeLock.buzz.rustVersion or null",
    )
    assert_nix_ast_equal(
        expect_binding(derivation.scope, "implementedContract").value,
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


def test_sidecars_use_the_patched_source_root_vendor_and_rust_1_95() -> None:
    """The build closure must cross only the three repo-owned Rust inputs."""
    _package, derivation = _sidecars_package()
    attrs = _derivation_arguments()

    assert_nix_ast_equal(
        expect_binding(derivation.scope, "sidecarsRustPlatform").value,
        """makeRustPlatform {
          cargo = rustToolchain;
          rustc = rustToolchain;
        }""",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "src").value,
        "patchedBuzzSource",
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cargoDeps").value,
        "rootCargoDeps",
    )
    assert_nix_ast_equal(expect_binding(attrs.values, "strictDeps").value, "true")
    assert_nix_ast_equal(expect_binding(attrs.values, "buildType").value, '"release"')
    assert_nix_ast_equal(
        expect_binding(attrs.values, "env").value,
        '{ CARGO_NET_OFFLINE = "true"; }',
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "cargoBuildFlags").value,
        """[ "--frozen" ]
        ++ lib.concatMap (
          spec: [ "--package" spec.package "--bin" spec.binary ]
        ) sidecarSpecs""",
    )

    conditions = _assertion_conditions()
    expected_conditions = (
        'stdenv.hostPlatform.system == "aarch64-darwin"',
        'builtins.isString buzzCommit && builtins.match "[0-9a-f]{40}" buzzCommit != null',
        "builtins.isString rustVersion",
        """sidecarSpecs == [
          { package = "buzz-acp"; binary = "buzz-acp"; }
          { package = "buzz-agent"; binary = "buzz-agent"; }
          { package = "buzz-backend-kubernetes"; binary = "buzz-backend-kubernetes"; }
          { package = "buzz-dev-mcp"; binary = "buzz-dev-mcp"; }
          { package = "git-credential-nostr"; binary = "git-credential-nostr"; }
          { package = "buzz-cli"; binary = "buzz"; }
        ]""",
        """(rustToolchain.passthru.buzzNativeContract or null) == {
          kind = "rust-toolchain";
          channel = rustVersion;
          profile = "default";
          target = "aarch64-apple-darwin";
        }""",
    )
    assert len(conditions) == len(expected_conditions)
    for actual, expected in zip(conditions, expected_conditions, strict=True):
        assert_nix_ast_equal(actual, expected)


def test_sidecars_install_only_the_target_qualified_binary_inventory() -> None:
    """The desktop bundler must receive six unambiguous Darwin sidecar names."""
    attrs = _derivation_arguments()

    install_phase = expect_binding(attrs.values, "installPhase").value
    bindings = "lib: target: sidecarSpecs:"
    assert_nix_ast_equal(
        f"{bindings} {install_phase.rebuild()}",
        bindings
        + " "
        + r"""''
          runHook preInstall
          mkdir -p "$out/bin"
          ${lib.concatMapStringsSep "\n" (spec: ''
            install -m0755 \
              "target/${target}/release/${spec.binary}" \
              "$out/bin/${spec.binary}-${target}"
          '') sidecarSpecs}
          runHook postInstall
        ''""",
    )


def test_sidecar_validator_accepts_exact_arm64_adhoc_system_linked_output(
    tmp_path: Path,
) -> None:
    """The post-fixup validator must accept the complete intended closure."""
    result = _run_validator(tmp_path)
    assert result.returncode == 0, result.stderr

    attrs = _derivation_arguments()
    post_fixup = expect_instance(
        expect_binding(attrs.values, "postFixup").value,
        IndentedString,
    )
    shell = parse_shell(dedent(indented_string_body(post_fixup.rebuild())))
    assert command_texts(shell, "/usr/bin/codesign") == [
        '/usr/bin/codesign --force --sign - "$sidecar"'
    ]

    assert_nix_ast_equal(
        expect_binding(attrs.values, "doInstallCheck").value,
        "true",
    )
    install_check = expect_binding(attrs.values, "installCheckPhase").value
    bindings = "cctools: validationScript:"
    assert_nix_ast_equal(
        f"{bindings} {install_check.rebuild()}",
        bindings
        + " "
        + r"""''
          runHook preInstallCheck
          export FILE_TOOL=/usr/bin/file
          export LIPO_TOOL=${cctools}/bin/lipo
          export OTOOL_TOOL=${cctools}/bin/otool
          export CODESIGN_TOOL=/usr/bin/codesign
          ${validationScript}
          runHook postInstallCheck
        ''""",
    )


def test_sidecars_relocate_the_pinned_sdk_iconv_edge_before_signing() -> None:
    """Rust's SDK iconv edge must become the stable macOS system install name."""
    attrs = _derivation_arguments()
    post_fixup = expect_instance(
        expect_binding(attrs.values, "postFixup").value,
        IndentedString,
    )
    bindings = "cctools: sidecarNames:"
    assert_nix_ast_equal(
        f"{bindings} {post_fixup.rebuild()}",
        bindings
        + " "
        + r"""''
          for sidecarName in ${sidecarNames}; do
            sidecar="$out/bin/$sidecarName"
            dependencyListing="$(${cctools}/bin/otool -L "$sidecar")"
            iconvDependency=""
            while IFS= read -r dependencyLine; do
              dependency="$(printf '%s\n' "$dependencyLine" | LC_ALL=C awk '{ print $1 }')"
              case "$dependency" in
                /nix/store/*-libiconv-*/lib/libiconv.2.dylib)
                  if [ -n "$iconvDependency" ]; then
                    echo "Buzz sidecar has multiple Nix libiconv edges: $sidecarName" >&2
                    exit 1
                  fi
                  case "$dependencyLine" in
                    *' (compatibility version 7.0.0, '*) ;;
                    *)
                      echo "Buzz sidecar libiconv ABI differs from macOS: $sidecarName" >&2
                      exit 1
                      ;;
                  esac
                  iconvDependency="$dependency"
                  ;;
              esac
            done < <(printf '%s\n' "$dependencyListing" | LC_ALL=C awk 'NR > 1')
            if [ -z "$iconvDependency" ]; then
              echo "Buzz sidecar has no relocatable Nix libiconv edge: $sidecarName" >&2
              exit 1
            fi
            ${cctools}/bin/install_name_tool \
              -change "$iconvDependency" /usr/lib/libiconv.2.dylib "$sidecar"
            /usr/bin/codesign --force --sign - "$sidecar"
          done
        ''""",
    )


def test_sidecar_validator_rejects_an_unexpected_output_file(tmp_path: Path) -> None:
    """No undeclared Cargo executable may leak into the sidecar closure."""
    result = _run_validator(tmp_path, extra_binary="undeclared-sidecar")
    assert result.returncode != 0
    assert "inventory differs from the exact six binaries" in result.stderr


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        (
            {"FAKE_FILE_FORMAT": "Mach-O 64-bit executable x86_64"},
            "not an arm64 Mach-O executable",
        ),
        (
            {"FAKE_ARCHITECTURES": "arm64 x86_64"},
            "not arm64-only",
        ),
        (
            {"FAKE_DEPENDENCY": "@rpath/libforbidden.dylib"},
            "forbidden dynamic-library edge",
        ),
        (
            {"FAKE_OTOOL_EXIT": "1"},
            "could not inspect dynamic-library edges",
        ),
        (
            {"FAKE_SIGNATURE": "Developer ID Application"},
            "does not have an ad-hoc signature",
        ),
    ],
)
def test_sidecar_validator_rejects_nonportable_or_nonadhoc_binaries(
    tmp_path: Path,
    environment: dict[str, str],
    message: str,
) -> None:
    """Architecture, closure, and final-signature drift must all stop packaging."""
    result = _run_validator(tmp_path, environment=environment)
    assert result.returncode != 0
    assert message in result.stderr


def test_sidecar_validator_allows_loader_relative_edges(tmp_path: Path) -> None:
    """A sidecar may resolve an app-bundled library beside its executable."""
    result = _run_validator(
        tmp_path,
        environment={"FAKE_DEPENDENCY": "@loader_path/libbundled.dylib"},
    )
    assert result.returncode == 0, result.stderr


def test_buzz_package_owns_and_gates_the_sidecar_derivation() -> None:
    """The unexported package must own the inputs and independently gate its slot."""
    _package, derivation = _buzz_package()
    scope = derivation.scope
    contracts = expect_instance(
        expect_binding(scope, "expectedNativeContracts").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(contracts.values, "sidecars").value,
        """{
          kind = "buzz-sidecars";
          commit = expectedCommit;
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

    bindings = (
        "buzzRuntimePolicySource: cctools: lib: nativeLock: pkgs: rootCargoDeps: "
        "rustToolchainNative: sidecarSpecs: stdenv: version:"
    )
    sidecars_native = expect_binding(scope, "sidecarsNative").value
    assert_nix_ast_equal(
        f"{bindings} {sidecars_native.rebuild()}",
        f"""{bindings} import ./native/sidecars.nix {{
          inherit cctools lib nativeLock rootCargoDeps sidecarSpecs stdenv version;
          inherit (pkgs) makeRustPlatform;
          patchedBuzzSource = buzzRuntimePolicySource;
          rustToolchain = rustToolchainNative;
        }}""",
    )

    slots = expect_instance(
        expect_binding(scope, "nativeFoundationSlots").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(slots.values, "sidecars").value,
        "sidecarsNative",
    )
    passthru = expect_instance(
        expect_binding(scope, "commonPassthru").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "sidecars").value,
        "sidecarsNative",
    )
