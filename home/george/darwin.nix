{
  config,
  lib,
  pkgs,
  ...
}:
let
  localZshSiteFuncsPath = "zsh/site-functions";
  codexBundledPluginRepair = pkgs.writeShellApplication {
    name = "codex-bundled-plugin-repair";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.diffutils
      pkgs.gawk
      pkgs.gnused
      pkgs.perl
    ];
    text = ''
      set -eu

      home_dir=${lib.escapeShellArg config.home.homeDirectory}
      case "$#" in
        0) ;;
        1) home_dir=$1 ;;
        *)
          echo "usage: codex-bundled-plugin-repair [home-directory]" >&2
          exit 64
          ;;
      esac
      app_root="$home_dir/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled"
      tmp_root="$home_dir/.codex/.tmp/bundled-marketplaces/openai-bundled"
      manifest_src="$app_root/.agents/plugins/marketplace.json"
      manifest_dst="$tmp_root/.agents/plugins/marketplace.json"
      config_file="$home_dir/.codex/config.toml"
      tmp_plugins_present=0
      if [ -d "$tmp_root/plugins" ]; then
        tmp_plugins_present=1
      fi

      umask 077
      snapshot_dir="$(mktemp -d "''${TMPDIR:-/tmp}/codex-bundled-plugin-repair.XXXXXX")"
      manifest_tmp=
      config_tmp=
      cleanup() {
        if [ -n "$manifest_tmp" ]; then
          rm -f -- "$manifest_tmp"
        fi
        if [ -n "$config_tmp" ]; then
          rm -f -- "$config_tmp"
        fi
        rm -rf -- "$snapshot_dir"
      }
      trap cleanup EXIT
      trap 'exit 1' HUP INT TERM

      sources=(
        "$manifest_src"
        "$config_file"
        "$tmp_root/plugins/browser/scripts/browser-client.mjs"
        "$tmp_root/plugins/chrome/scripts/browser-client.mjs"
        "$app_root/plugins/browser/scripts/browser-client.mjs"
        "$app_root/plugins/chrome/scripts/browser-client.mjs"
      )
      snapshot_names=(manifest config client-0 client-1 client-2 client-3)
      declare -a snapshots=()
      declare -a present=()

      for index in "''${!sources[@]}"; do
        source_path="''${sources[$index]}"
        snapshot_path="$snapshot_dir/''${snapshot_names[$index]}"
        snapshots+=("$snapshot_path")
        if [ -f "$source_path" ]; then
          cp --dereference -- "$source_path" "$snapshot_path"
          present+=(1)
        else
          present+=(0)
        fi
      done

      sources_stable() {
        current_tmp_plugins_present=0
        if [ -d "$tmp_root/plugins" ]; then
          current_tmp_plugins_present=1
        fi
        if [ "$current_tmp_plugins_present" -ne "$tmp_plugins_present" ]; then
          return 1
        fi

        for index in "''${!sources[@]}"; do
          source_path="''${sources[$index]}"
          if [ "''${present[$index]}" -eq 1 ]; then
            if [ ! -f "$source_path" ] || ! cmp -s "''${snapshots[$index]}" "$source_path"; then
              return 1
            fi
          elif [ -f "$source_path" ]; then
            return 1
          fi
        done
        return 0
      }

      sleep 1
      if ! sources_stable; then
        exit 0
      fi

      declare -a hashes=()
      declare -a unique_clients=()
      if [ "''${present[1]}" -eq 1 ]; then
        for index in 2 3 4 5; do
          if [ "''${present[$index]}" -eq 1 ]; then
            client="''${snapshots[$index]}"
            duplicate=0
            for unique_client in "''${unique_clients[@]}"; do
              if cmp -s "$client" "$unique_client"; then
                duplicate=1
                break
              fi
            done
            if [ "$duplicate" -eq 1 ]; then
              continue
            fi

            unique_clients+=("$client")
            hash="$(sha256sum "$client" | awk '{ print $1 }')"
            hashes+=("$hash")
          fi
        done
      fi

      if ! sources_stable; then
        exit 0
      fi

      if [ "''${present[0]}" -eq 1 ] && [ "$tmp_plugins_present" -eq 1 ]; then
        mkdir -p "$(dirname "$manifest_dst")"
        if ! cmp -s "''${snapshots[0]}" "$manifest_dst"; then
          manifest_tmp="$(mktemp "$(dirname "$manifest_dst")/.marketplace.json.tmp.XXXXXX")"
          cp --dereference -- "''${snapshots[0]}" "$manifest_tmp"
        fi
      fi

      if [ "''${present[1]}" -eq 1 ] && [ "''${#hashes[@]}" -gt 0 ]; then
        current="$(
          sed -n 's/^NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "\(.*\)"$/\1/p' \
            "''${snapshots[1]}" | tail -n 1
        )"
        if [ -n "$current" ]; then
          trusted="$current"
          for hash in "''${hashes[@]}"; do
            case "$trusted" in
              *"$hash"*) ;;
              *) trusted="$trusted,$hash" ;;
            esac
          done

          if [ "$trusted" != "$current" ]; then
            config_tmp="$(mktemp "$config_file.tmp.XXXXXX")"
            CODEX_BROWSER_HASHES="$trusted" perl -0pe \
              's/^NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = ".*"$/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "$ENV{CODEX_BROWSER_HASHES}"/m' \
              "''${snapshots[1]}" > "$config_tmp"
            chmod 600 "$config_tmp"
          fi
        fi
      fi

      if ! sources_stable; then
        exit 0
      fi
      if [ -n "$config_tmp" ] && ! cmp -s "''${snapshots[1]}" "$config_file"; then
        exit 0
      fi

      if [ -n "$manifest_tmp" ]; then
        mv -- "$manifest_tmp" "$manifest_dst"
        manifest_tmp=
      fi
      if [ -n "$config_tmp" ]; then
        if ! cmp -s "''${snapshots[1]}" "$config_file"; then
          exit 0
        fi
        mv -- "$config_tmp" "$config_file"
        config_tmp=
      fi
    '';
  };
