{
  config,
  lib,
  pkgs,
  ...
}:
let
  localZshSiteFuncsPath = "zsh/site-functions";
in
{
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
