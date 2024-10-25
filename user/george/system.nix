{ hostPlatform, ... }:
let
  inherit (builtins) elemAt split;

  # Grab the OS kernel part of the hostPlatform tuple
  kernel = elemAt (split "-" hostPlatform) 2;

  # User metadata
  meta = import ./meta.nix;
in
{
  imports = [
    {
      darwin = { };
      linux = {
        services.coredns = {
          enable = false;
          config = ''
            . {
              forward . 1.1.1.1 1.0.0.1 8.8.8.8 8.8.4.4
              cache
            }

            local {
              template IN A {
                answer "{{ .Name }} 0 IN A 127.0.0.1"
              }
            }
          '';
        };
        # networking.networkmanager.insertNameservers = [ "127.0.0.1" ];
      };
    }
    .${kernel}
  ];

  nix.settings.trusted-users = [ meta.name.user.system ];
}
