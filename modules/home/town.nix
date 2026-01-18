{ pkgs, ... }:
{
  home.packages = with pkgs; [
    _1password-cli
    _1password-gui
    google-cloud-sdk
  ];
  programs = {
    topgrade.settings.misc.disable = [
      # "bun"
      "gcloud"
      # "jetbrains_datagrip"
      # "uv"
    ];
    zsh.plugins = with pkgs; [
      {
        name = "op";
        src = runCommand "_op" { buildInputs = [ _1password-cli ]; } ''
          mkdir $out
          HOME=$(mktemp -d) op completion zsh > $out/_op
        '';
        file = "_op";
      }
    ];
  };
}
