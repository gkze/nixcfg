{
  final,
  inputs,
  slib,
  system,
  ...
}:
let
  upstream = inputs.opencode.packages.${system}.opencode;
  source = slib.sourceEntry "opencode";
  # The flake input's packageManager owns the Bun release: its lockfile is
  # regenerated and verified with exactly that Bun, and other Bun releases may
  # re-resolve it and reject --frozen-lockfile.
  bun =
    import ../../lib/exact-bun.nix
      {
        inherit (final) bun fetchurl lib;
      }
      {
        packageManifest = final.lib.importJSON "${inputs.opencode}/package.json";
        packageName = "opencode";
        inherit source system;
        version =
          (source.pins or { }).bunVersion or (throw "opencode sources.json is missing pins.bunVersion");
      };
  node_modules = (upstream.node_modules.override { inherit bun; }).overrideAttrs (_: {
    outputHash = slib.sourceHashForPlatform "opencode" "nodeModulesHash" system;
  });
in
{
  opencode = (upstream.override { inherit bun node_modules; }).overrideAttrs (old: {
    passthru = (old.passthru or { }) // {
      buildBun = bun;
    };
    postPatch = (old.postPatch or "") + ''
      # Some package sources omit .github, but build scripts read this.
      # Prefer the authoritative team list from the flake input when present.
      if [ ! -f .github/TEAM_MEMBERS ]; then
        mkdir -p .github
        if [ -f ${inputs.opencode}/.github/TEAM_MEMBERS ]; then
          cp ${inputs.opencode}/.github/TEAM_MEMBERS .github/TEAM_MEMBERS
        else
          touch .github/TEAM_MEMBERS
        fi
      fi
    '';
    # Some workspace packages import hoisted dependencies via their own
    # package-local node_modules trees. Symlink the known offenders so Bun's
    # resolver can find them during the Nix build.
    preBuild = ''
      # @opentui/core is hoisted to root node_modules/ but the build script
      # resolves it from its package-local node_modules. Symlink it so
      # fs.realpathSync can find parser.worker.js at build time.
      ${final.stdenv.shell} ${./link-hoisted-opentui-packages.sh} node_modules \
        ${
          if old.meta.mainProgram == "opencode2" then "packages/cli" else "packages/opencode"
        }/node_modules
    '';
    # Keep the configured command stable across upstream's opencode2 rename.
    postInstall =
      (old.postInstall or "")
      + final.lib.optionalString (old.meta.mainProgram == "opencode2") ''
        ln -s opencode2 "$out/bin/opencode"
      '';
  });
}
