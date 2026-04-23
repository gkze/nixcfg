{
  bun,
  cacert,
  inputs,
  nodejs,
  outputs,
  stdenv,
  sourceHashPackageName ? "t3code",
  ...
}:
let
  pname = "t3code";
  src = inputs.t3code;
  inherit (stdenv.hostPlatform) system;
  serverPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/server/package.json");
  baseVersion = serverPackageJson.version;
  revSuffix = builtins.substring 0 7 (outputs.lib.flakeLock.t3code.locked.rev or "unknown");
  version = "${baseVersion}-main-${revSuffix}";

  bunTarget =
    {
      aarch64-darwin = {
        cpu = "arm64";
        os = "darwin";
      };
    }
    .${system} or (throw "packages/t3code/_shared.nix unsupported system ${system}");

  node_modules = stdenv.mkDerivation {
    pname = "${sourceHashPackageName}-node_modules";
    inherit version src;

    nativeBuildInputs = [
      bun
      cacert
    ];

    dontPatchShebangs = true;
    dontFixup = true;

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/home"
      mkdir -p "$HOME"
      export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
      export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"
      export BUN_INSTALL_CACHE_DIR="$TMPDIR/.bun-cache"

      bun install \
        --cpu="${bunTarget.cpu}" \
        --os="${bunTarget.os}" \
        --frozen-lockfile \
        --ignore-scripts \
        --no-progress

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      mkdir -p "$out"
      find . -type d -name node_modules -exec cp -R --parents {} "$out" \;

      runHook postInstall
    '';

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = outputs.lib.sourceHashForPlatform sourceHashPackageName "nodeModulesHash" system;
  };

  workspaceBuild = stdenv.mkDerivation {
    pname = "${pname}-workspace-build";
    inherit version src;

    nativeBuildInputs = [
      bun
      nodejs
      cacert
    ];

    strictDeps = true;

    env = {
      CI = "1";
      NODE_OPTIONS = "--max-old-space-size=6144";
    };

    postUnpack = ''
      chmod -R u+w source
    '';

    buildPhase = ''
      runHook preBuild

      export HOME="$TMPDIR/home"
      mkdir -p "$HOME"
      export SSL_CERT_FILE="${cacert}/etc/ssl/certs/ca-bundle.crt"
      export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"
      export TURBO_CACHE_DIR="$TMPDIR/.turbo-cache"
      export TURBO_TELEMETRY_DISABLED=1

      cp -a ${node_modules}/. .
      chmod -R u+w node_modules apps packages scripts

      patchShebangs node_modules
      find apps packages -type d -name node_modules -print | while IFS= read -r nested_node_modules; do
        patchShebangs "$nested_node_modules"
      done

      bun run build:desktop

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
    bunTarget
    node_modules
    pname
    src
    version
    workspaceBuild
    ;
}
