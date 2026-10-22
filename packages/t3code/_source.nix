# Source discovery is shared across T3 consumers; build and hash policy stays local.
{ src, lib }:
let
  pname = "t3code";
  rootPackageJson = builtins.fromJSON (builtins.readFile "${builtins.toString src}/package.json");
  serverPackageJson = builtins.fromJSON (
    builtins.readFile "${builtins.toString src}/apps/server/package.json"
  );
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
  lockfileText =
    if builtins.pathExists (src + "/pnpm-lock.yaml") then
      builtins.readFile (src + "/pnpm-lock.yaml")
    else
      "";
  # pnpm installs `file:` tarball dependencies from the checkout, so the
  # dependency source must carry exactly the tarballs the lockfile resolves.
  localTarballs = lib.unique (
    map builtins.head (
      builtins.filter builtins.isList (builtins.split "tarball: file:([^,} ]+)" lockfileText)
    )
  );
  ancestorDirs =
    path:
    let
      parts = lib.init (lib.splitString "/" path);
    in
    lib.genList (count: lib.concatStringsSep "/" (lib.take (count + 1) parts)) (builtins.length parts);
  localTarballDirs = lib.unique (lib.concatMap ancestorDirs localTarballs);
  dependencySourceDirectories = [
    ""
  ]
  ++ localTarballDirs
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
          # pnpm records the hook file's checksum in the lockfile and refuses a
          # frozen install without it.
          ".pnpmfile.cjs"
          "package.json"
          "pnpm-lock.yaml"
          "pnpm-workspace.yaml"
        ]
        ++ localTarballs
        ++ map (dir: "${dir}/package.json") workspaceDirs
        ++ map (dir: "${dir}/package.json") mobileModulePackageDirs
      );
  };
in
{
  inherit
    pname
    src
    serverPackageJson
    dependencySource
    workspaceBuildShellDirs
    ;
}
