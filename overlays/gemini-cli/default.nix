{
  inputs,
  slib,
  prev,
  ...
}:
{
  gemini-cli =
    let
      version = slib.getFlakeVersion "gemini-cli";
      npmDepsHash = slib.sourceHash "gemini-cli" "npmDepsHash";
      npmDeps = prev.fetchNpmDeps {
        src = inputs.gemini-cli;
        hash = npmDepsHash;
      };
    in
    prev.gemini-cli.overrideAttrs (oldAttrs: {
      inherit version npmDepsHash npmDeps;
      src = inputs.gemini-cli;
      disallowedReferences = [
        npmDeps
        prev.nodejs_22.python
      ];
      # Replace postPatch entirely for v0.26.0+ (upstream patterns changed)
      postPatch =
        let
          jq = "${prev.jq}/bin/jq";
          rmNodePty = "del(.optionalDependencies.\"node-pty\")";
          rg = prev.lib.getExe prev.ripgrep;
          schema = "packages/cli/src/config/settingsSchema.ts";
          # Sed pattern to change default: true -> false within a block
          disableDefault = "s/default: true/default: false/";
        in
        ''
          # Remove node-pty dependency from package.json
          ${jq} '${rmNodePty}' package.json > package.json.tmp
          mv package.json.tmp package.json

          # Remove node-pty dependency from packages/core/package.json
          ${jq} '${rmNodePty}' packages/core/package.json > tmp.json
          mv tmp.json packages/core/package.json

          # Fix ripgrep path for SearchText
          substituteInPlace packages/core/src/tools/ripGrep.ts \
            --replace-fail "await ensureRgPath();" "'${rg}';"

          # Disable auto-update defaults in settingsSchema.ts (v0.26.0+)
          sed -i '/enableAutoUpdate: {/,/}/ ${disableDefault}' ${schema}
          sed -i '/enableAutoUpdateNotification: {/,/}/ ${disableDefault}' \
            ${schema}
        '';
      # Remove files that reference python to keep it out of the closure
      postInstall = (oldAttrs.postInstall or "") + ''
        rm -rf $out/share/gemini-cli/node_modules/keytar/build
      '';
    });
}
