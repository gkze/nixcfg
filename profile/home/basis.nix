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
        # Amazon ECR Credential Helper
        amazon-ecr-credential-helper
        # Amazon ECS CLI
        amazon-ecs-cli
        # Password manager
        _1password-gui
        # Knowledge management
        obsidian
        # AWS Systems Manager Session Manager plugin for the AWS CLI
        ssm-session-manager-plugin
        # Yet Another AWS SSO tool
        yawsso
      ];
      programs = {
        awscli = {
          enable = true;
          settings = {
            default = { region = "us-west-2"; output = "json"; };
            "profile mgmt" = {
              sso_session = "basis";
              sso_account_id = 820061307359;
              sso_role_name = "PowerUserAccess";
            };
            "profile stg" = {
              sso_session = "basis";
              sso_account_id = 905418462882;
              sso_role_name = "PowerUserAccess";
            };
            "profile prd" = {
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
