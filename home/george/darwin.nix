{
  config,
  lib,
  pkgs,
  ...
}:
let
  localZshSiteFuncsPath = "zsh/site-functions";
  opencodeDockApp = "${config.home.homeDirectory}/Applications/Home Manager Apps/OpenCode Desktop Dev.app";
in
{
  home.activation.repairTownDockOpenCodeDesktopDev = lib.hm.dag.entryAfter [ "installPackages" ] ''
    # When nix-darwin writes Dock defaults before Home Manager finishes
    # materializing ~/Applications/Home Manager Apps, macOS can keep an
    # unresolved question-mark tile for OpenCode Desktop Dev. Re-add the item after
    # installPackages so Dock resolves it against the live bundle.
    dockState="$(/usr/bin/defaults read com.apple.dock persistent-apps 2>/dev/null || true)"
    if ! printf '%s\n' "$dockState" | ${lib.getExe pkgs.gnugrep} -Eq 'OpenCode (Desktop |Electron )?Dev'; then
      exit 0
    fi

    if [ ! -d ${lib.escapeShellArg opencodeDockApp} ]; then
      echo "warning: skipping OpenCode Desktop Dev Dock repair because ${opencodeDockApp} is missing" >&2
      exit 0
    fi

    ${pkgs.dockutil}/bin/dockutil --remove "OpenCode Electron Dev" --no-restart >/dev/null 2>&1 || true
    ${pkgs.dockutil}/bin/dockutil --remove "OpenCode Dev" --no-restart >/dev/null 2>&1 || true
    ${pkgs.dockutil}/bin/dockutil --remove "OpenCode Desktop Dev" --no-restart >/dev/null 2>&1 || true
    ${pkgs.dockutil}/bin/dockutil --add ${lib.escapeShellArg opencodeDockApp} --after "Claude"
  '';

  launchd.agents = {
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
