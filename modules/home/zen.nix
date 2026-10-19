{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkEnableOption
    mkIf
    mkMerge
    mkOption
    types
    ;

  cfg = config.nixcfg.zen;
  debugging = cfg.remoteDebugging;
  debuggingEnabled =
    debugging.bidi.enable || debugging.marionette.enable || debugging.devtools.enable;
  protocolOption = description: port: {
    enable = mkEnableOption description;
    port = mkOption {
      type = types.port;
      default = port;
      description = "Local TCP port for ${description}.";
    };
  };
  debuggingPorts =
    lib.optional debugging.bidi.enable debugging.bidi.port
    ++ lib.optional debugging.marionette.enable debugging.marionette.port
    ++ lib.optional debugging.devtools.enable debugging.devtools.port;
  debuggingArgs =
    lib.optionals debugging.bidi.enable [
      "--remote-debugging-port"
      (toString debugging.bidi.port)
    ]
    ++ lib.optional debugging.marionette.enable "--marionette"
    ++ lib.optionals debugging.devtools.enable [
      "--start-debugger-server"
      (toString debugging.devtools.port)
    ];
  debuggingPrefs = {
    "devtools.debugger.remote-enabled" = debugging.devtools.enable;
    "devtools.chrome.enabled" = debugging.devtools.enable;
    "devtools.debugger.force-local" = true;
    "devtools.debugger.prompt-connection" = !debugging.devtools.allowUnattended;
    "marionette.port" = debugging.marionette.port;
    # This is a daily browser, not an automation-owned disposable profile.
    "remote.prefs.recommended" = false;
  };
  debuggingUserJs = pkgs.writeText "zen-user.js" (
    lib.optionalString (cfg.userJsSource != null) (builtins.readFile cfg.userJsSource)
    + "\n// Managed by nixcfg.zen.remoteDebugging.\n"
    + lib.concatStringsSep "\n" (
      lib.mapAttrsToList (
        name: value: "user_pref(${builtins.toJSON name}, ${builtins.toJSON value});"
      ) debuggingPrefs
    )
    + "\n"
  );
  configuredPackage = cfg.package.overrideAttrs (old: {
    postInstall = (old.postInstall or "") + ''
      app="$out/${cfg.package.passthru.macApp.bundleRelPath}"
      mv "$app/Contents/MacOS/zen" "$app/Contents/MacOS/zen-bin"
      cat > "$app/Contents/MacOS/zen" <<'EOF'
      #!/bin/sh
      if [ "$HOME" = ${lib.escapeShellArg config.home.homeDirectory} ]; then
        exec "$(dirname "$0")/zen-bin" ${lib.escapeShellArgs debuggingArgs} "$@"
      fi
      exec "$(dirname "$0")/zen-bin" "$@"
      EOF
      chmod +x "$app/Contents/MacOS/zen"
      /usr/bin/codesign --force --deep --sign - "$app"
    '';
  });
  managedConfigDir = "${config.xdg.configHome}/zen";
  zenPython = pkgs.python3.withPackages (
    ps: with ps; [
      click
      deepdiff
      lz4
      pydantic
      pyyaml
      typer
    ]
  );
  mkZenWrapper =
    name: script:
    pkgs.writeShellApplication {
      inherit name;
      text = ''
        exec ${lib.getExe zenPython} ${lib.escapeShellArg script} "$@"
      '';
    };
  zenTool = mkZenWrapper "zentool" ../../home/george/bin/zentool;
in
{
  options.nixcfg.zen = {
    enable = mkEnableOption "Zen/Twilight profile sync and declarative customizations";

    package = mkOption {
      type = types.nullOr types.package;
      default = if pkgs.stdenv.hostPlatform.isDarwin then pkgs.zen-twilight else null;
      description = "Base Zen app package. Remote debugging currently supports the macOS Zen bundle.";
    };

    finalPackage = mkOption {
      type = types.nullOr types.package;
      readOnly = true;
      description = ''
        Zen package with this user's launch configuration. Use this package in
        the app installation routing so Dock, Finder, and CLI launches agree.
        With remote debugging disabled, this is the unmodified base package.
      '';
    };

    remoteDebugging = {
      bidi = protocolOption "WebDriver BiDi" 9222;
      marionette = protocolOption "Marionette" 2828;
      devtools = protocolOption "Firefox DevTools RDP" 6000 // {
        allowUnattended = mkOption {
          type = types.bool;
          default = false;
          description = "Accept local DevTools connections without a confirmation dialog.";
        };
      };
    };

    profile = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "Default (twilight)";
      description = ''
        Profile selector passed through to zentool. Accepts a profile
        directory name, a direct path, or a human profile name from
        profiles.ini. Null uses auto-detection from Zen's profiles.ini.
      '';
    };

    chromeSource = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = ./chrome;
      description = ''
        Directory of managed Zen chrome assets. It is published to
        ~/.config/zen/chrome and each file is symlinked into the live profile's
        chrome/ directory.

        For local theme iteration without a Nix rebuild, run
        `zentool apply --assets --chrome-source /path/to/chrome` (or set
        `ZEN_CHROME_SOURCE=/path/to/chrome`) to temporarily sync from a direct
        filesystem path instead.
      '';
    };

    userJsSource = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = ./user.js;
      description = ''
        Source file for ~/.config/zen/user.js, symlinked into the live profile
        root.
      '';
    };

    foldersSource = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = ./folders.yaml;
      description = ''
        Declarative Zen session config published to ~/.config/zen/folders.yaml
        and applied with zentool when Zen is closed. The schema is
        containers/workspaces/items/tabs, with exact syncing against the
        managed subset of zen-sessions.jsonlz4 and the referenced contextual
        identities in containers.json.
      '';
    };

    quitDialogCssSource = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = ./quit-dialog-primary.css;
      description = ''
        CSS loaded by Twilight AutoConfig and injected into the quit dialog's
        shadow root. Published to ~/.config/zen/quit-dialog-primary.css.
      '';
    };

    toolCommand = mkOption {
      type = types.str;
      default = lib.getExe zenTool;
      description = ''
        Command used to inspect and reconcile Zen state and assets. Defaults to
        the packaged repo-managed zentool wrapper.
      '';
    };

    syncOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Run zentool apply during Home Manager activation.";
    };

    applyStateOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Apply folders.yaml to the Zen session during activation.";
    };

    applyAssetsOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Apply managed Zen assets during activation.";
    };
  };

  config = mkIf cfg.enable {
    nixcfg.zen.finalPackage = if debuggingEnabled then configuredPackage else cfg.package;

    assertions = [
      {
        assertion = debuggingEnabled -> (pkgs.stdenv.hostPlatform.isDarwin && cfg.package != null);
        message = "nixcfg.zen.remoteDebugging requires a macOS Zen package.";
      }
      {
        assertion = builtins.length debuggingPorts == builtins.length (lib.unique debuggingPorts);
        message = "nixcfg.zen.remoteDebugging protocols must use distinct ports.";
      }
      {
        assertion = debuggingEnabled -> (cfg.syncOnActivation && cfg.applyAssetsOnActivation);
        message = "nixcfg.zen.remoteDebugging requires profile asset sync for its companion preferences.";
      }
      {
        assertion = cfg.syncOnActivation -> cfg.toolCommand != "";
        message = "nixcfg.zen.toolCommand must be non-empty when syncOnActivation is enabled.";
      }
    ];

    home.packages = [
      zenTool
    ];

    xdg.configFile = mkMerge [
      (mkIf (cfg.chromeSource != null) {
        "zen/chrome" = {
          source = cfg.chromeSource;
          recursive = true;
        };
      })
      (mkIf (cfg.userJsSource != null || debuggingEnabled) {
        "zen/user.js".source = if debuggingEnabled then debuggingUserJs else cfg.userJsSource;
      })
      (mkIf (cfg.foldersSource != null) {
        "zen/folders.yaml".source = cfg.foldersSource;
      })
      (mkIf (cfg.quitDialogCssSource != null) {
        "zen/quit-dialog-primary.css".source = cfg.quitDialogCssSource;
      })
    ];

    home.activation = mkIf cfg.syncOnActivation {
      nixcfgZenSync = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
        sync_cmd=(${lib.escapeShellArg cfg.toolCommand})
        sync_args=(apply --yes)
        profile_args=()
        state_args=()
        asset_args=()
        state_config=${lib.escapeShellArg (managedConfigDir + "/folders.yaml")}

        ${lib.optionalString (cfg.profile != null) ''
          profile_args+=(--profile ${lib.escapeShellArg cfg.profile})
        ''}

        ${lib.optionalString cfg.applyStateOnActivation ''
          if [ -e "$state_config" ]; then
            state_args+=(--state)
            state_args+=(--config "$state_config")
          fi
        ''}

        ${lib.optionalString cfg.applyAssetsOnActivation ''
          asset_args+=(--assets)
          asset_args+=(--asset-dir ${lib.escapeShellArg managedConfigDir})
        ''}

        if [ "''${#state_args[@]}" -gt 0 ]; then
          runtime_check_cmd=("''${sync_cmd[@]}" profile)
          if [ "''${#profile_args[@]}" -gt 0 ]; then
            runtime_check_cmd+=("''${profile_args[@]}")
          fi
          runtime_check_cmd+=(is-running)

          if "''${runtime_check_cmd[@]}" >/dev/null 2>&1; then
            echo "warning: skipping Zen state sync during activation because Zen is running" >&2
            state_args=()
          fi
        fi

        if [ "''${#state_args[@]}" -gt 0 ]; then
          sync_args+=("''${state_args[@]}")
        fi

        if [ "''${#asset_args[@]}" -gt 0 ]; then
          sync_args+=("''${asset_args[@]}")
        fi

        if [ "''${#profile_args[@]}" -gt 0 ]; then
          sync_args+=("''${profile_args[@]}")
        fi

        if [ "''${#state_args[@]}" -gt 0 ] || [ "''${#asset_args[@]}" -gt 0 ]; then
          run --silence "''${sync_cmd[@]}" "''${sync_args[@]}"
        fi
      '';
    };
  };
}
