{
  src,
  lib,
  pkgs,
  ...
}:
{
  home = {
    stateVersion = lib.removeSuffix "\n" (builtins.readFile "${src}/NIXOS_VERSION");

    packages = with pkgs; [ ];
  };

  services.syncthing = {
    enable = true;
    tray.enable = true;
  };

  programs = {
    # _1password.enable = true;

    awscli = {
      enable = true;
      settings = {
        default = {
          region = "us-west-2";
          output = "json";
        };
        "profile mgmt" = {
          sso_session = "basis";
          sso_account_id = 820061307359;
          sso_role_name = "poweruseraccess";
        };
        "profile stg" = {
          sso_session = "basis";
          sso_account_id = 905418462882;
          sso_role_name = "poweruseraccess";
        };
        "profile prd" = {
          sso_session = "basis";
          sso_account_id = 432644110438;
          sso_role_name = "poweruseraccess";
        };
        "sso-session basis" = {
          sso_start_url = "https://d-90679b66bf.awsapps.com/start";
          sso_region = "us-east-1";
          sso_registration_scopes = "sso:account:access";
        };
      };
    };

    direnv = {
      enable = true;
      nix-direnv.enable = true;
    };

    home-manager.enable = true;

    man = {
      enable = true;
      generateCaches = true;
    };
  };

}
