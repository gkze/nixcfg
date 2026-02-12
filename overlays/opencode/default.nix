{
  final,
  inputs,
  slib,
  system,
  ...
}:
{
  opencode = inputs.opencode.packages.${system}.opencode.overrideAttrs (old: {
    nativeBuildInputs =
      (old.nativeBuildInputs or [ ])
      ++ (with final; [
        findutils
        jq
        moreutils
        bun
      ]);
    # IMPORTANT: Keep this postPatch in place unless upstream removes
    # versionCheckHook/packageManager strictness. Removing it can reintroduce
    # Bun version mismatch failures when nixpkgs Bun lags/leads opencode.
    postPatch = (old.postPatch or "") + ''
      # Keep packageManager in sync with the Bun provided by nixpkgs so
      # versionCheckHook accepts the runtime Bun used in the build.
      bunVersion=$(bun -v | tr -d '\n')
      find . -name 'package.json' -exec sh -c '
        if jq -e ".packageManager" "$1" >/dev/null 2>&1; then
          jq --arg bunVersion "'"$bunVersion"'" ".packageManager = (\"bun@\" + \$bunVersion)" "$1" | sponge "$1"
        fi
      ' _ {} \;
    '';
    # @opentui/core is hoisted to root node_modules/ but the build script
    # resolves it from packages/opencode/node_modules/. Symlink it so
    # fs.realpathSync can find parser.worker.js at build time.
    preBuild = ''
      if [ -d node_modules/@opentui ] && [ ! -d packages/opencode/node_modules/@opentui ]; then
        chmod u+w packages/opencode/node_modules
        mkdir -p packages/opencode/node_modules/@opentui
        for pkg in node_modules/@opentui/*; do
          ln -s "../../../../$pkg" "packages/opencode/node_modules/@opentui/$(basename "$pkg")"
        done
      fi
    '';
    node_modules = old.node_modules.overrideAttrs (nodeOld: {
      outputHash = slib.sourceHashForPlatform "opencode" "nodeModulesHash" system;
      # bun 1.3.8+ no longer creates .bun/node_modules/, making the
      # upstream canonicalize/normalize scripts fail. Guard them so they
      # only run when .bun/node_modules actually exists.
      buildPhase =
        builtins.replaceStrings [ "bun --bun" ] [ "[ -d node_modules/.bun/node_modules ] && bun --bun" ]
          (nodeOld.buildPhase or "");
    });
  });
}
