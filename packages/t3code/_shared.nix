{
  cacert,
  fetchPnpmDeps ? null,
  inputs,
  lib,
  nodejs,
  outputs,
  pnpm_11,
  pnpmConfigHook,
  stdenv,
  sourceHashPackageName ? "t3code",
  t3codeWorkspaceSource ? null,
  ...
}:
let
  src = inputs.t3code;
  inherit (stdenv.hostPlatform) system;
  sourceData =
    if t3codeWorkspaceSource != null && toString t3codeWorkspaceSource.src == toString src then
      t3codeWorkspaceSource
    else
      import ./_source.nix { inherit src lib; };
  inherit (sourceData)
    pname
    serverPackageJson
    dependencySource
    workspaceBuildShellDirs
    ;
  baseVersion = serverPackageJson.version;
  revSuffix = builtins.substring 0 7 (outputs.lib.flakeLock.t3code.locked.rev or "unknown");
  version = "${baseVersion}-main-${revSuffix}";
  nodeModulesVersion = "deps";
  pnpm = pnpm_11.override { nodejs-slim = nodejs; };

  node_modules =
    let
      args = {
        pname = "${sourceHashPackageName}-node_modules";
        version = nodeModulesVersion;
        src = dependencySource;
        inherit pnpm;
        fetcherVersion = 4;
        pnpmInstallFlags = [
          "--fetch-retries=5"
          "--network-concurrency=1"
        ];
        hash = outputs.lib.sourceHashForPlatform sourceHashPackageName "nodeModulesHash" system;
      };
    in
    if fetchPnpmDeps != null then fetchPnpmDeps args else pnpm.fetchDeps args;

  workspaceBuild = stdenv.mkDerivation {
    pname = "${pname}-workspace-build";
    inherit version src;
    pnpmDeps = node_modules;

    nativeBuildInputs = [
      cacert
      nodejs
      pnpm
      pnpmConfigHook
    ];

    strictDeps = true;

    env = {
      CI = "1";
      NODE_OPTIONS = "--max-old-space-size=6144";
      pnpm_config_pm_on_fail = "ignore";
    };

    postUnpack = ''
      chmod -R u+w source
    '';

    # fff-node 0.9.4 exposes only an ESM import entry. These builds run under
    # Node/Electron, not the upstream single-executable runtime.
    patches = [ ./fff-node-esm.patch ];

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/home"
      mkdir -p "$HOME"
      export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
      export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"
      export TURBO_CACHE_DIR="$TMPDIR/.turbo-cache"
      export TURBO_TELEMETRY_DISABLED=1
      export XDG_CACHE_HOME="$TMPDIR/xdg-cache"
      export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
      export XDG_DATA_HOME="$TMPDIR/xdg-data"
      export XDG_STATE_HOME="$TMPDIR/xdg-state"
      mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"

      chmod -R u+w node_modules ${workspaceBuildShellDirs}

      patchShebangs node_modules
      find ${workspaceBuildShellDirs} -type d -name node_modules -print | while IFS= read -r nested_node_modules; do
        patchShebangs "$nested_node_modules"
      done

      pnpm run build:desktop

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      mkdir -p "$out/apps/server" "$out/apps/web" "$out/apps/desktop"
      cp -R apps/server/dist "$out/apps/server/dist"
      cp -R apps/web/dist "$out/apps/web/dist"
      cp -R apps/desktop/dist-electron "$out/apps/desktop/dist-electron"
      cp -R apps/desktop/resources "$out/apps/desktop/resources"

      runHook postInstall
    '';
  };
in
{
  inherit
    node_modules
    pname
    pnpm
    src
    version
    workspaceBuild
    ;
}
