{
  sources,
  slib,
  prev,
  ...
}:
{
  gemini-cli =
    let
      inherit (sources.gemini-cli) version;
      src = prev.fetchFromGitHub {
        owner = "google-gemini";
        repo = "gemini-cli";
        tag = "v${version}";
        hash = slib.sourceHash "gemini-cli" "srcHash";
      };
      npmDepsHash = slib.sourceHash "gemini-cli" "npmDepsHash";
      npmDeps = prev.fetchNpmDeps {
        inherit src;
        hash = npmDepsHash;
      };
    in
    prev.gemini-cli.overrideAttrs (oldAttrs: {
      inherit
        version
        src
        npmDepsHash
        npmDeps
        ;
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

          # Build devtools first so CLI typecheck can resolve its declarations
          substituteInPlace scripts/build.js \
            --replace-fail "npm run build --workspaces" \
                           "npm run build --workspace=@google/gemini-cli-devtools && npm run build --workspaces"
        '';
      # Remove files that reference python to keep it out of the closure
      postInstall = (oldAttrs.postInstall or "") + ''
        rm -rf $out/share/gemini-cli/node_modules/keytar/build
        rm -rf $out/share/gemini-cli/node_modules/@google/gemini-cli-sdk
        rm -rf $out/share/gemini-cli/node_modules/@google/gemini-cli-devtools
        cp -r packages/devtools \
          $out/share/gemini-cli/node_modules/@google/gemini-cli-devtools
      '';
    });
}
