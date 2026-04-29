"""Patch rusty_v8 build.rs to accept a Nix-built GN output directory."""

from __future__ import annotations

import sys
from pathlib import Path

_EXPECTED_ARGC = 2
_USAGE = "usage: patch_build_rs_prebuilt.py <build.rs path>"
_MISSING_ENV_ANCHOR = "expected RUSTY_V8_SRC_BINDING_PATH env list entry not found"
_MISSING_PREBUILT_ANCHOR = "expected prebuilt V8 branch not found"

_ENV_NEEDLE = '    "RUSTY_V8_SRC_BINDING_PATH",\n'
_ENV_REPLACEMENT = '    "RUSTY_V8_SRC_BINDING_PATH",\n    "RUSTY_V8_PREBUILT_GN_OUT",\n'

_PREBUILT_NEEDLE = (
    "  print_prebuilt_src_binding_path();\n\n  download_static_lib_binaries();\n"
)
_PREBUILT_REPLACEMENT = """  if let Ok(prebuilt_gn_out) = env::var("RUSTY_V8_PREBUILT_GN_OUT") {
    let prebuilt_gn_out = PathBuf::from(prebuilt_gn_out);
    let local_gn_out = build_dir().join("gn_out");
    fs::create_dir_all(&local_gn_out).unwrap();
    fs::copy(prebuilt_gn_out.join("project.json"), local_gn_out.join("project.json")).unwrap();
    if let Ok(args_gn) = fs::read(prebuilt_gn_out.join("args.gn")) {
      fs::write(local_gn_out.join("args.gn"), args_gn).unwrap();
    }
    build_binding();
  } else {
    print_prebuilt_src_binding_path();
  }

  download_static_lib_binaries();
"""


def patch_build_rs(build_rs: Path) -> None:
    """Patch one rusty_v8 build.rs file in place."""
    text = build_rs.read_text(encoding="utf-8")
    if _ENV_NEEDLE not in text:
        raise SystemExit(_MISSING_ENV_ANCHOR)
    text = text.replace(_ENV_NEEDLE, _ENV_REPLACEMENT, 1)

    if _PREBUILT_NEEDLE not in text:
        raise SystemExit(_MISSING_PREBUILT_ANCHOR)
    text = text.replace(_PREBUILT_NEEDLE, _PREBUILT_REPLACEMENT, 1)

    build_rs.write_text(text, encoding="utf-8")


def main() -> int:
    """Patch the requested build.rs file."""
    if len(sys.argv) != _EXPECTED_ARGC:
        raise SystemExit(_USAGE)
    patch_build_rs(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
