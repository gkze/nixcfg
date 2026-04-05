{ lib, pkgs }:
let
  inherit (builtins) concatLists filter;
  inherit (lib)
    attrByPath
    concatMapStringsSep
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
      throw "Package '${packageLabel package}' must define passthru.macApp.${attr} to be used with nixcfg.macApps.systemApplications.";

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
          description = "Whether to copy the app bundle out of the store or symlink it into /Applications.";
        };
      };

      config = {
        bundleName = mkDefault (requiredMacAppAttr config.package "bundleName");
        mode = mkDefault (attrByPath [ "passthru" "macApp" "installMode" ] "symlink" config.package);
      };
    }
  );

  quotedLines = values: concatMapStringsSep "\n" (value: "    ${escapeShellArg value}") values;
in
{
  inherit applicationEntryType;

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
          "nixcfg.macApps.systemApplications packages must not also appear in other installed package lists."
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
    ''
            packageListLabel=${escapeShellArg label}

            managedBundleNames=(
      ${quotedLines uniqueManagedBundleNames}
            )

            packagePaths=(
      ${quotedLines packagePaths}
            )

            bundle_is_managed() {
              local needle="$1"
              local managedBundleName

              for managedBundleName in "''${managedBundleNames[@]}"; do
                if [ "$managedBundleName" = "$needle" ]; then
                  return 0
                fi
              done

              return 1
            }

            offendingBundles=()

            for packagePath in "''${packagePaths[@]}"; do
              if [ ! -d "$packagePath/Applications" ]; then
                continue
              fi

              for appBundle in "$packagePath"/Applications/*.app; do
                if [ "$appBundle" = "$packagePath/Applications/*.app" ] || [ ! -d "$appBundle" ]; then
                  continue
                fi

                bundleName="''${appBundle##*/}"
                if bundle_is_managed "$bundleName"; then
                  offendingBundles+=("$bundleName <= $packagePath")
                fi
              done
            done

            if [ "''${#offendingBundles[@]}" -ne 0 ]; then
              echo "Managed macOS app bundles must not be exposed through $packageListLabel." >&2
              echo "Move those packages out of $packageListLabel so /Applications stays the only mutable app-bundle surface." >&2
              printf ' - %s\n' "''${offendingBundles[@]}" >&2
              exit 1
            fi
    '';

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
      rsyncModeFlag = if writable then "--chmod=+w" else "--chmod=-w";
      stateFile = "${stateDirectory}/${stateName}.txt";
      installCommands = concatMapStringsSep "\n" (entry: ''
        install_managed_app ${escapeShellArg entry.mode} ${escapeShellArg (bundleSourcePath entry)} ${escapeShellArg "${targetDirectory}/${entry.bundleName}"}
      '') entries;
    in
    ''
            targetDirectory=${escapeShellArg targetDirectory}
            stateDirectory=${escapeShellArg stateDirectory}
            stateFile=${escapeShellArg stateFile}

            currentApps=(
      ${quotedLines (map (entry: entry.bundleName) entries)}
            )

            app_in_current_set() {
              local needle="$1"
              local app

              for app in "''${currentApps[@]}"; do
                if [ "$app" = "$needle" ]; then
                  return 0
                fi
              done

              return 1
            }

            app_in_other_manifests() {
              local needle="$1"
              local manifest
              local managedApp

              for manifest in "$stateDirectory"/*.txt; do
                if [ "$manifest" = "$stateDirectory/*.txt" ] || [ "$manifest" = "$stateFile" ] || [ ! -f "$manifest" ]; then
                  continue
                fi

                while IFS= read -r managedApp || [ -n "$managedApp" ]; do
                  if [ "$managedApp" = "$needle" ]; then
                    return 0
                  fi
                done < "$manifest"
              done

              return 1
            }

            install_managed_app() {
              local mode="$1"
              local src="$2"
              local dst="$3"

              if [ ! -d "$src" ]; then
                echo "Expected macOS app bundle at $src" >&2
                exit 1
              fi

              echo "setting up $dst..." >&2

              if [ "$mode" = "symlink" ]; then
                if [ -e "$dst" ] || [ -L "$dst" ]; then
                  rm -rf -- "''${dst:?}"
                fi
                ln -s "$src" "$dst"
                return
              fi

              if [ -L "$dst" ] || { [ -e "$dst" ] && [ ! -d "$dst" ]; }; then
                rm -rf -- "''${dst:?}"
              fi

              mkdir -p "$dst"

              rsyncFlags=(
                --checksum
                --copy-unsafe-links
                --archive
                --delete
                ${rsyncModeFlag}
                --no-group
                --no-owner
              )

              ${getExe pkgs.rsync} "''${rsyncFlags[@]}" "$src/" "$dst"
            }

            mkdir -p "$targetDirectory" "$stateDirectory"

            if [ -f "$stateFile" ]; then
              while IFS= read -r managedApp || [ -n "$managedApp" ]; do
                if [ -n "$managedApp" ] && ! app_in_current_set "$managedApp"; then
                  if app_in_other_manifests "$managedApp"; then
                    echo "keeping $targetDirectory/$managedApp because another manifest still manages it..." >&2
                  else
                    echo "removing stale managed app $targetDirectory/$managedApp..." >&2
                    rm -rf -- "''${targetDirectory:?}/''${managedApp:?}"
                  fi
                fi
              done < "$stateFile"
            fi

            ${installCommands}

            : > "$stateFile"
            for app in "''${currentApps[@]}"; do
              printf '%s\n' "$app" >> "$stateFile"
            done
    '';
}
