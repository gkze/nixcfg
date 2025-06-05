{ pkgs, ... }:
{
  home.packages = with pkgs; [ google-cloud-sdk ];
  programs = {
    topgrade.settings.misc.disable = [
      "gcloud"
      "uv"
    ];
    zsh.plugins = with pkgs; [
      {
        name = "op";
        src = runCommand "_op" { } ''
          mkdir $out
          HOME=$(mktemp -d) /opt/homebrew/bin/op completion zsh > $out/_op
        '';
        file = "_op";
      }
    ];
  };
}
