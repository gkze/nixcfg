"""Patch V8's allocator BUILD.gn to force whole-archive linking."""

from __future__ import annotations

import sys
from pathlib import Path

_EXPECTED_ARGC = 2
_USAGE = "usage: patch_allocator_build.py <BUILD.gn path>"
_MISSING_ANCHOR = "allocator BUILD.gn anchor not found"

_NEEDLE = """if (toolchain_has_rust) {
  # All targets which depend on Rust code but are not linked by rustc must
  # depend on this. Usually, this dependency will come from a `rust_target` or
  # `cargo_crate` GN template, but note that this may be overridden by setting
  # `no_allocator_crate` of `rust_static_library` or `no_std` of `cargo_crate`.
  rust_static_library(\"allocator\") {
"""

_REPLACEMENT = """if (toolchain_has_rust) {
  # All targets which depend on Rust code but are not linked by rustc must
  # depend on this. Usually, this dependency will come from a `rust_target` or
  # `cargo_crate` GN template, but note that this may be overridden by setting
  # `no_allocator_crate` of `rust_static_library` or `no_std` of `cargo_crate`.
  config(\"allocator_alwayslink\") {
    _output_dir = rebase_path(target_out_dir, root_build_dir)
    _crate_dir = get_label_info(\":allocator\", \"dir\")
    _crate_hash = string_hash(_crate_dir)
    _crate_name = \"allocator_${_crate_hash}\"
    _rlib_path = \"${_output_dir}/lib${_crate_name}.rlib\"

    if (current_os == \"aix\") {
      # The AIX linker does not implement an option for this.
    } else if (is_win) {
      ldflags = [ \"/WHOLEARCHIVE:${_rlib_path}\" ]
    } else {
      ldflags = [ \"-LinkWrapper,add-whole-archive=${_rlib_path}\" ]
    }

    visibility = [ \":*\" ]
  }

  rust_static_library(\"allocator\") {
    all_dependent_configs = [ \":allocator_alwayslink\" ]
"""


def main() -> int:
    """Patch the provided BUILD.gn file in place."""
    if len(sys.argv) != _EXPECTED_ARGC:
        raise SystemExit(_USAGE)

    build_gn = Path(sys.argv[1])
    original = build_gn.read_text(encoding="utf-8")
    if _NEEDLE not in original:
        raise SystemExit(_MISSING_ANCHOR)

    patched = original.replace(_NEEDLE, _REPLACEMENT, 1)
    patched = patched.replace(
        "    if (!rustc_nightly_capability) {\n"
        '      rustflags += [ "--cfg=rust_allocator_no_nightly_capability" ]\n'
        "    }\n",
        """    # With our Nix-provided rustc + RUSTC_BOOTSTRAP, the legacy shim names
    # are unnecessary. Keeping both legacy and v2 variants causes rustc to emit
    # the v2 allocator shim symbols as local (`t`) instead of externally
    # resolvable symbols, which breaks the final mksnapshot link on Darwin.
""",
        1,
    )
    build_gn.write_text(patched, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
