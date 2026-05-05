"""Tests for extracted crate2nix Tauri helper scripts."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

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
    rewritten_payload = json.loads(nested_payload.read_text(encoding="utf-8"))
    original_payload = json.loads(payload_file.read_text(encoding="utf-8"))
    assert isinstance(rewritten_payload, list)
    assert isinstance(rewritten_payload[0], str)
    assert rewritten_payload != original_payload
    assert rewritten_payload[1] == "relative-entry"
    assert Path(rewritten_payload[0]).parent.name == "tauri_json-files"


def test_env_rewrite_helper_treats_linux_build_dir_as_ephemeral() -> None:
    """Linux remote builders expose crate build outputs under /build."""
    helper = _load_module(
        "lib/crate2nix_tauri_env_rewrite.py",
        "_crate2nix_tauri_env_rewrite_linux_build_dir",
    )

    assert "/build/" in helper._TEMP_SOURCE_PREFIXES


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


def test_env_rewrite_helper_supports_file_copy_and_non_temp_inputs(
    tmp_path: Path,
) -> None:
    """Only temp absolute paths should be materialized, with file copies preserved."""
    helper = _load_module(
        "lib/crate2nix_tauri_env_rewrite.py",
        "_crate2nix_tauri_env_rewrite_file",
    )
    helper._TEMP_SOURCE_PREFIXES = (f"{tmp_path}/temp/",)

    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    env_file = tmp_path / "env"
    source_root = tmp_path / "temp"
    source_root.mkdir()
    source_file = source_root / "value.txt"
    source_file.write_text("demo", encoding="utf-8")
    env_file.write_text(
        "\n".join([
            f"export TAURI:FILE={source_file}",
            "export KEEP_RELATIVE=relative/path",
            f"export KEEP_ABSOLUTE={tmp_path / 'stable.txt'}",
            "not an export",
        ])
        + "\n",
        encoding="utf-8",
    )

    helper.rewrite_env_file(env_file, metadata_dir)

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "not an export"
    rewritten = dict(
        line.removeprefix("export ").split("=", 1)
        for line in lines
        if line.startswith("export ")
    )
    assert rewritten["KEEP_RELATIVE"] == "relative/path"
    assert rewritten["KEEP_ABSOLUTE"] == str(tmp_path / "stable.txt")
    rewritten_file = Path(rewritten["TAURI_FILE"])
    assert rewritten_file.read_text(encoding="utf-8") == "demo"
    assert rewritten_file.name == "tauri_file-value.txt"


def test_env_rewrite_helper_skips_missing_env_files(tmp_path: Path) -> None:
    """Rewriting a missing env file should be a quiet no-op."""
    helper = _load_module(
        "lib/crate2nix_tauri_env_rewrite.py",
        "_crate2nix_tauri_env_rewrite_missing_env",
    )

    helper.rewrite_env_file(tmp_path / "missing.env", tmp_path / "metadata")
    assert not (tmp_path / "metadata").exists()


def test_env_rewrite_helper_replaces_existing_materialized_directories(
    tmp_path: Path,
) -> None:
    """Directory copies should replace stale metadata directories before recopying."""
    helper = _load_module(
        "lib/crate2nix_tauri_env_rewrite.py",
        "_crate2nix_tauri_env_rewrite_replace_dir",
    )

    source = tmp_path / "source"
    source.mkdir()
    (source / "fresh.txt").write_text("fresh", encoding="utf-8")

    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    destination = metadata_dir / "demo-source"
    destination.mkdir()
    (destination / "stale.txt").write_text("stale", encoding="utf-8")

    helper._copy_path(source, destination)

    assert not (destination / "stale.txt").exists()
    assert (destination / "fresh.txt").read_text(encoding="utf-8") == "fresh"


def test_env_rewrite_helper_ignores_invalid_nested_json_payloads(
    tmp_path: Path,
) -> None:
    """Nested JSON rewriting should ignore unreadable or unsupported payloads."""
    helper = _load_module(
        "lib/crate2nix_tauri_env_rewrite.py",
        "_crate2nix_tauri_env_rewrite_invalid_json",
    )
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()

    invalid = metadata_dir / "invalid.json"
    invalid.write_text("{not-json}", encoding="utf-8")
    helper._rewrite_nested_json_file(invalid, metadata_dir, "invalid")
    assert invalid.read_text(encoding="utf-8") == "{not-json}"

    non_string_list = metadata_dir / "mixed.json"
    non_string_list.write_text(json.dumps(["ok", 1]), encoding="utf-8")
    helper._rewrite_nested_json_file(non_string_list, metadata_dir, "mixed")
    assert json.loads(non_string_list.read_text(encoding="utf-8")) == ["ok", 1]

    missing_path_list = metadata_dir / "missing.json"
    missing_path_list.write_text(
        json.dumps(["/missing/file", "relative"]), encoding="utf-8"
    )
    helper._rewrite_nested_json_file(missing_path_list, metadata_dir, "missing")
    assert json.loads(missing_path_list.read_text(encoding="utf-8")) == [
        "/missing/file",
        "relative",
    ]


def test_env_rewrite_helper_main_reads_environment(monkeypatch, tmp_path: Path) -> None:
    """Main should resolve env vars, create metadata, and rewrite both env files."""
    helper = _load_module(
        "lib/crate2nix_tauri_env_rewrite.py",
        "_crate2nix_tauri_env_rewrite_main",
    )
    metadata_dir = tmp_path / "metadata"
    out_file = tmp_path / "out.env"
    lib_file = tmp_path / "lib.env"
    out_file.write_text("export KEEP=value\n", encoding="utf-8")
    lib_file.write_text("export KEEP=other\n", encoding="utf-8")

    monkeypatch.setenv("TAURI_ENV_METADATA_DIR", str(metadata_dir))
    monkeypatch.setenv("TAURI_ENV_OUT", str(out_file))
    monkeypatch.setenv("TAURI_ENV_LIB", str(lib_file))

    assert helper.main() == 0
    assert metadata_dir.is_dir()
    assert out_file.read_text(encoding="utf-8") == "export KEEP=value\n"
    assert lib_file.read_text(encoding="utf-8") == "export KEEP=other\n"


def test_env_rewrite_helper_env_path_requires_non_empty_values(monkeypatch) -> None:
    """Required env vars should reject both missing and empty values."""
    helper = _load_module(
        "lib/crate2nix_tauri_env_rewrite.py",
        "_crate2nix_tauri_env_rewrite_env_path",
    )

    monkeypatch.delenv("TAURI_ENV_OUT", raising=False)
    with pytest.raises(
        RuntimeError, match="Missing required environment variable TAURI_ENV_OUT"
    ):
        helper._env_path("TAURI_ENV_OUT")

    monkeypatch.setenv("TAURI_ENV_OUT", "")
    with pytest.raises(
        RuntimeError, match="Missing required environment variable TAURI_ENV_OUT"
    ):
        helper._env_path("TAURI_ENV_OUT")


def test_acl_build_patch_helper_resolve_and_main(tmp_path: Path, monkeypatch) -> None:
    """Path resolution and main should patch the first matching build.rs file."""
    helper = _load_module(
        "lib/crate2nix_tauri_patch_acl_build.py",
        "_crate2nix_tauri_patch_acl_build_main",
    )
    source_dir = tmp_path / "src" / "acl"
    source_dir.mkdir(parents=True)
    build_rs = source_dir / "build.rs"

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
    original = (
        'const CORE_PLUGIN_PERMISSIONS_TOKEN: &str = "__CORE_PLUGIN__";\n'
        + permission_lines
        + schema_lines
        + "      .map(|v| {\n"
        + "        v.strip_suffix(CORE_PLUGIN_PERMISSIONS_TOKEN)\n"
        + '          .and_then(|v| v.strip_prefix("TAURI_"))\n'
        + "          .unwrap_or(v)\n"
        + "      })\n"
    )
    build_rs.write_text(
        original,
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    assert helper.resolve_path() == Path("src/acl/build.rs")
    assert helper.main() == 0
    assert build_rs.read_text(encoding="utf-8") == helper.patch_text(
        original,
        path=Path("src/acl/build.rs"),
    )


def test_acl_build_patch_helper_errors_are_explicit(
    tmp_path: Path, monkeypatch
) -> None:
    """Missing files or snippets should raise SystemExit with useful messages."""
    helper = _load_module(
        "lib/crate2nix_tauri_patch_acl_build.py",
        "_crate2nix_tauri_patch_acl_build_errors",
    )

    with pytest.raises(SystemExit, match="expected snippet not found"):
        helper.patch_text("missing", path=Path("build.rs"))

    monkeypatch.chdir(tmp_path)
    with pytest.raises(
        SystemExit, match="expected tauri-utils ACL build.rs path not found"
    ):
        helper.resolve_path()
