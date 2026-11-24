"""The crate builder's compiler and output check are explicit Nix."""

from lib.tests._nix_ast import parse_nix_expr
from lib.tests._nix_source import nix_file_binding_expr

_OVERLAY = "overlays/_lib/build-rust-crate/default.nix"


def test_crate_toolchain_builds_rust_1_99_with_llvm_23() -> None:
    """Rust 1.99 must see LLVM 23, including the sets llvmSharedFor reads."""
    assert nix_file_binding_expr(_OVERLAY, "withLlvm23") == parse_nix_expr(
        "ps: ps // { llvmPackages = ps.llvmPackages_23; }"
    )
    assert nix_file_binding_expr(_OVERLAY, "llvmPackages") == parse_nix_expr(
        "ps.llvmPackages_23"
    )
    assert nix_file_binding_expr(
        _OVERLAY, "llvmPackages", occurrence=1
    ) == parse_nix_expr("pkgSet.llvmPackages_23")
    for name in (
        "pkgsBuildBuild",
        "pkgsBuildHost",
        "pkgsBuildTarget",
        "pkgsHostTarget",
    ):
        assert nix_file_binding_expr(_OVERLAY, name) == parse_nix_expr(
            f"withLlvm23 pkgSet.{name}"
        )
    assert nix_file_binding_expr(_OVERLAY, "toolchain") == parse_nix_expr(
        "llvm23Toolchain (buildPlatformFor prev)"
    )


def test_crate_outputs_keep_the_metadata_trailer() -> None:
    """A published rlib has to survive the decoder, including after fixup."""
    assert nix_file_binding_expr(_OVERLAY, "dontStrip") == parse_nix_expr("true")
    post_install = nix_file_binding_expr(_OVERLAY, "postInstall")
    post_fixup = nix_file_binding_expr(_OVERLAY, "postFixup")
    assert post_install == parse_nix_expr('(old.postInstall or "") + crateOutputCheck')
    assert post_fixup == parse_nix_expr('(old.postFixup or "") + crateOutputCheck')
