{
  bun,
  cacert,
  inputs,
  lib,
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
  nodeModulesVersion = "deps";
  childDirectoryNames =
    path: builtins.attrNames (lib.filterAttrs (_: type: type == "directory") (builtins.readDir path));
  workspaceDirs =
    lib.concatMap
      (
        parent:
        lib.optionals (builtins.pathExists (src + "/${parent}")) (
          map (name: "${parent}/${name}") (childDirectoryNames (src + "/${parent}"))
        )
      )
      [
        "apps"
        "packages"
      ]
    ++ lib.optional (builtins.pathExists (src + "/scripts/package.json")) "scripts";
  dependencySourceDirectories = [
    ""
  ]
  ++ lib.optionals (builtins.pathExists (src + "/apps")) [ "apps" ]
  ++ lib.optionals (builtins.pathExists (src + "/packages")) [ "packages" ]
  ++ workspaceDirs
  ++ lib.optional (builtins.pathExists (src + "/patches")) "patches";
  dependencySource = builtins.path {
    name = "${pname}-dependency-source";
    path = src;
    filter =
      path: type:
      let
        pathString = toString path;
        srcString = toString src;
        relativePath = if pathString == srcString then "" else lib.removePrefix "${srcString}/" pathString;
      in
      (type == "directory" && builtins.elem relativePath dependencySourceDirectories)
      || lib.hasPrefix "patches/" relativePath
      || builtins.elem relativePath (
        [
          "bun.lock"
          "bunfig.toml"
          "package.json"
        ]
        ++ map (dir: "${dir}/package.json") workspaceDirs
      );
  };

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
    version = nodeModulesVersion;
    src = dependencySource;

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
      find . -type d -name node_modules -prune -exec cp -R --parents {} "$out" \;

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
