{ lib }:
{
  mkDockContext =
    {
      config,
      primaryUser ? null,
      username ? null,
    }:
    let
      user = if primaryUser != null then primaryUser else username;
      homeDirectory =
        if config ? home && config.home ? homeDirectory then
          config.home.homeDirectory
        else
          "/Users/${user}";
      resolved =
        (lib.attrByPath [ "nixcfg" "macApps" "resolved" ] { } config)
        // (lib.attrByPath [ "home-manager" "users" user "nixcfg" "macApps" "resolved" ] { } config);
      appPath =
        key: bundleName:
        lib.attrByPath [ key "path" ] "${homeDirectory}/Applications/${bundleName}" resolved;
    in
    {
      inherit
        appPath
        homeDirectory
        resolved
        user
        ;
    };

  mkDockModule =
    {
      activationName,
      apps,
      options,
      others,
      pkgs ? null,
      removeOthers ? [ ],
    }:
    let
      inherit (lib)
        concatMapStringsSep
        escapeShellArg
        mkMerge
        optionalAttrs
        ;

      hasDarwinDock = options ? system && options.system ? defaults && options.system.defaults ? dock;
      hasHomeActivation = options ? home && options.home ? activation && pkgs != null && lib ? hm;
      dockutil = if pkgs != null then "${pkgs.dockutil}/bin/dockutil" else "";
      dockLabel = path: lib.removeSuffix ".app" (builtins.baseNameOf path);
      dockSortToDarwinArrangement = {
        name = "name";
        dateadded = "date-added";
        datemodified = "date-modified";
        datecreated = "date-created";
        kind = "kind";
      };

      removeOtherCommands = concatMapStringsSep "\n" (other: ''
        if "$dockutil" --find ${escapeShellArg other} --section others >/dev/null 2>&1; then
          if ! "$dockutil" --remove ${escapeShellArg other} --section others --no-restart >/dev/null; then
            echo "warning: failed to remove stale Dock item ${other}" >&2
          fi
        fi
      '') removeOthers;

      positionedApps = lib.imap1 (position: app: { inherit app position; }) apps;
      positionedOthers = lib.imap1 (position: other: other // { inherit position; }) others;

      addAppCommands = concatMapStringsSep "\n" (
        { app, position }:
        ''
          if [ -e ${escapeShellArg app} ]; then
            if ! "$dockutil" --add ${escapeShellArg app} --replacing ${escapeShellArg (dockLabel app)} --position ${toString position} --section apps --no-restart >/dev/null; then
              echo "warning: failed to add Dock app ${app}" >&2
            fi
          else
            echo "warning: skipping missing Dock app ${app}" >&2
          fi
        ''
      ) positionedApps;

      addOtherCommands = concatMapStringsSep "\n" (other: ''
        if [ -e ${escapeShellArg other.path} ]; then
          if ! "$dockutil" --add ${escapeShellArg other.path} --replacing ${escapeShellArg (dockLabel other.path)} --position ${toString other.position} --section others --sort ${escapeShellArg other.sort} --no-restart >/dev/null; then
            echo "warning: failed to add Dock item ${other.path}" >&2
          fi
        else
          echo "warning: skipping missing Dock item ${other.path}" >&2
        fi
      '') positionedOthers;
    in
    mkMerge [
      (optionalAttrs hasDarwinDock {
        system.defaults.dock = {
          persistent-apps = map (app: { inherit app; }) apps;
          persistent-others = map (
            { path, sort }:
            {
              folder = {
                inherit path;
                arrangement = builtins.getAttr sort dockSortToDarwinArrangement;
              };
            }
          ) others;
        };
      })

      (optionalAttrs hasHomeActivation {
        home.activation = {
          ${activationName} = lib.hm.dag.entryAfter [ "nixcfgUserApplications" ] ''
            dockutil=${escapeShellArg dockutil}
            if [ ! -x "$dockutil" ]; then
              echo "warning: skipping Dock setup because dockutil is unavailable at $dockutil" >&2
              exit 0
            fi

            ${removeOtherCommands}
            ${addAppCommands}
            ${addOtherCommands}
            /usr/bin/killall Dock >/dev/null 2>&1 || true
          '';
        };
      })
    ];
}
