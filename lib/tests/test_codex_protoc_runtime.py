"""Semantic contracts for Codex's vendored protoc runtime data."""

from nix_manipulator.expressions.function.definition import FunctionDefinition

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT

_PACKAGE_PATH = REPO_ROOT / "packages/codex/default.nix"


def _package_scope() -> list[object]:
    package = expect_instance(
        parse_nix_expr(_PACKAGE_PATH.read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    return package.output.scope


def test_vendored_protoc_platform_crates_keep_runtime_data_in_their_output() -> None:
    """Compiled path helpers must never retain a transient producer build path."""
    override = expect_binding(
        _package_scope(),
        "protocBinVendoredPlatformOverride",
    ).value

    assert_nix_ast_equal(
        override,
        """attrs: {
          preBuild = (attrs.preBuild or "") + ''
            export CARGO_MANIFEST_DIR="$lib/share/${attrs.crateName}"
          '';
          postInstall = (attrs.postInstall or "") + ''
            mkdir -p "$lib/share/${attrs.crateName}"
            cp -R bin include "$lib/share/${attrs.crateName}/"
          '';
        }""",
    )


def test_every_vendored_protoc_platform_uses_the_runtime_data_override() -> None:
    """Generated platform crates must automatically receive the runtime fix."""
    scope = _package_scope()

    assert_nix_ast_equal(
        expect_binding(scope, "protocBinVendoredPlatformCrates").value,
        """map
          (dependency: cargoNix.internal.crates.${dependency.packageId}.crateName)
          cargoNix.internal.crates."protoc-bin-vendored".dependencies""",
    )
    assert_nix_ast_equal(
        expect_binding(scope, "protocBinVendoredPlatformOverrides").value,
        "lib.genAttrs protocBinVendoredPlatformCrates (_: protocBinVendoredPlatformOverride)",
    )

    crate_overrides = expect_binding(scope, "crateOverrides").value
    assert_nix_ast_equal(
        crate_overrides,
        """pkgs.defaultCrateOverrides
        // protocBinVendoredPlatformOverrides
        // {
          codex-app-server-protocol = codexLinuxLowMemoryOverride;
          crossterm = crosstermOverride;
          codex-linux-sandbox = codexLinuxSandboxOverride;
          rmcp = rmcpOverride;
          runfiles = runfilesOverride;
          v8 = v8Build.mkCrateOverride;
          webrtc-sys = webrtcSysOverride;
        }
        // lib.optionalAttrs needsCoreNodeVersionPatch {
          codex-core = codexCoreOverride;
        }""",
    )
