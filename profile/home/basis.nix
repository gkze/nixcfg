{ config, pkgs, hostPlatform, ... }:
let
  kernel = builtins.elemAt (builtins.split "-" hostPlatform) 2;

  # Source code directory
  srcDir = { darwin = "Development"; linux = "src"; }.${kernel};
in
{
  imports = [
    {
      darwin = { };
      linux = {
        home.packages = with pkgs; [
          # OpenVPN extension for NetworkManager
          networkmanager-openvpn
        ];
      };
    }.${kernel}
    {
      home.packages = with pkgs; [
        # Password manager
        _1password-gui
        # Knowledge management
        obsidian
        # Yet Another AWS SSO tool
        yawsso
      ];
      programs = {
        awscli = {
          enable = true;
          settings = {
            default = { region = "us-east-1"; output = "json"; };
            "profile development" = {
              sso_session = "basis";
              sso_account_id = 820061307359;
              sso_role_name = "PowerUserAccess";
            };
            "profile staging" = {
              sso_session = "basis";
              sso_account_id = 523331955727;
              sso_role_name = "PowerUserAccess";
            };
            "profile production" = {
              sso_session = "basis";
              sso_account_id = 432644110438;
              sso_role_name = "PowerUserAccess";
            };
            "sso-session basis" = {
              sso_start_url = "https://d-90679b66bf.awsapps.com/start";
              sso_region = "us-east-1";
              sso_registration_scopes = "sso:account:access";
            };
          };
        };
        git = {
          lfs.enable = true;
          includes = [
            {
              path = "${config.xdg.configHome}/git/basis";
              condition = "gitdir:~/${srcDir}/git.usebasis.co/**";
            }
          ];
        };
      };
    }
  ];
}
