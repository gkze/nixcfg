"""Tests for extracted crate2nix Tauri helper scripts."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_module(relative_path: str, module_name: str) -> ModuleType:
    return load_module_from_path(Path(REPO_ROOT / relative_path), module_name)


def test_env_rewrite_helper_materializes_temp_paths_and_normalizes_exports(
    tmp_path: Path,
) -> None:
    """Rewrite crate2nix env exports into stable metadata paths."""
    helper = _load_module(
        "lib/crate2nix_tauri_env_rewrite.py",
        "_crate2nix_tauri_env_rewrite",
    )

    helper._TEMP_SOURCE_PREFIXES = (f"{tmp_path}/",)

    metadata_dir = tmp_path / "metadata"
    env_file = tmp_path / "env"
    source_dir = tmp_path / "source-dir"
    source_dir.mkdir()
    (source_dir / "value.txt").write_text("demo", encoding="utf-8")
    nested_file = tmp_path / "nested.txt"
    nested_file.write_text("nested", encoding="utf-8")
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(
        f'["{nested_file}","relative-entry"]',
        encoding="utf-8",
    )
    env_file.write_text(
        "\n".join([
            f'export TAURI:DIR="{source_dir}"',
            f'export TAURI:JSON="{payload_file}"',
            "export KEEP=value",
        ])
        + "\n",
        encoding="utf-8",
    )

    helper.rewrite_env_file(env_file, metadata_dir)

    lines = env_file.read_text(encoding="utf-8").splitlines()
    rewritten = dict(line.removeprefix("export ").split("=", 1) for line in lines)
    assert rewritten["KEEP"] == "value"

    dir_value = rewritten["TAURI_DIR"].strip('"')
    json_value = rewritten["TAURI_JSON"].strip('"')
    assert Path(dir_value).is_dir()
    assert Path(dir_value).name == f"tauri_dir-{source_dir.name}"
    assert Path(json_value).is_file()
    assert Path(json_value).name == f"tauri_json-{payload_file.name}"

    nested_payload = Path(json_value)
    assert nested_payload.exists()
    assert nested_payload.read_text(encoding="utf-8") != payload_file.read_text(
        encoding="utf-8"
    )
    assert "relative-entry" in nested_payload.read_text(encoding="utf-8")
    assert "tauri_json-files" in nested_payload.read_text(encoding="utf-8")


def test_acl_build_patch_helper_rewrites_tauri_env_key_round_trip() -> None:
    """Patch tauri-utils ACL build.rs with reversible colon normalization."""
    helper = _load_module(
        "lib/crate2nix_tauri_patch_acl_build.py",
        "_crate2nix_tauri_patch_acl_build",
    )

    permission_lines = (
        '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n'
        "    println!(\n"
        '      "cargo:{plugin_name}'
        '{CORE_PLUGIN_PERMISSIONS_TOKEN}_{PERMISSION_FILES_PATH_KEY}={}",\n'
        "      permission_files_path.display()\n"
        "    );\n"
    )
    schema_lines = (
        '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n'
        "    println!(\n"
        '      "cargo:{plugin_name}'
        '{CORE_PLUGIN_PERMISSIONS_TOKEN}_{GLOBAL_SCOPE_SCHEMA_PATH_KEY}={}",\n'
        "      path.display()\n"
        "    );\n"
    )
    sample = (
        'const CORE_PLUGIN_PERMISSIONS_TOKEN: &str = "__CORE_PLUGIN__";\n'
        + permission_lines
        + schema_lines
        + "      .map(|v| {\n"
        "        v.strip_suffix(CORE_PLUGIN_PERMISSIONS_TOKEN)\n"
        '          .and_then(|v| v.strip_prefix("TAURI_"))\n'
        "          .unwrap_or(v)\n"
        "      })\n"
    )

    patched = helper.patch_text(sample, path=Path("build.rs"))

    assert 'ENV_KEY_COLON_TOKEN: &str = "__TAURI_COLON__";' in patched
    assert "plugin_name.replace(':', ENV_KEY_COLON_TOKEN)" in patched
    assert '.replace(ENV_KEY_COLON_TOKEN, ":")' in patched
