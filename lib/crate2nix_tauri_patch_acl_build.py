"""Patch tauri-utils ACL build.rs to survive crate2nix env var normalization."""

from __future__ import annotations

from pathlib import Path

_CANDIDATES = (
    Path("src/acl/build.rs"),
    Path("crates/tauri-utils/src/acl/build.rs"),
)
_REPLACEMENTS = (
    (
        'const CORE_PLUGIN_PERMISSIONS_TOKEN: &str = "__CORE_PLUGIN__";\n',
        'const CORE_PLUGIN_PERMISSIONS_TOKEN: &str = "__CORE_PLUGIN__";\n'
        'const ENV_KEY_COLON_TOKEN: &str = "__TAURI_COLON__";\n',
    ),
    (
        '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n'
        "    println!(\n"
        '      "cargo:{plugin_name}'
        '{CORE_PLUGIN_PERMISSIONS_TOKEN}_{PERMISSION_FILES_PATH_KEY}={}",\n'
        "      permission_files_path.display()\n"
        "    );\n",
        '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n'
        "    let plugin_name = plugin_name.replace(':', ENV_KEY_COLON_TOKEN);\n"
        "    println!(\n"
        '      "cargo:{plugin_name}'
        '{CORE_PLUGIN_PERMISSIONS_TOKEN}_{PERMISSION_FILES_PATH_KEY}={}",\n'
        "      permission_files_path.display()\n"
        "    );\n",
    ),
    (
        '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n'
        "    println!(\n"
        '      "cargo:{plugin_name}'
        '{CORE_PLUGIN_PERMISSIONS_TOKEN}_{GLOBAL_SCOPE_SCHEMA_PATH_KEY}={}",\n'
        "      path.display()\n"
        "    );\n",
        '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n'
        "    let plugin_name = plugin_name.replace(':', ENV_KEY_COLON_TOKEN);\n"
        "    println!(\n"
        '      "cargo:{plugin_name}'
        '{CORE_PLUGIN_PERMISSIONS_TOKEN}_{GLOBAL_SCOPE_SCHEMA_PATH_KEY}={}",\n'
        "      path.display()\n"
        "    );\n",
    ),
    (
        "      .map(|v| {\n"
        "        v.strip_suffix(CORE_PLUGIN_PERMISSIONS_TOKEN)\n"
        '          .and_then(|v| v.strip_prefix("TAURI_"))\n'
        "          .unwrap_or(v)\n"
        "      })\n",
        "      .map(|v| {\n"
        "        v.strip_suffix(CORE_PLUGIN_PERMISSIONS_TOKEN)\n"
        '          .and_then(|v| v.strip_prefix("TAURI_"))\n'
        "          .unwrap_or(v)\n"
        '          .replace(ENV_KEY_COLON_TOKEN, ":")\n'
        "      })\n",
    ),
)


def resolve_path() -> Path:
    """Return the first supported tauri-utils ACL build.rs path."""
    for candidate in _CANDIDATES:
        if candidate.exists():
            return candidate
    msg = "expected tauri-utils ACL build.rs path not found"
    raise SystemExit(msg)


def patch_text(text: str, *, path: Path) -> str:
    """Apply the ACL env-var normalization patch to one build.rs payload."""
    patched = text
    for old, new in _REPLACEMENTS:
        if old not in patched:
            msg = f"expected snippet not found in {path}"
            raise SystemExit(msg)
        patched = patched.replace(old, new)
    return patched


def main() -> int:
    """Patch the tauri-utils ACL build.rs file in the current working tree."""
    path = resolve_path()
    path.write_text(patch_text(path.read_text(), path=path))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
