{ lib, pkgs }:
let
  inherit (builtins)
    concatLists
    filter
    readFile
    toFile
    toJSON
    ;
  inherit (lib)
    attrByPath
    concatStringsSep
    escapeShellArg
    getExe
    literalExpression
    mkDefault
    mkOption
    optionalString
    types
    unique
    ;

  packageLabel = package: package.pname or package.name or "<unknown package>";

  packageOutPath = package: if package ? outPath then toString package.outPath else null;

  packageBundleName = package: attrByPath [ "passthru" "macApp" "bundleName" ] null package;

  entryConflictsWithPackage =
    entry: candidate:
    let
      entryOutPath = packageOutPath entry.package;
      candidateOutPath = packageOutPath candidate;
      candidateBundleName = packageBundleName candidate;
    in
    (entryOutPath != null && candidateOutPath != null && entryOutPath == candidateOutPath)
    || (candidateBundleName != null && entry.bundleName == candidateBundleName);

  formatPackageListConflict =
    conflict:
    let
      managedLabel = packageLabel conflict.entry.package;
      candidateLabel = packageLabel conflict.candidate;
    in
    "- ${conflict.entry.bundleName} (${managedLabel}) also appears in ${conflict.label}"
    + optionalString (candidateLabel != managedLabel) " as ${candidateLabel}"
    + ".";

  managedAppsPackageListConflicts =
    entries: packageLists:
    concatLists (
      map (
        entry:
        concatLists (
          map (
            packageList:
            map (candidate: {
              inherit entry candidate;
              inherit (packageList) label;
            }) (filter (candidate: entryConflictsWithPackage entry candidate) packageList.packages)
          ) packageLists
        )
      ) entries
    );

  requiredMacAppAttr =
    package: attr:
    let
      value = attrByPath [ "passthru" "macApp" attr ] null package;
    in
    if value != null then
      value
    else
      throw (
        "Package '${packageLabel package}' must define "
        + "passthru.macApp.${attr} to be used with "
        + "nixcfg.macApps.systemApplications."
      );

  applicationEntryType = types.submodule (
    { config, ... }:
    {
      options = {
        package = mkOption {
          type = types.package;
          description = "Package containing a macOS .app bundle under /Applications in its output.";
          example = literalExpression "pkgs.wispr-flow";
        };

        bundleName = mkOption {
          type = types.str;
          description = "Target bundle name written into /Applications.";
        };

        mode = mkOption {
          type = types.enum [
            "copy"
            "symlink"
          ];
          description = ''
            Whether to copy the app bundle out of the store or symlink it into
            /Applications.
          '';
        };
      };

      config = {
        bundleName = mkDefault (requiredMacAppAttr config.package "bundleName");
        mode = mkDefault (attrByPath [ "passthru" "macApp" "installMode" ] "symlink" config.package);
      };
    }
  );

  managedMacAppRoutingProjection = managedMacAppRouting: {
    excludePackagesByName = builtins.catAttrs "excludePackageName" managedMacAppRouting;
    systemApplications = map (
      entry: builtins.removeAttrs entry [ "excludePackageName" ]
    ) managedMacAppRouting;
  };

  pythonExe =
    let
      python = attrByPath [ "python3" ] null pkgs;
    in
    if python != null then getExe python else "python3";

  writeText =
    name: text:
    let
      writer = attrByPath [ "writeText" ] null pkgs;
    in
    if writer != null then writer name text else toFile name text;

  macAppsHelper = writeText "nixcfg-mac-apps-helper.py" (readFile ./mac_apps_helper.py);

  callMacAppsHelper =
    command: payload:
    let
      payloadFile = writeText "nixcfg-mac-apps-${command}.json" (toJSON payload);
    in
    ''
      ${escapeShellArg pythonExe} ${escapeShellArg (toString macAppsHelper)} \
        ${escapeShellArg command} ${escapeShellArg (toString payloadFile)}
    '';
in
{
  inherit applicationEntryType managedMacAppRoutingProjection;

  systemApplicationsOption = mkOption {
    type = types.listOf applicationEntryType;
    default = [ ];
    description = "macOS application bundles to manage directly under /Applications.";
    example = literalExpression ''
      [
        { package = pkgs.wispr-flow; }
      ]
    '';
  };

  uniqueBundleNamesAssertion = entries: {
    assertion =
      builtins.length (unique (map (entry: entry.bundleName) entries)) == builtins.length entries;
    message = "nixcfg.macApps.systemApplications must not contain duplicate bundleName values.";
  };

  managedAppsNotInPackageListsAssertion =
    {
      entries,
      packageLists,
    }:
    let
      conflicts = managedAppsPackageListConflicts entries packageLists;
      conflictLines = unique (map formatPackageListConflict conflicts);
    in
    {
      assertion = conflicts == [ ];
      message = concatStringsSep "\n" (
        [
          (
            "nixcfg.macApps.systemApplications packages must not also appear in "
            + "other installed package lists."
          )
        ]
        ++ conflictLines
      );
    };

  profileBundleLeakAuditScript =
    {
      packagePaths,
      managedBundleNames,
      label ? "home.packages",
    }:
    let
      uniqueManagedBundleNames = unique managedBundleNames;
    in
    callMacAppsHelper "profile-bundle-leak-audit" {
      inherit label packagePaths;
      managedBundleNames = uniqueManagedBundleNames;
    };

  systemApplicationsScript =
    {
      entries,
      stateDirectory,
      stateName,
      writable,
      targetDirectory ? "/Applications",
    }:
    let
      bundleSourcePath = entry: "${entry.package}/${requiredMacAppAttr entry.package "bundleRelPath"}";
      helperEntries = map (entry: {
        inherit (entry) bundleName mode;
        sourcePath = bundleSourcePath entry;
      }) entries;
    in
    callMacAppsHelper "system-applications" {
      inherit
        stateDirectory
        stateName
        targetDirectory
        writable
        ;
      entries = helperEntries;
      rsyncPath = getExe pkgs.rsync;
    };
}
