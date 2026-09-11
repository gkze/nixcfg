{
  final,
  inputs,
  slib,
  system,
  ...
}:
{
  opencode = inputs.opencode.packages.${system}.opencode.overrideAttrs (old: {
    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ final.python3 ];
    passthru = (old.passthru or { }) // {
      # Upstream owns the Bun release used by its dependency installer and CLI.
      buildBun =
        final.lib.findFirst (input: (input.pname or "") == "bun")
          (throw "OpenCode's dependency installer does not expose its Bun runtime")
          old.node_modules.nativeBuildInputs;
    };
    # IMPORTANT: Keep this postPatch in place unless upstream removes
    # versionCheckHook/packageManager strictness. Removing it can reintroduce
    # Bun version mismatch failures when nixpkgs Bun lags/leads opencode.
    postPatch = (old.postPatch or "") + ''
      # Keep packageManager in sync with the selected build-time Bun so
      # versionCheckHook accepts the runtime Bun used in the build.
      bunVersion="$(bun -v | tr -d '\n')"
      ${final.lib.getExe final.python3} ${./sync_package_manager_bun_version.py} \
        . \
        "$bunVersion"

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
    node_modules = old.node_modules.overrideAttrs (nodeOld: {
      nativeBuildInputs = (nodeOld.nativeBuildInputs or [ ]) ++ [ final.python3 ];
      preBuild = (nodeOld.preBuild or "") + ''
        # Bun re-resolves the branch-based ghostty-web dependency and mutates
        # bun.lock even under --frozen-lockfile. Pin the workspace manifest to
        # the commit already recorded in bun.lock so the install stays
        # reproducible during hash computation.
        ${final.lib.getExe final.python3} ${./pin_ghostty_web_ref.py} .
      '';
      outputHash = slib.sourceHashForPlatform "opencode" "nodeModulesHash" system;
    });
  });
}
