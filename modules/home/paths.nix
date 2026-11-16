{
  config,
  lib,
  pkgs,
  slib,
  ...
}:
let
  inherit (lib)
    hasPrefix
    hasSuffix
    mkIf
    mkOption
    optionals
    removePrefix
    types
    unique
    ;

  cfg = config.nixcfg.paths;
  isDarwin = slib.kernel pkgs.stdenv.hostPlatform.system == "darwin";

  expandHome =
    entry:
    if hasPrefix "$HOME" entry then
      "${config.home.homeDirectory}${removePrefix "$HOME" entry}"
    else
      entry;
  expanded = map expandHome cfg.user;
  malformed = builtins.filter (entry: !(hasPrefix "/" entry) || hasSuffix "/" entry) expanded;
  repeated = builtins.filter (entry: lib.count (other: other == entry) expanded > 1) (
    unique expanded
  );
in
{
  options.nixcfg.paths.user = mkOption {
    type = types.listOf types.str;
    default = [ ];
    description = ''
      Ordered user PATH entries shared by shells and by processes started from
      the launchd user domain (GUI apps, hooks, editor tasks, language servers).

      On Darwin, `modules/darwin/paths.nix` renders this list into
      `environment.variables.PATH` and `launchd.user.envVariables.PATH`, so
      these entries must not also be written to `home.sessionPath`. Keep
      tool-managed layers such as mise and direnv out of this list: they activate
      from their own configuration.
    '';
  };

  config = {
    assertions = [
      {
        assertion = repeated == [ ];
        message = "nixcfg.paths.user repeats: ${builtins.toJSON repeated}";
      }
      {
        assertion = malformed == [ ];
        message = "nixcfg.paths.user entries must be absolute or $HOME-prefixed with no trailing slash: ${builtins.toJSON malformed}";
      }
    ]
    ++ optionals isDarwin [
      {
        assertion = config.home.sessionPath == [ ];
        message = ''
          home.sessionPath must stay empty on Darwin: PATH is rendered from
          nixcfg.paths.user plus nixcfg.paths.system. Move the entry to
          nixcfg.paths.user instead of adding a second, overlapping source.
        '';
      }
    ];

    # Non-Darwin consumers have no nix-darwin environment file, so Home Manager
    # remains the PATH writer there.
    home.sessionPath = mkIf (!isDarwin) cfg.user;
  };
}
