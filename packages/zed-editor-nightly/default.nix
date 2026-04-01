{
  pkgs,
  inputs,
  lib,
  rustPlatform,
  symlinkJoin,
  runCommand,
  makeFontsConf,
  git,
  cmake,
  curl,
  perl,
  pkg-config,
  protobuf,
  xcodebuild,
  fontconfig,
  freetype,
  imagemagick,
  libicns,
  libgit2,
  openssl,
  sqlite,
  zlib,
  zstd,
  apple-sdk_15,
  darwinMinVersionHook,
  python3,
  crate2nixSourceOnly ? false,
  ...
}:
let
  pname = "zed-editor-nightly";
  version = "unstable-${inputs.zed.shortRev or (builtins.substring 0 8 inputs.zed.rev)}";
  src = inputs.zed;
  zedManifest = builtins.fromTOML (builtins.readFile "${src}/crates/zed/Cargo.toml");
  appVersion = zedManifest.package.version;
  releaseChannel = "nightly";

  pythonForSourcePrep = python3.withPackages (_: [ ]);

  copyZedManifestFor = crateName: ''
    if [ -d "$out/crates/${crateName}" ]; then
      cp "$out/crates/zed/Cargo.toml" "$out/crates/${crateName}/zed-Cargo.toml"
    fi
  '';

  patchIfExists = relPath: body: ''
    if [ -f "$out/${relPath}" ]; then
      ${body}
    fi
  '';

  preparedWorkspaceInputs = ''
    cp -r "$out/assets" "$out/crates/assets/workspace-assets"
    cp -r "$out/assets" "$out/crates/settings/workspace-assets"
    cp -r "$out/crates/extension_api/wit" "$out/crates/extension_host/workspace-extension-api-wit"
    cp -r "$out/crates/gpui" "$out/crates/gpui_macos/workspace-gpui"
    cp "$out/crates/git_ui/src/commit_message_prompt.txt" "$out/crates/prompt_store/commit_message_prompt.txt"
    cp "$out/script/uninstall.sh" "$out/crates/cli/uninstall.sh"
    cp "$out/crates/zed/RELEASE_CHANNEL" "$out/crates/release_channel/RELEASE_CHANNEL"

    if [ -d "$out/crates/edit_prediction_cli" ]; then
      if [ -d "$out/crates/grammars/src" ]; then
        cp -r "$out/crates/grammars/src" "$out/crates/edit_prediction_cli/workspace-language-configs-src"
      elif [ -d "$out/crates/languages/src" ]; then
        cp -r "$out/crates/languages/src" "$out/crates/edit_prediction_cli/workspace-language-configs-src"
      fi
    fi

    ${copyZedManifestFor "remote_server"}
    ${copyZedManifestFor "edit_prediction_cli"}
    ${copyZedManifestFor "eval"}
    ${copyZedManifestFor "eval_cli"}
  '';

  preparedWorkspacePatches = ''
    substituteInPlace "$out/crates/release_channel/src/lib.rs" \
      --replace-fail 'include_str!("../../zed/RELEASE_CHANNEL")' 'include_str!("../RELEASE_CHANNEL")'

    substituteInPlace "$out/crates/assets/src/assets.rs" \
      --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]' \
      --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};' \
      --replace-fail ".filter_map(|p| {" ".filter_map(|p: std::borrow::Cow<'static, str>| {"

    substituteInPlace "$out/crates/settings/src/settings.rs" \
      --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]' \
      --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};'

    substituteInPlace "$out/crates/prompt_store/src/prompt_store.rs" \
      --replace-fail 'include_str!("../../git_ui/src/commit_message_prompt.txt")' 'include_str!("../commit_message_prompt.txt")'

    substituteInPlace "$out/crates/extension_host/build.rs" \
      --replace-fail 'PathBuf::from("../extension_api/wit")' 'PathBuf::from("workspace-extension-api-wit")'

    for path in "$out"/crates/extension_host/src/wasm_host/wit/since_v*.rs; do
      substituteInPlace "$path" \
        --replace-fail 'path: "../extension_api/wit/' 'path: "workspace-extension-api-wit/'
    done

    ${patchIfExists "crates/remote_server/build.rs" ''
      substituteInPlace "$out/crates/remote_server/build.rs" \
        --replace-fail 'include_str!("../zed/Cargo.toml")' 'include_str!("./zed-Cargo.toml")'
    ''}

    ${patchIfExists "crates/edit_prediction_cli/build.rs" ''
      substituteInPlace "$out/crates/edit_prediction_cli/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'
    ''}

    ${patchIfExists "crates/eval/build.rs" ''
      substituteInPlace "$out/crates/eval/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'
    ''}

    ${patchIfExists "crates/eval_cli/build.rs" ''
      substituteInPlace "$out/crates/eval_cli/build.rs" \
        --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")' \
        --replace-fail 'println!("cargo:rerun-if-changed=../zed/Cargo.toml");' 'println!("cargo:rerun-if-changed=./zed-Cargo.toml");'
    ''}

    ${patchIfExists "crates/edit_prediction_cli/src/filter_languages.rs" ''
      if grep -Fq '#[folder = "../grammars/src/"]' "$out/crates/edit_prediction_cli/src/filter_languages.rs"; then
        substituteInPlace "$out/crates/edit_prediction_cli/src/filter_languages.rs" \
          --replace-fail '#[folder = "../grammars/src/"]' '#[folder = "workspace-language-configs-src/"]'
      elif grep -Fq '#[folder = "../languages/src/"]' "$out/crates/edit_prediction_cli/src/filter_languages.rs"; then
        substituteInPlace "$out/crates/edit_prediction_cli/src/filter_languages.rs" \
          --replace-fail '#[folder = "../languages/src/"]' '#[folder = "workspace-language-configs-src/"]'
      fi

      if grep -Fq 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' "$out/crates/edit_prediction_cli/src/filter_languages.rs"; then
        substituteInPlace "$out/crates/edit_prediction_cli/src/filter_languages.rs" \
          --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'
      elif grep -Fq 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' "$out/crates/edit_prediction_cli/src/filter_languages.rs"; then
        substituteInPlace "$out/crates/edit_prediction_cli/src/filter_languages.rs" \
          --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'
      fi
    ''}

    substituteInPlace "$out/crates/cli/src/main.rs" \
      --replace-fail 'include_bytes!("../../../script/uninstall.sh")' 'include_bytes!("../uninstall.sh")'

    substituteInPlace "$out/crates/inspector_ui/build.rs" \
      --replace-fail '    let mut path = std::path::PathBuf::from(&cargo_manifest_dir);' '    println!("cargo:rustc-env=ZED_REPO_DIR={}", cargo_manifest_dir);
        return;

        let mut path = std::path::PathBuf::from(&cargo_manifest_dir);'

    substituteInPlace "$out/crates/gpui_macos/build.rs" \
      --replace-fail '        gpui::GPUI_MANIFEST_DIR.into()' '        PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("workspace-gpui")'
  '';

  patchedSrc =
    runCommand "${pname}-${version}-src"
      {
        nativeBuildInputs = [ pythonForSourcePrep ];
      }
      ''
        cp -r ${src} "$out"
        chmod -R u+w "$out"

        {
          printf '# ###### THEME LICENSES ######\n\n'
          cat "$out/assets/themes/LICENSES"
          printf '\n# ###### ICON LICENSES ######\n\n'
          cat "$out/assets/icons/LICENSES"
          printf '\n# ###### CODE LICENSES ######\n\n'
          printf 'Generated in Nix packaging; cargo-about step is pending.\n'
        } > "$out/assets/licenses.md"

        printf '${releaseChannel}\n' > "$out/crates/zed/RELEASE_CHANNEL"

        ${preparedWorkspaceInputs}
        ${preparedWorkspacePatches}
      '';

  cargoNix = import ./Cargo.nix {
    inherit pkgs;
    rootSrc = patchedSrc;
  };
  cargoNixVersion = cargoNix.internal.crates.zed.version;
  cargoNixVersionCheck =
    if cargoNixVersion == appVersion then
      true
    else
      throw ''
        packages/zed-editor-nightly/Cargo.nix has zed version ${cargoNixVersion},
        expected ${appVersion}; regenerate Cargo.nix
      '';

  livekitLibwebrtc = pkgs.callPackage "${src}/nix/livekit-libwebrtc/package.nix" { };

  commonNativeBuildInputs = [
    cmake
    curl
    perl
    pkg-config
    protobuf
    rustPlatform.bindgenHook
    xcodebuild
  ];

  commonBuildInputs = [
    fontconfig
    freetype
    libgit2
    openssl
    sqlite
    zlib
    zstd
    apple-sdk_15
    (darwinMinVersionHook "10.15")
  ];

  commonCrates = [
    "acp_thread"
    "acp_tools"
    "action_log"
    "activity_indicator"
    "agent"
    "agent_servers"
    "agent_settings"
    "agent_ui"
    "askpass"
    "assets"
    "audio"
    "auto_update"
    "auto_update_helper"
    "auto_update_ui"
    "breadcrumbs"
    "call"
    "channel"
    "cli"
    "client"
    "collections"
    "collab_ui"
    "command_palette"
    "component"
    "component_preview"
    "copilot"
    "copilot_ui"
    "crashes"
    "csv_preview"
    "dap_adapters"
    "db"
    "debug_adapter_extension"
    "debugger_tools"
    "debugger_ui"
    "dev_container"
    "diagnostics"
    "edit_prediction"
    "edit_prediction_ui"
    "editor"
    "encoding_selector"
    "extension"
    "extension_host"
    "extensions_ui"
    "feature_flags"
    "feedback"
    "file_finder"
    "fs"
    "git"
    "git_graph"
    "git_hosting_providers"
    "git_ui"
    "go_to_line"
    "gpui"
    "gpui_macros"
    "gpui_platform"
    "gpui_tokio"
    "gpui_util"
    "gpui_wgpu"
    "http_client"
    "image_viewer"
    "inspector_ui"
    "install_cli"
    "journal"
    "keymap_editor"
    "language"
    "language_extension"
    "language_model"
    "language_models"
    "language_onboarding"
    "language_selector"
    "language_tools"
    "languages"
    "line_ending_selector"
    "livekit_api"
    "markdown"
    "markdown_preview"
    "media"
    "menu"
    "migrator"
    "miniprofiler_ui"
    "nc"
    "net"
    "node_runtime"
    "notifications"
    "onboarding"
    "outline"
    "outline_panel"
    "paths"
    "picker"
    "perf"
    "platform_title_bar"
    "prettier"
    "project"
    "project_panel"
    "project_symbols"
    "prompt_store"
    "proto"
    "recent_projects"
    "release_channel"
    "remote"
    "remote_connection"
    "repl"
    "reqwest_client"
    "rope"
    "search"
    "scheduler"
    "session"
    "sum_tree"
    "settings"
    "settings_profile_selector"
    "settings_ui"
    "snippet_provider"
    "snippets_ui"
    "svg_preview"
    "system_specs"
    "tab_switcher"
    "task"
    "tasks_ui"
    "telemetry"
    "telemetry_events"
    "terminal_view"
    "theme"
    "theme_extension"
    "theme_selector"
    "theme_settings"
    "time_format"
    "title_bar"
    "toolchain_selector"
    "ui"
    "ui_prompt"
    "util"
    "util_macros"
    "vim"
    "vim_mode_setting"
    "watch"
    "web_search"
    "web_search_providers"
    "which_key"
    "workspace"
    "zed_actions"
    "zed_env_vars"
    "zlog"
    "ztracing"
  ];

  commonOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ commonNativeBuildInputs;

    buildInputs = (attrs.buildInputs or [ ]) ++ commonBuildInputs;

    FONTCONFIG_FILE = makeFontsConf {
      fontDirectories = [
        "${patchedSrc}/assets/fonts/lilex"
        "${patchedSrc}/assets/fonts/ibm-plex-sans"
      ];
    };
    LK_CUSTOM_WEBRTC = livekitLibwebrtc;
    NIX_OUTPATH_USED_AS_RANDOM_SEED = "norebuilds";
    PROTOC = "${protobuf}/bin/protoc";
    RELEASE_VERSION = version;
    ZED_COMMIT_SHA = inputs.zed.rev or "";
    ZED_UPDATE_EXPLANATION = "Zed has been installed using Nix. Auto-updates have thus been disabled.";
    ZSTD_SYS_USE_PKG_CONFIG = true;
  };

  gpuiMacosOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ pkgs.rust-cbindgen ];
  };

  webrtcSysOverride = attrs: {
    dontCheckForBrokenSymlinks = true;
    postPatch = (attrs.postPatch or "") + ''
      substituteInPlace webrtc-sys/build.rs --replace-fail \
        "cargo:rustc-link-lib=static=webrtc" "cargo:rustc-link-lib=dylib=webrtc"

      substituteInPlace webrtc-sys/build.rs --replace-fail \
        'add_gio_headers(&mut builder);' \
        'for lib_name in ["glib-2.0", "gio-2.0"] {
            if let Ok(lib) = pkg_config::Config::new().cargo_metadata(false).probe(lib_name) {
                for path in lib.include_paths {
                    builder.include(&path);
                }
            }
        }'
    '';
  };

  documentedOverride = attrs: {
    postPatch = (attrs.postPatch or "") + ''
      substituteInPlace src/lib.rs \
        --replace-fail 'concat!("../", std::env!("CARGO_PKG_README"))' '"../README.md"'
    '';
  };

  rav1eOverride = _attrs: {
    CARGO_ENCODED_RUSTFLAGS = "";
  };

  wasmtimeCApiImplOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ cmake ];
  };

  treeSitterOverride =
    attrs:
    let
      wasmtimeCApiIncludeDirs =
        lib.concatMapStringsSep " " (dep: "${dep.lib}/lib/wasmtime-c-api-impl.out/include")
          (builtins.filter (dep: (dep.crateName or "") == "wasmtime-c-api-impl") (attrs.dependencies or [ ]));
    in
    {
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ python3 ];
      preConfigure = (attrs.preConfigure or "") + ''
        export DEP_WASMTIME_C_API_INCLUDE="${wasmtimeCApiIncludeDirs}"
        if [ -z "$DEP_WASMTIME_C_API_INCLUDE" ]; then
          echo "missing wasmtime-c-api-impl include path for tree-sitter" >&2
          exit 1
        fi
      '';
      postPatch = (attrs.postPatch or "") + ''
        python3 - <<'PY'
        from pathlib import Path

        path = Path("binding_rust/build.rs")
        text = path.read_text()
        old = (
            "        config\n"
            '            .define("TREE_SITTER_FEATURE_WASM", "")\n'
            '            .define("static_assert(...)", "")\n'
            '            .include(env::var("DEP_WASMTIME_C_API_INCLUDE").unwrap());\n'
        )
        new = (
            "        config\n"
            '            .define("TREE_SITTER_FEATURE_WASM", "")\n'
            '            .define("static_assert(...)", "");\n'
            '        if let Ok(include) = env::var("DEP_WASMTIME_C_API_INCLUDE") {\n'
            '            for include in include.split_whitespace() {\n'
            "                config.include(include);\n"
            "            }\n"
            "        }\n"
        )

        assert old in text, "tree-sitter patch target not found"
        path.write_text(text.replace(old, new, 1))
        PY
      '';
    };

  zedOverride = attrs: {
    buildInputs = (attrs.buildInputs or [ ]) ++ [ git ];
    installPhase = ''
            runHook preInstall

            app_path="$out/Applications/Zed Nightly.app"
            iconset_dir="$TMPDIR/Zed Nightly.iconset"

            mkdir -p "$app_path/Contents/MacOS" "$app_path/Contents/Resources" "$out/bin"

      cat > "$app_path/Contents/Info.plist" <<EOF
      <?xml version="1.0" encoding="UTF-8"?>
      <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
      <plist version="1.0">
      <dict>
        <key>CFBundleDevelopmentRegion</key>
        <string>English</string>
        <key>CFBundleDisplayName</key>
        <string>Zed Nightly</string>
        <key>CFBundleExecutable</key>
        <string>zed</string>
        <key>CFBundleIconFile</key>
        <string>Zed Nightly</string>
        <key>CFBundleIdentifier</key>
        <string>dev.zed.Zed-Nightly</string>
        <key>CFBundleInfoDictionaryVersion</key>
        <string>6.0</string>
        <key>CFBundleName</key>
        <string>Zed Nightly</string>
        <key>CFBundlePackageType</key>
        <string>APPL</string>
        <key>CFBundleShortVersionString</key>
        <string>${appVersion}</string>
        <key>CFBundleVersion</key>
        <string>${appVersion}</string>
        <key>CFBundleURLTypes</key>
        <array>
          <dict>
            <key>CFBundleTypeRole</key>
            <string>Editor</string>
            <key>CFBundleURLSchemes</key>
            <array>
              <string>zed</string>
            </array>
          </dict>
        </array>
        <key>LSApplicationCategoryType</key>
        <string>public.app-category.developer-tools</string>
        <key>LSMinimumSystemVersion</key>
        <string>10.15.7</string>
        <key>NSHighResolutionCapable</key>
        <true/>
      $(cat "${patchedSrc}/crates/zed/resources/info/SupportedPlatforms.plist")
      $(cat "${patchedSrc}/crates/zed/resources/info/Permissions.plist")
      $(cat "${patchedSrc}/crates/zed/resources/info/DocumentTypes.plist")
      </dict>
      </plist>
      EOF

            mkdir -p "$iconset_dir"
            for size in 16 32 64 128 256; do
              "${imagemagick}/bin/magick" "${patchedSrc}/crates/zed/resources/app-icon-nightly.png" \
                -resize "''${size}x''${size}" "$iconset_dir/''${size}.png"
            done
            cp "${patchedSrc}/crates/zed/resources/app-icon-nightly.png" "$iconset_dir/512.png"
            cp "${patchedSrc}/crates/zed/resources/app-icon-nightly@2x.png" "$iconset_dir/1024.png"
            "${libicns}/bin/png2icns" "$app_path/Contents/Resources/Zed Nightly.icns" "$iconset_dir"/*.png >/dev/null
            cp "${patchedSrc}/crates/zed/resources/Document.icns" "$app_path/Contents/Resources/Document.icns"

            cp "$PWD/target/bin/zed" "$app_path/Contents/MacOS/zed"
            ln -s ${git}/bin/git "$app_path/Contents/MacOS/git"
            cp ${cliDrv}/bin/cli "$app_path/Contents/MacOS/cli"
            ln -s "$out/Applications/Zed Nightly.app/Contents/MacOS/cli" "$out/bin/zed"

            runHook postInstall
    '';
  };

  commonCrateOverrides = lib.genAttrs commonCrates (_: commonOverride);

  crateOverrides =
    pkgs.defaultCrateOverrides
    // commonCrateOverrides
    // {
      documented = documentedOverride;
      gpui_macos = attrs: (commonOverride attrs) // (gpuiMacosOverride attrs);
      rav1e = rav1eOverride;
      tree-sitter = treeSitterOverride;
      wasmtime-c-api-impl = wasmtimeCApiImplOverride;
      webrtc-sys = attrs: (commonOverride attrs) // (webrtcSysOverride attrs);
      zed = attrs: (commonOverride attrs) // (zedOverride attrs);
    };

  cliDrv = cargoNix.workspaceMembers.cli.build.override {
    inherit crateOverrides;
    runTests = false;
  };

  zedDrv = cargoNix.workspaceMembers.zed.build.override {
    inherit crateOverrides;
    runTests = false;
    features = [
      "default"
      "gpui_platform/runtime_shaders"
    ];
  };
  zedDrvChecked = zedDrv.overrideAttrs (old: {
    doInstallCheck = true;
    installCheckPhase = (old.installCheckPhase or "") + ''
      runHook preInstallCheck

      test -x "$out/Applications/Zed Nightly.app/Contents/MacOS/zed"
      test -L "$out/bin/zed"
      $out/bin/zed --help >/dev/null

      runHook postInstallCheck
    '';
  });
  guardedZedDrv =
    assert cargoNixVersionCheck;
    zedDrvChecked;
in
if crate2nixSourceOnly then
  patchedSrc
else
  symlinkJoin {
    name = "${pname}-${version}";
    paths = [ guardedZedDrv ];

    passthru = {
      inherit cargoNix crateOverrides patchedSrc;
      zedDrv = guardedZedDrv;
    };

    meta = {
      description = "High-performance, multiplayer code editor from the creators of Atom and Tree-sitter";
      homepage = "https://zed.dev";
      changelog = "https://zed.dev/releases/preview";
      license = lib.licenses.gpl3Only;
      mainProgram = "zed";
      platforms = lib.platforms.darwin;
    };
  }
