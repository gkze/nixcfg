{
  config,
  lib,
  pkgs,
  ...
}:
with lib;
let
  cfg = config.local.dock;
  inherit (pkgs) stdenv coreutils dockutil;
  dockutilExe = "sudo -u ${config.system.primaryUser} ${lib.getExe dockutil}";
in
{
  options = {
    local.dock.enable = mkOption {
      description = "Enable dock";
      default = stdenv.isDarwin;
      example = false;
    };
    local.dock.entries = mkOption {
      description = "Entries on the Dock";
      type =
        with types;
        listOf (submodule {
          options = {
            path = lib.mkOption { type = str; };
            section = lib.mkOption {
              type = str;
              default = "apps";
            };
            options = lib.mkOption {
              type = str;
              default = "";
            };
          };
        });
      default = [ ];
    };
  };
  config = mkIf (cfg.enable && builtins.length cfg.entries > 0) (
    let
      normalize = path: if hasSuffix ".app" path then path + "/" else path;
      entryURI =
        path:
        "file://"
        + (builtins.replaceStrings
          [
            " "
            "!"
            "\""
            "#"
            "$"
            "%"
            "&"
            "'"
            "("
            ")"
          ]
          [
            "%20"
            "%21"
            "%22"
            "%23"
            "%24"
            "%25"
            "%26"
            "%27"
            "%28"
            "%29"
          ]
          (normalize path)
        );
      wantURIs = concatMapStrings (entry: "${entryURI entry.path}\n") cfg.entries;
      createEntries = concatMapStrings (
        entry:
        "${dockutilExe} --no-restart --add '${entry.path}' --section ${entry.section} ${entry.options}\n"
      ) cfg.entries;
    in
    {
      system.activationScripts.postActivation.text = ''
        echo >&2 "Setting up the Dock..."
        haveURIs="$(${dockutilExe} --list | ${coreutils}/bin/cut -f2)"
        if ! diff -wu <(echo -n "$haveURIs") <(echo -n '${wantURIs}') >&2 ; then
          echo >&2 "Resetting Dock."
          ${dockutilExe} --no-restart --remove all
          ${createEntries}
          killall Dock
        else
          echo >&2 "Dock setup complete."
        fi
      '';
    }
  );
}