in
{
  home.activation.codexBundledPluginRepair = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    ${lib.getExe codexBundledPluginRepair}
  '';

  launchd.agents = {
    codex-bundled-plugin-repair = {
      enable = true;
      config = {
        Label = "dev.george.codex-bundled-plugin-repair";
        RunAtLoad = true;
        StartCalendarInterval = {
          Hour = 4;
          Minute = 0;
        };
        WatchPaths = [
          "${config.home.homeDirectory}/Applications/ChatGPT.app"
          "${config.home.homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/.agents/plugins"
          "${config.home.homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/.agents/plugins/marketplace.json"
          "${config.home.homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/browser/scripts"
          "${config.home.homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/browser/scripts/browser-client.mjs"
          "${config.home.homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/chrome/scripts"
          "${config.home.homeDirectory}/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled/plugins/chrome/scripts/browser-client.mjs"
          "${config.home.homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled"
          "${config.home.homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/.agents/plugins"
          "${config.home.homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/.agents/plugins/marketplace.json"
          "${config.home.homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/browser/scripts"
          "${config.home.homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/browser/scripts/browser-client.mjs"
          "${config.home.homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/chrome/scripts"
          "${config.home.homeDirectory}/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/chrome/scripts/browser-client.mjs"
          "${config.home.homeDirectory}/.codex/config.toml"
        ];
        ProgramArguments = [ (lib.getExe codexBundledPluginRepair) ];
        StandardOutPath = "${config.home.homeDirectory}/Library/Logs/codex-bundled-plugin-repair.log";
        StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/codex-bundled-plugin-repair.log";
      };
    };
    ssh-add = {
      enable = true;
      config = {
        Label = "org.openssh.add";
        LaunchOnlyOnce = true;
        RunAtLoad = true;
        ProgramArguments = [
          "/usr/bin/ssh-add"
          "--apple-load-keychain"
          "--apple-use-keychain"
        ];
      };
    };
    gpg-home = {
      enable = true;
      config = {
        Label = "org.gnupg.home";
        RunAtLoad = true;
        ProgramArguments = [
          "/bin/launchctl"
          "setenv"
          "GNUPGHOME"
          config.programs.gpg.homedir
        ];
      };
    };
  };

  xdg.dataFile.${localZshSiteFuncsPath} = {
    source = pkgs.homebrew-zsh-completion;
    recursive = true;
    executable = true;
  };

  programs.zsh.initContent = lib.mkMerge [
    (lib.mkOrder 550 ''
      fpath+=${config.xdg.dataHome}/${localZshSiteFuncsPath}
    '')
    (lib.mkAfter ''
      # >>> town:mise-shell-activation-v1 >>>
      if [[ -x "${config.home.homeDirectory}/.local/bin/mise" ]]; then
        eval "$("${config.home.homeDirectory}/.local/bin/mise" activate zsh)"
      fi
      # <<< town:mise-shell-activation-v1 <<<
    '')
  ];
}
