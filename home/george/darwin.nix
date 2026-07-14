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
      pkgs.gawk
      pkgs.gnused
      pkgs.perl
    ];
    text = ''
      set -eu

      home_dir=${lib.escapeShellArg config.home.homeDirectory}
      app_root="$home_dir/Applications/ChatGPT.app/Contents/Resources/plugins/openai-bundled"
      tmp_root="$home_dir/.codex/.tmp/bundled-marketplaces/openai-bundled"
      manifest_src="$app_root/.agents/plugins/marketplace.json"
      manifest_dst="$tmp_root/.agents/plugins/marketplace.json"

      if [ -f "$manifest_src" ] && [ -d "$tmp_root/plugins" ]; then
        mkdir -p "$(dirname "$manifest_dst")"
        cp "$manifest_src" "$manifest_dst"
      fi

      config_file="$home_dir/.codex/config.toml"
      if [ ! -f "$config_file" ]; then
        exit 0
      fi

      hashes=""
      for client in \
        "$tmp_root/plugins/browser/scripts/browser-client.mjs" \
        "$tmp_root/plugins/chrome/scripts/browser-client.mjs" \
        "$app_root/plugins/browser/scripts/browser-client.mjs" \
        "$app_root/plugins/chrome/scripts/browser-client.mjs"
      do
        if [ -f "$client" ]; then
          hash="$(sha256sum "$client" | awk '{ print $1 }')"
          case " $hashes " in
            *" $hash "*) ;;
            *) hashes="$hashes $hash" ;;
          esac
        fi
      done

      hashes="$(printf '%s\n' "$hashes" | sed 's/^ *//; s/ *$//')"
      if [ -z "$hashes" ]; then
        exit 0
      fi

      current="$(
        sed -n 's/^NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "\\(.*\\)"$/\\1/p' "$config_file" \
          | tail -n 1
      )"
      if [ -z "$current" ]; then
        exit 0
      fi

      trusted="$current"
      for hash in $hashes; do
        case "$trusted" in
          *"$hash"*) ;;
          *) trusted="$trusted,$hash" ;;
        esac
      done

      if [ "$trusted" = "$current" ]; then
        exit 0
      fi

      tmp_file="$(mktemp "$config_file.tmp.XXXXXX")"
      CODEX_BROWSER_HASHES="$trusted" perl -0pe \
        's/^NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = ".*"$/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S = "$ENV{CODEX_BROWSER_HASHES}"/m' \
        "$config_file" > "$tmp_file"
      chmod 600 "$tmp_file"
      mv "$tmp_file" "$config_file"
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
        StartInterval = 30;
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
    gpg-agent = {
      enable = true;
      config = {
        Label = "org.gnupg.gpg-agent";
        RunAtLoad = true;
        ProgramArguments = [
          "${pkgs.gnupg}/bin/gpg-agent"
          "--server"
        ];
      };
    };
  };

  xdg.dataFile.${localZshSiteFuncsPath} = {
    source = pkgs.homebrew-zsh-completion;
    recursive = true;
    executable = true;
  };

  programs.zsh.initContent = lib.mkOrder 550 ''
    fpath+=${config.xdg.dataHome}/${localZshSiteFuncsPath}
  '';
}
