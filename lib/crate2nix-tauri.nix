{ lib }:
let
  tauriPluginEnvCrateNames = [
    "tauri-plugin-clipboard-manager"
    "tauri-plugin-decorum"
    "tauri-plugin-deep-link"
    "tauri-plugin-dialog"
    "tauri-plugin-fs"
    "tauri-plugin-http"
    "tauri-plugin-notification"
    "tauri-plugin-opener"
    "tauri-plugin-os"
    "tauri-plugin-process"
    "tauri-plugin-shell"
    "tauri-plugin-single-instance"
    "tauri-plugin-store"
    "tauri-plugin-updater"
    "tauri-plugin-window-state"
  ];

  mkCrate2nixTauriEnvOverride =
    { pkgs }:
    attrs: {
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ pkgs.python3 ];

      postFixup = (attrs.postFixup or "") + ''
        export TAURI_ENV_OUT="$out/env"
        export TAURI_ENV_LIB="$lib/env"
        export TAURI_ENV_METADATA_DIR="$lib/lib/${attrs.crateName or "crate"}.env"
        ${pkgs.python3}/bin/python3 - <<'PY'
        import json
        import os
        import re
        import shutil
        from pathlib import Path

        metadata_dir = Path(os.environ["TAURI_ENV_METADATA_DIR"])
        metadata_dir.mkdir(parents=True, exist_ok=True)

        for env_path in (Path(os.environ["TAURI_ENV_OUT"]), Path(os.environ["TAURI_ENV_LIB"])):
            if not env_path.exists():
                continue
            lines = []
            for line in env_path.read_text().splitlines():
                match = re.match(r'^(export\s+)([^=]+)(=.*)$', line)
                if match is None:
                    lines.append(line)
                    continue
                prefix, name, suffix = match.groups()
                raw_value = suffix[1:]
                quoted = raw_value.startswith('"') and raw_value.endswith('"')
                value = raw_value[1:-1] if quoted else raw_value
                source_path = Path(value)
                if source_path.is_absolute() and source_path.exists() and (
                    str(source_path).startswith("/nix/var/nix/builds/")
                    or str(source_path).startswith("/private/tmp/")
                    or str(source_path).startswith("/tmp/")
                ):
                    dest = metadata_dir / f"{name.replace(':', '_').lower()}-{source_path.name}"
                    if source_path.is_dir():
                        if dest.exists():
                            shutil.rmtree(dest)
                        shutil.copytree(source_path, dest)
                    else:
                        shutil.copy2(source_path, dest)
                        try:
                            payload = json.loads(dest.read_text())
                        except Exception:
                            payload = None
                        if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
                            rewritten = []
                            nested_dir = metadata_dir / f"{name.replace(':', '_').lower()}-files"
                            nested_dir.mkdir(parents=True, exist_ok=True)
                            for item in payload:
                                nested_source = Path(item)
                                if nested_source.is_absolute() and nested_source.exists():
                                    nested_dest = nested_dir / nested_source.name
                                    if nested_source.is_dir():
                                        if nested_dest.exists():
                                            shutil.rmtree(nested_dest)
                                        shutil.copytree(nested_source, nested_dest)
                                    else:
                                        shutil.copy2(nested_source, nested_dest)
                                    rewritten.append(str(nested_dest))
                                else:
                                    rewritten.append(item)
                            dest.write_text(json.dumps(rewritten))
                    value = str(dest)
                suffix = f'="{value}"' if quoted else f'={value}'
                lines.append(f"{prefix}{name.replace(':', '_')}{suffix}")
            env_path.write_text("\n".join(lines) + "\n")
        PY
      '';
    };

  mkCrate2nixTauriUtilsOverride =
    { pkgs }:
    attrs: {
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ pkgs.python3 ];

      postPatch = (attrs.postPatch or "") + ''
        ${pkgs.python3}/bin/python3 - <<'PY'
        from pathlib import Path

        candidates = [
            Path("src/acl/build.rs"),
            Path("crates/tauri-utils/src/acl/build.rs"),
        ]
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            raise SystemExit("expected tauri-utils ACL build.rs path not found")
        text = path.read_text()
        replacements = [
            (
                'const CORE_PLUGIN_PERMISSIONS_TOKEN: &str = "__CORE_PLUGIN__";\n',
                'const CORE_PLUGIN_PERMISSIONS_TOKEN: &str = "__CORE_PLUGIN__";\nconst ENV_KEY_COLON_TOKEN: &str = "__TAURI_COLON__";\n',
            ),
            (
                '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n    println!(\n      "cargo:{plugin_name}{CORE_PLUGIN_PERMISSIONS_TOKEN}_{PERMISSION_FILES_PATH_KEY}={}",\n      permission_files_path.display()\n    );\n',
                '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n    let plugin_name = plugin_name.replace(\':\', ENV_KEY_COLON_TOKEN);\n    println!(\n      "cargo:{plugin_name}{CORE_PLUGIN_PERMISSIONS_TOKEN}_{PERMISSION_FILES_PATH_KEY}={}",\n      permission_files_path.display()\n    );\n',
            ),
            (
                '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n    println!(\n      "cargo:{plugin_name}{CORE_PLUGIN_PERMISSIONS_TOKEN}_{GLOBAL_SCOPE_SCHEMA_PATH_KEY}={}",\n      path.display()\n    );\n',
                '  if let Some(plugin_name) = pkg_name.strip_prefix("tauri:") {\n    let plugin_name = plugin_name.replace(\':\', ENV_KEY_COLON_TOKEN);\n    println!(\n      "cargo:{plugin_name}{CORE_PLUGIN_PERMISSIONS_TOKEN}_{GLOBAL_SCOPE_SCHEMA_PATH_KEY}={}",\n      path.display()\n    );\n',
            ),
            (
                '      .map(|v| {\n        v.strip_suffix(CORE_PLUGIN_PERMISSIONS_TOKEN)\n          .and_then(|v| v.strip_prefix("TAURI_"))\n          .unwrap_or(v)\n      })\n',
                '      .map(|v| {\n        v.strip_suffix(CORE_PLUGIN_PERMISSIONS_TOKEN)\n          .and_then(|v| v.strip_prefix("TAURI_"))\n          .unwrap_or(v)\n          .replace(ENV_KEY_COLON_TOKEN, ":")\n      })\n',
            ),
        ]

        for old, new in replacements:
            if old not in text:
                raise SystemExit(f"expected snippet not found in {path}")
            text = text.replace(old, new)

        path.write_text(text)
        PY
      '';
    };

  mkCrate2nixTauriOverrides =
    {
      pkgs,
      pluginCrates ? tauriPluginEnvCrateNames,
      patchTauriUtils ? true,
    }:
    let
      tauriEnvOverride = mkCrate2nixTauriEnvOverride { inherit pkgs; };
    in
    {
      tauri = tauriEnvOverride;
    }
    // lib.genAttrs pluginCrates (_: tauriEnvOverride)
    // lib.optionalAttrs patchTauriUtils {
      tauri-utils = mkCrate2nixTauriUtilsOverride { inherit pkgs; };
    };
in
{
  inherit
    mkCrate2nixTauriEnvOverride
    mkCrate2nixTauriOverrides
    mkCrate2nixTauriUtilsOverride
    tauriPluginEnvCrateNames
    ;
}
