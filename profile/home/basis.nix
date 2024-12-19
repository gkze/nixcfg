{
  config,
  pkgs,
  hostPlatform,
  ...
}:
let
  kernel = builtins.elemAt (builtins.split "-" hostPlatform) 2;

  # Source code directory
  srcDir =
    {
      darwin = "Development";
      linux = "src";
    }
    .${kernel};
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

        programs.firefox.profiles.main = {
          bookmarks = [
            {
              toolbar = true;
              bookmarks = [
                {
                  name = "Mail";
                  url = "https://mail.google.com/mail/u/0/#inbox";
                }
                {
                  name = "Calendar";
                  url = "https://calendar.google.com/calendar/u/0/r";
                }
                {
                  name = "Drive";
                  url = "https://drive.google.com/drive/home";
                }
                {
                  name = "Meet";
                  url = "https://meet.google.com/landing";
                }
                {
                  name = "JumpCloud";
                  url = "https://console.jumpcloud.com/userconsole#/";
                }
                {
                  name = "Linear";
                  url = "https://linear.app/usebasis/team/BAS/active";
                }
                {
                  name = "GitLab";
                  bookmarks = [
                    {
                      name = "Home";
                      url = "https://git.usebasis.co/";
                    }
                    {
                      name = "My MRs | Open";
                      url = "https://git.usebasis.co/dashboard/merge_requests?scope=all&state=opened&author_username=george";
                    }
                    {
                      name = "My MRs | All";
                      url = "https://git.usebasis.co/dashboard/merge_requests?scope=all&state=all&author_username=george";
                    }
                    {
                      name = "basis/basis";
                      url = "https://git.usebasis.co/basis/basis";
                    }
                    {
                      name = "basis/connect";
                      url = "https://git.usebasis.co/basis/connect";
                    }
                    {
                      name = "basis/point";
                      url = "https://git.usebasis.co/basis/point";
                    }
                    {
                      name = "basis/cloudformation";
                      url = "https://git.usebasis.co/basis/cloudformation";
                    }
                    {
                      name = "basis/finicity_client";
                      url = "https://git.usebasis.co/basis/finicity_client";
                    }
                  ];
                }
                {
                  name = "AWS";
                  bookmarks = [
                    {
                      name = "ACM";
                      url = "https://us-west-2.console.aws.amazon.com/acm/home?region=us-west-2#/welcome";
                    }
                    {
                      name = "AMQ";
                      url = "https://us-west-2.console.aws.amazon.com/amazon-mq/home?region=us-west-2#/";
                    }
                    {
                      name = "BCM";
                      url = "https://us-east-1.console.aws.amazon.com/costmanagement/home?region=us-west-2#/home";
                    }
                    {
                      name = "CForm";
                      url = "https://us-west-2.console.aws.amazon.com/vpcconsole/home?region=us-west-2#Home:";
                    }
                    {
                      name = "CFront";
                      url = "https://us-east-1.console.aws.amazon.com/cloudfront/v4/home?region=us-west-2";
                    }
                    {
                      name = "CW";
                      url = "https://us-west-2.console.aws.amazon.com/cloudwatch/home?region=us-west-2#home:";
                    }
                    {
                      name = "Console";
                      url = "https://us-west-2.console.aws.amazon.com/console/home?region=us-west-2";
                    }
                    {
                      name = "EC";
                      url = "https://us-west-2.console.aws.amazon.com/elasticache/home?region=us-west-2#/";
                    }
                    {
                      name = "EC2";
                      url = "https://us-west-2.console.aws.amazon.com/ec2/home?region=us-west-2#Home:";
                    }
                    {
                      name = "ECR";
                      url = "https://us-west-2.console.aws.amazon.com/ecr/home?region=us-west-2#";
                    }
                    {
                      name = "ECS";
                      url = "https://us-west-2.console.aws.amazon.com/ecs/v2/clusters?region=us-west-2";
                    }
                    {
                      name = "IAM";
                      url = "https://us-east-1.console.aws.amazon.com/iam/home?region=us-west-2#/home";
                    }
                    {
                      name = "R53";
                      url = "https://us-east-1.console.aws.amazon.com/route53/v2/home?region=us-west-2#Dashboard";
                    }
                    {
                      name = "RDS";
                      url = "https://us-west-2.console.aws.amazon.com/rds/home?region=us-west-2#";
                    }
                    {
                      name = "S3";
                      url = "https://us-west-2.console.aws.amazon.com/s3/home?region=us-west-2#";
                    }
                    {
                      name = "SES";
                      url = "https://us-west-2.console.aws.amazon.com/ses/home?region=us-west-2#/homepage";
                    }
                    {
                      name = "SM";
                      url = "https://us-west-2.console.aws.amazon.com/secretsmanager/home?region=us-west-2#";
                    }
                    {
                      name = "SNS";
                      url = "https://us-west-2.console.aws.amazon.com/sns/v3/home?region=us-west-2#/homepage";
                    }
                  ];
                }
                {
                  name = "Sentry";
                  url =
                    let
                      inherit (builtins) concatStringsSep map toString;
                      projectIds = concatStringsSep "&" (
                        map (pId: "project=${toString pId}") [
                          4506396608954368
                          4506804915994624
                          4506827713282048
                          4506396605349888
                          4506396583460864
                          4506894368505856
                          4506396636086272
                          4506894366932992
                          4506894692253696
                          4507420592635904
                        ]
                      );
                    in
                    "https://basis-lf.sentry.io/issues/?${projectIds}&statsPeriod=24h";
                }
                {
                  name = "Finicity OpenAPI Docs";
                  url = "https://developer.mastercard.com/open-banking-us/documentation/api-reference/";
                }
              ];
            }
          ];
          # This currently breaks devtools, needs to be reworked
          # containersForce = true;
          # containers = {
          #   "Customer User" = { color = "turquoise"; id = 1; icon = "circle"; };
          #   "Profile User" = { color = "purple"; id = 2; icon = "circle"; };
          #   "AWS Management" = { color = "green"; id = 3; icon = "circle"; };
          #   "AWS Staging" = { color = "yellow"; id = 4; icon = "circle"; };
          #   "AWS Production" = { color = "red"; id = 5; icon = "circle"; };
          # };
          extensions = with pkgs.nur.repos.rycee.firefox-addons; [
            onepassword-password-manager
          ];
        };
      };
    }
    .${kernel}
  ];
  home = {
    file.".docker/config.json".text = builtins.toJSON { credsStore = "ecr-login"; };
    packages = with pkgs; [
      # Amazon ECR Credential Helper
      amazon-ecr-credential-helper
      # Amazon ECS CLI
      amazon-ecs-cli
      # Password manager
      _1password-gui
      # S3 filesystem in userspace
      # mountpoint-s3
      # Knowledge management
      obsidian
      # AWS Systems Manager Session Manager plugin for the AWS CLI
      ssm-session-manager-plugin
      # Yet Another AWS SSO tool
      yawsso
    ];
  };
  programs = {
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
    nixvim.config.plugins.gitlinker.callbacks = {
      "git.usebasis.co" = "get_gitlab_type_url";
    };
  };
  services.syncthing = {
    enable = true;
    tray.enable = true;
  };
}
