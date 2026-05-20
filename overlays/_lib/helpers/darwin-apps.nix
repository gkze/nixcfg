{ prev, system, ... }:
let
  mkAppNames =
    {
      pname,
      appName ? pname,
      executableName ? null,
    }:
    let
      capitalizedAppName =
        (prev.lib.toUpper (builtins.substring 0 1 appName)) + builtins.substring 1 (-1) appName;
    in
    {
      inherit capitalizedAppName;
      resolvedExecutableName = if executableName == null then capitalizedAppName else executableName;
    };
in
{
  mkDmgApp =
    {
      pname,
      info,
      appName ? pname,
      executableName ? null,
      postInstallApp ? "",
      codesignApp ? false,
      macApp ? { },
      meta ? { },
    }:
    let
      arch = if system == "aarch64-darwin" then "aarch64" else "x86_64";
      inherit (mkAppNames { inherit pname appName executableName; })
        capitalizedAppName
        resolvedExecutableName
        ;
    in
    prev.stdenvNoCC.mkDerivation {
      inherit pname;
      inherit (info) version;
      inherit meta;

      passthru.macApp = {
        bundleName = "${capitalizedAppName}.app";
        bundleRelPath = "Applications/${capitalizedAppName}.app";
        installMode = "symlink";
      }
      // macApp;

      src = prev.fetchurl {
        name = "${capitalizedAppName}_${info.version}_${arch}.dmg";
        url = info.urls.${system};
        hash = info.hashes.${system};
      };

      nativeBuildInputs = [
        prev.darwin.xattr
        prev.undmg
      ];

      sourceRoot = "${capitalizedAppName}.app";

      unpackCmd = ''
        runHook preUnpack
        undmg "$src"
        runHook postUnpack
      '';

      installPhase = ''
        runHook preInstall

        mkdir -p "$out/Applications"
        mkdir -p "$out/bin"
        cp -R . "$out/Applications/${capitalizedAppName}.app"
        ${prev.darwin.xattr}/bin/xattr -cr "$out/Applications/${capitalizedAppName}.app"
        ${postInstallApp}
        ${prev.lib.optionalString codesignApp ''
          /usr/bin/codesign --force --deep --sign - "$out/Applications/${capitalizedAppName}.app"
        ''}
        ln -s "$out/Applications/${capitalizedAppName}.app/Contents/MacOS/${resolvedExecutableName}" "$out/bin/${pname}"

        runHook postInstall
      '';
    };

  mkZipApp =
    {
      pname,
      info,
      appName ? pname,
      executableName ? null,
      sourceName ? null,
      dontFixup ? false,
      postInstallApp ? "",
      macApp ? { },
      meta ? { },
    }:
    let
      inherit (mkAppNames { inherit pname appName executableName; })
        capitalizedAppName
        resolvedExecutableName
        ;
    in
    prev.stdenvNoCC.mkDerivation {
      inherit pname dontFixup meta;
      inherit (info) version;

      passthru.macApp = {
        bundleName = "${capitalizedAppName}.app";
        bundleRelPath = "Applications/${capitalizedAppName}.app";
        installMode = "symlink";
      }
      // macApp;

      src = prev.fetchurl (
        {
          url = info.urls.${system};
          hash = info.hashes.${system};
        }
        // prev.lib.optionalAttrs (sourceName != null) {
          name = sourceName;
        }
      );

      nativeBuildInputs = [
        prev.darwin.xattr
        prev.unzip
      ];

      dontUnpack = true;

      installPhase = ''
        runHook preInstall

        unpack_dir="$TMPDIR/${pname}-unpack"
        mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
        unzip -qq "$src" -d "$unpack_dir"

        app_bundle="$unpack_dir/${capitalizedAppName}.app"
        if [ ! -d "$app_bundle" ]; then
          echo "Expected ${capitalizedAppName}.app in ${pname} archive" >&2
          find "$unpack_dir" -maxdepth 2 -type d >&2
          exit 1
        fi

        cp -R "$app_bundle" "$out/Applications/${capitalizedAppName}.app"
        ${prev.darwin.xattr}/bin/xattr -cr "$out/Applications/${capitalizedAppName}.app"
        ${postInstallApp}
        ln -s "$out/Applications/${capitalizedAppName}.app/Contents/MacOS/${resolvedExecutableName}" "$out/bin/${pname}"

        runHook postInstall
      '';
    };
}
