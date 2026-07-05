{
  bun,
  cacert,
  fetchPnpmDeps ? null,
  inputs,
  lib,
  nodejs,
  outputs,
  pnpm_10,
  pnpmConfigHook,
  stdenv,
  sourceHashPackageName ? "t3code",
  ...
}:
let
  pname = "t3code";
  src = inputs.t3code;
  inherit (stdenv.hostPlatform) system;
  rootPackageJson = builtins.fromJSON (builtins.readFile "${src}/package.json");
  serverPackageJson = builtins.fromJSON (builtins.readFile "${src}/apps/server/package.json");
  baseVersion = serverPackageJson.version;
  revSuffix = builtins.substring 0 7 (outputs.lib.flakeLock.t3code.locked.rev or "unknown");
  version = "${baseVersion}-main-${revSuffix}";
  nodeModulesVersion = "deps";
  hasBunLock = builtins.pathExists (src + "/bun.lock");
  pnpm = pnpm_10.override { inherit nodejs; };
  childDirectoryNames =
    path: builtins.attrNames (lib.filterAttrs (_: type: type == "directory") (builtins.readDir path));
  workspaceParentNames = [
    "apps"
    "infra"
    "packages"
  ];
  workspaceParentDirs = builtins.filter (
    parent: builtins.pathExists (src + "/${parent}")
  ) workspaceParentNames;
  nestedWorkspaceDirs = lib.concatMap (
    parent: map (name: "${parent}/${name}") (childDirectoryNames (src + "/${parent}"))
  ) workspaceParentDirs;
  rootWorkspaces = rootPackageJson.workspaces or { };
  rootWorkspacePackagePatterns =
    if builtins.isList rootWorkspaces then rootWorkspaces else rootWorkspaces.packages or [ ];
  explicitRootWorkspaceDirs = builtins.filter (
    dir: !lib.hasInfix "*" dir && builtins.pathExists (src + "/${dir}/package.json")
  ) rootWorkspacePackagePatterns;
  topLevelWorkspaceNames = [
    "oxlint-plugin-t3code"
    "scripts"
  ];
  topLevelWorkspaceDirs = builtins.filter (
    dir: builtins.pathExists (src + "/${dir}/package.json")
  ) topLevelWorkspaceNames;
  mobileModuleRoot = "apps/mobile/modules";
  mobileModulePackageDirs = lib.optionals (builtins.pathExists (src + "/${mobileModuleRoot}")) (
    map (name: "${mobileModuleRoot}/${name}") (childDirectoryNames (src + "/${mobileModuleRoot}"))
  );
  workspaceDirs = lib.unique (
    nestedWorkspaceDirs ++ explicitRootWorkspaceDirs ++ topLevelWorkspaceDirs
  );
  workspaceBuildDirectories = lib.unique (
    workspaceParentDirs ++ explicitRootWorkspaceDirs ++ topLevelWorkspaceDirs
  );
  workspaceBuildShellDirs = lib.escapeShellArgs workspaceBuildDirectories;
  dependencySourceDirectories = [
    ""
  ]
  ++ workspaceParentDirs
  ++ workspaceDirs
  ++ lib.optional (builtins.pathExists (src + "/${mobileModuleRoot}")) mobileModuleRoot
  ++ mobileModulePackageDirs
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
          "package.json"
        ]
        ++ lib.optionals hasBunLock [
          "bun.lock"
          "bunfig.toml"
        ]
        ++ lib.optionals (!hasBunLock) [
          "pnpm-lock.yaml"
          "pnpm-workspace.yaml"
        ]
        ++ map (dir: "${dir}/package.json") workspaceDirs
        ++ map (dir: "${dir}/package.json") mobileModulePackageDirs
      );
  };

  bunTarget =
    {
      aarch64-darwin = {
        cpu = "arm64";
        os = "darwin";
      };
      x86_64-darwin = {
        cpu = "x64";
        os = "darwin";
      };
      aarch64-linux = {
        cpu = "arm64";
        os = "linux";
      };
      x86_64-linux = {
        cpu = "x64";
        os = "linux";
      };
    }
    .${system} or (throw "Unsupported system ${system} for ${pname}");

  pnpm_node_modules =
    let
      args = {
        pname = "${sourceHashPackageName}-node_modules";
        version = nodeModulesVersion;
        src = dependencySource;
        inherit pnpm;
        fetcherVersion = 3;
        hash = outputs.lib.sourceHashForPlatform sourceHashPackageName "nodeModulesHash" system;
      };
    in
    if fetchPnpmDeps != null then fetchPnpmDeps args else pnpm.fetchDeps args;

  bun_node_modules = stdenv.mkDerivation {
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

  node_modules = if hasBunLock then bun_node_modules else pnpm_node_modules;

  workspaceBuild = stdenv.mkDerivation ({
    pname = "${pname}-workspace-build";
    inherit version src;

    nativeBuildInputs = [
      bun
      cacert
      nodejs
    ]
    ++ lib.optionals (!hasBunLock) [
      pnpm
      pnpmConfigHook
    ];

    strictDeps = true;

    env = {
      CI = "1";
      NODE_OPTIONS = "--max-old-space-size=6144";
      npm_config_manage_package_manager_versions = "false";
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
      export XDG_CACHE_HOME="$TMPDIR/xdg-cache"
      export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
      export XDG_DATA_HOME="$TMPDIR/xdg-data"
      export XDG_STATE_HOME="$TMPDIR/xdg-state"
      export npm_config_manage_package_manager_versions=false
      mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"
      ${lib.optionalString (!hasBunLock) "pnpm config set manage-package-manager-versions false"}

      ${lib.optionalString hasBunLock "cp -a ${node_modules}/. ."}
      chmod -R u+w node_modules ${workspaceBuildShellDirs}

      patchShebangs node_modules
      find ${workspaceBuildShellDirs} -type d -name node_modules -print | while IFS= read -r nested_node_modules; do
        patchShebangs "$nested_node_modules"
      done

      ${if hasBunLock then "bun run build:desktop" else "pnpm run build:desktop"}

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
  } // lib.optionalAttrs (!hasBunLock) {
    pnpmDeps = node_modules;
  });
in
{
  inherit
    bunTarget
    node_modules
    pname
    pnpm
    src
    version
    workspaceBuild
    ;
}
