{ pkgs, ... }:
{
  home.packages = with pkgs; [
    _1password-cli
    google-cloud-sdk
  ];
  programs = {
    # TODO: figure out
    # opencode.settings.mcp = {
    #   axiom = {
    #     type = "remote";
    #     url = "https://mcp.axiom.co/mcp";
    #   };
    #   convex = {
    #     type = "local";
    #     command = [
    #       "npx"
    #       "-y"
    #       "convex@latest"
    #       "mcp"
    #       "start"
    #     ];
    #   };
    #   linear = {
    #     type = "remote";
    #     url = "https://mcp.linear.app/mcp";
    #   };
    #   notion = {
    #     type = "remote";
    #     url = "https://mcp.notion.com/mcp";
    #     oauth = { };
    #   };
    #   sentry = {
    #     type = "remote";
    #     url = "https://mcp.sentry.dev/mcp";
    #   };
    #   vercel = {
    #     type = "remote";
    #     url = "https://mcp.vercel.com";
    #   };
    # };
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
