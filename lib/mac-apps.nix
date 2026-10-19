{ lib, pkgs }:
let
  inherit (builtins)
    attrValues
    concatLists
    filter
    mapAttrs
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
    mapAttrs'
    mkDefault
    mkOption
    nameValuePair
    optionalAttrs
    optionalString
    types
    unique
    ;

  tryPackageValue =
    value:
    let
      result = builtins.tryEval value;
    in
    if result.success then result.value else null;

  packageLabel =
    package:
    let
      label = tryPackageValue (package.pname or package.name or "<unknown package>");
    in
    if label != null then label else "<unknown package>";

  packageOutPath =
    package: tryPackageValue (if package ? outPath then toString package.outPath else null);

  packageBundleName =
    package: tryPackageValue (attrByPath [ "passthru" "macApp" "bundleName" ] null package);

  packageIndexKey =
    value: if builtins.isString value then builtins.unsafeDiscardStringContext value else null;

  normalizedCandidate =
    package:
    let
      bundleName = packageBundleName package;
      outPath = packageOutPath package;
    in
    {
      inherit
        bundleName
        outPath
        package
        ;
      bundleNameKey = packageIndexKey bundleName;
      label = packageLabel package;
      outPathKey = packageIndexKey outPath;
    };

  indexedValues =
    values:
    let
      go =
        index: remaining:
        if remaining == [ ] then
          [ ]
        else
          [
            {
              inherit index;
              value = builtins.head remaining;
            }
          ]
          ++ go (index + 1) (builtins.tail remaining);
    in
    go 0 values;

  indexPackagesBy =
    keyFor: packages:
    let
      values = builtins.groupBy keyFor (filter (package: keyFor package != null) packages);
    in
    {
      inherit values;
      nonEmpty = builtins.attrNames values != [ ];
    };

  packagesAt =
    index: key:
    if !index.nonEmpty then
      [ ]
    else if key != null then
      index.values.${key} or [ ]
    else
      [ ];

  mergeIndexedPackages =
    left: right:
    if left == [ ] then
      right
    else if right == [ ] then
      left
    else
      let
        leftHead = builtins.head left;
        rightHead = builtins.head right;
      in
      if leftHead.index < rightHead.index then
        [ leftHead ] ++ mergeIndexedPackages (builtins.tail left) right
      else if rightHead.index < leftHead.index then
        [ rightHead ] ++ mergeIndexedPackages left (builtins.tail right)
      else
        [ leftHead ] ++ mergeIndexedPackages (builtins.tail left) (builtins.tail right);

  formatPackageListConflict =
    conflict:
    let
      inherit (conflict) candidateLabel managedLabel;
    in
    "- ${conflict.entry.bundleName} (${managedLabel}) also appears in ${conflict.label}"
    + optionalString (candidateLabel != managedLabel) " as ${candidateLabel}"
    + ".";

  managedAppsPackageListConflicts =
    entries: packageLists:
    let
      normalizedEntries = map (
        indexed:
        let
          package = indexed.value.package;
          outPath = packageOutPath package;
        in
        {
          bundleNameKey = packageIndexKey indexed.value.bundleName;
          entry = indexed.value;
          inherit (indexed) index;
          label = packageLabel package;
          outPathKey = packageIndexKey outPath;
        }
      ) (indexedValues entries);
      normalizedPackageLists = map (
        packageList:
        let
          packages = map (
            indexed:
            normalizedCandidate indexed.value
            // {
              inherit (indexed) index;
            }
          ) (indexedValues packageList.packages);
        in
        {
          inherit (packageList) label;
          byBundleName = indexPackagesBy (package: package.bundleNameKey) packages;
          byOutPath = indexPackagesBy (package: package.outPathKey) packages;
        }
      ) packageLists;
      conflictsFor =
        entry: packageList:
        let
          candidates = mergeIndexedPackages (packagesAt packageList.byOutPath entry.outPathKey) (
            packagesAt packageList.byBundleName entry.bundleNameKey
          );
        in
        map (candidate: {
          candidate = candidate.package;
          candidateLabel = candidate.label;
          inherit (entry) entry;
          inherit (packageList) label;
          managedLabel = entry.label;
        }) candidates;
    in
    concatLists (
      map (
        entry: concatLists (map (packageList: conflictsFor entry packageList) normalizedPackageLists)
      ) normalizedEntries
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
        + "nixcfg.macApps.applications."
      );

  scopeTargetDirectory =
    {
      homeDirectory,
      scope,
    }:
    {
      system = "/Applications";
      user = "${homeDirectory}/Applications";
    }
    .${scope};

  commonApplicationOptions =
    {
      bundleNameDescription,
      packageDescription,
      packageExample ? null,
      scopeDefault ? null,
      scopeDescription,
    }:
    {
      package = mkOption (
        {
          type = types.package;
          description = packageDescription;
        }
        // optionalAttrs (packageExample != null) { example = packageExample; }
      );

      bundleName = mkOption {
        type = types.str;
        description = bundleNameDescription;
      };

      scope = mkOption (
        {
          type = types.enum [
            "user"
            "system"
          ];
          description = scopeDescription;
        }
        // optionalAttrs (scopeDefault != null) { default = scopeDefault; }
      );

      mode = mkOption {
        type = types.enum [
          "copy"
          "symlink"
        ];
        description = ''
          Whether to copy the app bundle out of the store or symlink it into
          the scoped application directory.
        '';
      };

      preventDowngrade = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Whether activation must refuse to replace a newer installed bundle
          with an older version from the declared package.
        '';
      };
    };

  applicationEntryType = types.submodule (
    { config, ... }:
    {
      options = commonApplicationOptions {
        packageDescription = "Package containing a macOS .app bundle under /Applications in its output.";
        packageExample = literalExpression "pkgs.wispr-flow";
        bundleNameDescription = "Target bundle name written into the scoped application directory.";
        scopeDefault = "user";
        scopeDescription = ''
          Application installation scope. User-scoped apps are materialized under
          ~/Applications; system-scoped apps are materialized under /Applications.
        '';
      };

      config = {
        bundleName = mkDefault (requiredMacAppAttr config.package "bundleName");
        mode = mkDefault (attrByPath [ "passthru" "macApp" "installMode" ] "copy" config.package);
      };
    }
  );

  resolvedApplicationEntryType = types.submodule {
    options =
      commonApplicationOptions {
        packageDescription = "Package containing the source macOS application bundle.";
        bundleNameDescription = "Target bundle name.";
        scopeDescription = "Resolved application installation scope.";
      }
      // {
        sourcePath = mkOption {
          type = types.str;
          description = "Source application bundle path inside the package output.";
        };

        targetDirectory = mkOption {
          type = types.str;
          description = "Directory where the application bundle is materialized.";
        };

        path = mkOption {
          type = types.str;
          description = "Full materialized application bundle path.";
        };

        targetPath = mkOption {
          type = types.str;
          description = "Full materialized application bundle path.";
        };
      };
  };

  packageNamesForExclusion =
    package:
    filter (name: name != null) [
      (package.pname or null)
      (package.name or null)
    ];

  applicationEntryValues =
    applications: if builtins.isAttrs applications then attrValues applications else applications;

  applicationBundleNames =
    applications: map (entry: entry.bundleName) (applicationEntryValues applications);

  applicationTargetPaths =
    applications: map (entry: entry.targetPath) (applicationEntryValues applications);

  entryPackageNamesForExclusion =
    entry:
    (packageNamesForExclusion entry.package)
    ++ (if entry ? excludePackageName then [ entry.excludePackageName ] else [ ])
    ++ (entry.excludePackageNames or [ ]);

  managedMacAppRoutingProjection = managedMacAppRouting: {
    excludePackagesByName = unique (
      concatLists (map entryPackageNamesForExclusion (attrValues managedMacAppRouting))
    );
    applications = mapAttrs' (
      name: entry:
      nameValuePair name (
        builtins.removeAttrs entry [
          "excludePackageName"
          "excludePackageNames"
        ]
      )
    ) managedMacAppRouting;
  };

  resolveApplications =
    {
      applications,
      homeDirectory,
    }:
    mapAttrs (
      _name: entry:
      let
        targetDirectory = scopeTargetDirectory {
          inherit homeDirectory;
          inherit (entry) scope;
        };
        sourcePath = "${entry.package}/${requiredMacAppAttr entry.package "bundleRelPath"}";
        targetPath = "${targetDirectory}/${entry.bundleName}";
      in
      {
        inherit (entry)
          bundleName
          mode
          package
          preventDowngrade
          scope
          ;
        inherit
          sourcePath
          targetDirectory
          targetPath
          ;
        path = targetPath;
      }
    ) applications;

  applicationsForScope =
    scope: resolvedApplications:
    attrValues (lib.filterAttrs (_name: entry: entry.scope == scope) resolvedApplications);

  pythonExe =
    let
      python = attrByPath [ "python3" ] null pkgs;
      pythonWithPackages =
        if python != null then python.withPackages (pythonPackages: [ pythonPackages.packaging ]) else null;
    in
    if pythonWithPackages != null then getExe pythonWithPackages else "python3";

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
rec {
  inherit
    applicationBundleNames
    applicationEntryType
    applicationTargetPaths
    applicationsForScope
    managedMacAppRoutingProjection
    resolveApplications
    ;

  applicationsOption = mkOption {
    type = types.attrsOf applicationEntryType;
    default = { };
    description = "macOS application bundles to manage by scope.";
    example = literalExpression ''
      {
        wispr-flow.package = pkgs.wispr-flow;
        google-drive = {
          package = pkgs.google-drive;
          scope = "system";
        };
      }
    '';
  };

  resolvedOption = mkOption {
    type = types.attrsOf resolvedApplicationEntryType;
    default = { };
    description = "Resolved macOS application bundle routing, including full materialized paths.";
  };

  uniqueBundleNamesAssertion =
    applications:
    let
      entries = applicationEntryValues applications;
    in
    {
      assertion =
        builtins.length (unique (map (entry: entry.bundleName) entries)) == builtins.length entries;
      message = "nixcfg.macApps.applications must not contain duplicate bundleName values.";
    };

  managedAppsNotInPackageListsAssertion =
    {
      applications ? null,
      entries ? null,
      packageLists,
    }:
    let
      managedEntries = if applications != null then applicationEntryValues applications else entries;
      conflicts = managedAppsPackageListConflicts managedEntries packageLists;
      conflictLines = unique (map formatPackageListConflict conflicts);
    in
    {
      assertion = conflicts == [ ];
      message = concatStringsSep "\n" (
        [
          ("nixcfg.macApps.applications packages must not also appear in " + "other installed package lists.")
        ]
        ++ conflictLines
      );
    };

  removeProfileCopiesScript =
    {
      bundleNames,
      targetDirectory,
    }:
    callMacAppsHelper "remove-profile-copies" {
      inherit targetDirectory;
      bundleNames = unique bundleNames;
    };

  profileBundleLeakAuditScript =
    {
      packagePaths,
      managedBundleNames,
      label ? "home.packages",
    }:
    callMacAppsHelper "profile-bundle-leak-audit" {
      inherit label packagePaths;
      managedBundleNames = unique managedBundleNames;
    };

  applicationsScript =
    {
      entries,
      stateDirectory,
      stateName,
      writable,
      targetDirectory ? "/Applications",
    }:
    let
      bundleSourcePath =
        entry: entry.sourcePath or "${entry.package}/${requiredMacAppAttr entry.package "bundleRelPath"}";
      helperEntries = map (entry: {
        inherit (entry) bundleName mode;
        preventDowngrade = entry.preventDowngrade or false;
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

  systemApplicationsScript = applicationsScript;
}
