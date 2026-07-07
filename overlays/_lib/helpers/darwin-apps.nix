{ prev, system, ... }:
let
  mkAppNames =
    {
      pname,
      appName ? pname,
      executableName ? null,
      bundleName ? null,
    }:
    let
      capitalizedAppName =
        (prev.lib.toUpper (builtins.substring 0 1 appName)) + builtins.substring 1 (-1) appName;
      resolvedBundleName = if bundleName == null then "${capitalizedAppName}.app" else bundleName;
    in
    {
      inherit capitalizedAppName;
      inherit resolvedBundleName;
      resolvedExecutableName = if executableName == null then capitalizedAppName else executableName;
    };
  mkMacAppPassthru =
    {
      bundleName,
      macApp ? { },
    }:
    {
      macApp = {
        inherit bundleName;
        bundleRelPath = "Applications/${bundleName}";
        installMode = "copy";
      }
      // macApp;
    };
  mkInfoFetchurl =
    {
      info,
      name ? null,
    }:
    prev.fetchurl (
      {
        url = info.urls.${system};
        hash = info.hashes.${system};
      }
      // prev.lib.optionalAttrs (name != null) { inherit name; }
    );
  mkDescribedMeta =
    {
      description,
      homepage,
      mainProgram,
      license,
      platforms,
      sourceProvenance,
      meta ? { },
    }:
    {
      inherit
        description
        homepage
        license
        mainProgram
        platforms
        sourceProvenance
        ;
    }
    // meta;
in
{
  mkSimpleDarwinApp =
    {
      builder,
      pname,
      info,
      description,
      homepage,
      mainProgram ? pname,
      license ? prev.lib.licenses.unfree,
      platforms ? prev.lib.platforms.darwin,
      sourceProvenance ? with prev.lib.sourceTypes; [ binaryNativeCode ],
      dontFixup ? true,
      macApp ? { },
      meta ? { },
      ...
    }@args:
    let
      builderArgs = builtins.removeAttrs args [
        "builder"
        "description"
        "dontFixup"
        "homepage"
        "info"
        "license"
        "macApp"
        "mainProgram"
        "meta"
        "platforms"
        "pname"
        "sourceProvenance"
      ];
    in
    builder (
      builderArgs
      // {
        inherit
          dontFixup
          info
          pname
          ;
        macApp = {
          installMode = "copy";
        }
        // macApp;
        meta = {
          inherit
            description
            homepage
            license
            mainProgram
            platforms
            sourceProvenance
            ;
        }
        // meta;
      }
    );

  mkDmgApp =
    {
      pname,
      info,
      appName ? pname,
      executableName ? null,
      bundleName ? null,
      binaryName ? pname,
      makeBinary ? true,
      postInstallApp ? "",
      codesignApp ? false,
      dontFixup ? false,
      macApp ? { },
      meta ? { },
    }:
    let
      arch = if system == "aarch64-darwin" then "aarch64" else "x86_64";
      inherit
        (mkAppNames {
          inherit
            pname
            appName
            executableName
            bundleName
            ;
        })
        capitalizedAppName
        resolvedBundleName
        resolvedExecutableName
        ;
    in
    prev.stdenvNoCC.mkDerivation {
      inherit pname dontFixup;
      inherit (info) version;
      inherit meta;

      passthru = mkMacAppPassthru {
        bundleName = resolvedBundleName;
        inherit macApp;
      };

      src = mkInfoFetchurl {
        inherit info;
        name = "${capitalizedAppName}_${info.version}_${arch}.dmg";
      };

      nativeBuildInputs = [
        prev.darwin.xattr
        prev.undmg
      ];

      sourceRoot = resolvedBundleName;

      unpackCmd = ''
        runHook preUnpack
        undmg "$src"
        runHook postUnpack
      '';

      installPhase = ''
        runHook preInstall

        mkdir -p "$out/Applications"
        ${prev.lib.optionalString makeBinary ''mkdir -p "$out/bin"''}
        cp -R . "$out/Applications/${resolvedBundleName}"
        ${prev.darwin.xattr}/bin/xattr -cr "$out/Applications/${resolvedBundleName}"
        ${postInstallApp}
        ${prev.lib.optionalString codesignApp ''
          /usr/bin/codesign --force --deep --sign - "$out/Applications/${resolvedBundleName}"
        ''}
        ${prev.lib.optionalString makeBinary ''
          ln -s "$out/Applications/${resolvedBundleName}/Contents/MacOS/${resolvedExecutableName}" "$out/bin/${binaryName}"
        ''}

        runHook postInstall
      '';
    };

  mkDmgApp7zz =
    {
      pname,
      info,
      bundleName,
      executableName ? null,
      sourceAppPath ? null,
      sourceName ? null,
      createBin ? true,
      postInstallApp ? "",
      macApp ? { },
      description,
      homepage,
      mainProgram ? pname,
      license ? prev.lib.licenses.unfree,
      platforms ? prev.lib.platforms.darwin,
      sourceProvenance ? with prev.lib.sourceTypes; [ binaryNativeCode ],
      meta ? { },
    }:
    let
      resolvedExecutableName =
        if executableName == null then prev.lib.removeSuffix ".app" bundleName else executableName;
      findAppBundle =
        if sourceAppPath == null then
          ''
            app_bundle="$(find "$unpack_dir" -maxdepth 4 -type d -name "${bundleName}" -print -quit)"
          ''
        else
          ''
            app_bundle="$unpack_dir/${sourceAppPath}"
          '';
    in
    prev.stdenvNoCC.mkDerivation {
      inherit pname;
      inherit (info) version;
      meta = mkDescribedMeta {
        inherit
          description
          homepage
          license
          mainProgram
          platforms
          sourceProvenance
          meta
          ;
      };

      passthru = mkMacAppPassthru {
        inherit bundleName;
        inherit macApp;
      };

      src = mkInfoFetchurl {
        inherit info;
        name = sourceName;
      };

      nativeBuildInputs = [
        prev._7zz
        prev.darwin.xattr
      ];

      dontFixup = true;
      dontUnpack = true;

      installPhase = ''
        runHook preInstall

        unpack_dir="$TMPDIR/${pname}-unpack"
        mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
        7zz x -sns- -snld -y "$src" -o"$unpack_dir"
        ${findAppBundle}
        if [ -z "''${app_bundle:-}" ] || [ ! -d "$app_bundle" ]; then
          echo "Expected ${bundleName} in ${pname} DMG" >&2
          find "$unpack_dir" -maxdepth 4 -type d -name "*.app" >&2
          exit 1
        fi

        cp -R "$app_bundle" "$out/Applications/${bundleName}"
        ${prev.darwin.xattr}/bin/xattr -cr "$out/Applications/${bundleName}"
        ${postInstallApp}
        ${prev.lib.optionalString createBin ''
          if [ ! -e "$out/Applications/${bundleName}/Contents/MacOS/${resolvedExecutableName}" ]; then
            echo "Expected executable ${resolvedExecutableName} in ${bundleName}" >&2
            find "$out/Applications/${bundleName}/Contents/MacOS" -maxdepth 1 -type f >&2
            exit 1
          fi
          ln -s "$out/Applications/${bundleName}/Contents/MacOS/${resolvedExecutableName}" "$out/bin/${pname}"
        ''}

        runHook postInstall
      '';
    };

  mkZipApp =
    {
      pname,
      info,
      appName ? pname,
      executableName ? null,
      bundleName ? null,
      binaryName ? pname,
      makeBinary ? true,
      sourceName ? null,
      sourceAppPath ? null,
      dontFixup ? false,
      postInstallApp ? "",
      macApp ? { },
      meta ? { },
    }:
    let
      inherit
        (mkAppNames {
          inherit
            pname
            appName
            executableName
            bundleName
            ;
        })
        resolvedBundleName
        resolvedExecutableName
        ;
    in
    prev.stdenvNoCC.mkDerivation {
      inherit pname dontFixup meta;
      inherit (info) version;

      passthru = mkMacAppPassthru {
        bundleName = resolvedBundleName;
        inherit macApp;
      };

      src = mkInfoFetchurl {
        inherit info;
        name = sourceName;
      };

      nativeBuildInputs = [
        prev.darwin.xattr
        prev.unzip
      ];

      dontUnpack = true;

      installPhase = ''
        runHook preInstall

        unpack_dir="$TMPDIR/${pname}-unpack"
        mkdir -p "$unpack_dir" "$out/Applications"
        ${prev.lib.optionalString makeBinary ''mkdir -p "$out/bin"''}
        unzip -qq "$src" -d "$unpack_dir"

        app_bundle="${
          if sourceAppPath == null then
            "$unpack_dir/${resolvedBundleName}"
          else
            "$unpack_dir/${sourceAppPath}"
        }"
        if [ ! -d "$app_bundle" ]; then
          echo "Expected ${resolvedBundleName} in ${pname} archive" >&2
          find "$unpack_dir" -maxdepth 2 -type d >&2
          exit 1
        fi

        cp -R "$app_bundle" "$out/Applications/${resolvedBundleName}"
        ${prev.darwin.xattr}/bin/xattr -cr "$out/Applications/${resolvedBundleName}"
        ${postInstallApp}
        ${prev.lib.optionalString makeBinary ''
          ln -s "$out/Applications/${resolvedBundleName}/Contents/MacOS/${resolvedExecutableName}" "$out/bin/${binaryName}"
        ''}

        runHook postInstall
      '';
    };

  mkPkgApp =
    {
      pname,
      info,
      bundleName,
      executableName ? null,
      binaryName ? pname,
      copyContents ? false,
      createBin ? true,
      sourceName ? null,
      sourcePath ? null,
      postInstallApp ? "",
      macApp ? { },
      description,
      homepage,
      mainProgram ? pname,
      license ? prev.lib.licenses.unfree,
      platforms ? prev.lib.platforms.darwin,
      sourceProvenance ? with prev.lib.sourceTypes; [ binaryNativeCode ],
      meta ? { },
    }:
    let
      resolvedExecutableName =
        if executableName == null then prev.lib.removeSuffix ".app" bundleName else executableName;
      sourcePayloadPath = sourcePath;
      findPayload =
        if copyContents then
          if sourcePayloadPath == null then
            ''
              payload_path="$(find "$pkg_dir" -maxdepth 4 -type d -path "*/Payload/Contents" -print -quit)"
            ''
          else
            ''
              payload_path="$pkg_dir/${sourcePayloadPath}"
            ''
        else if sourcePayloadPath == null then
          ''
            payload_path="$(find "$pkg_dir" -maxdepth 4 -type d -name "${bundleName}" -print -quit)"
          ''
        else
          ''
            payload_path="$pkg_dir/${sourcePayloadPath}"
          '';
      copyPayload =
        if copyContents then
          ''
            mkdir -p "$out/Applications/${bundleName}"
            cp -R "$payload_path" "$out/Applications/${bundleName}/Contents"
          ''
        else
          ''
            cp -R "$payload_path" "$out/Applications/${bundleName}"
          '';
    in
    prev.stdenvNoCC.mkDerivation {
      inherit pname;
      inherit (info) version;
      meta = mkDescribedMeta {
        inherit
          description
          homepage
          license
          mainProgram
          platforms
          sourceProvenance
          meta
          ;
      };

      passthru = mkMacAppPassthru {
        inherit bundleName;
        inherit macApp;
      };

      src = mkInfoFetchurl {
        inherit info;
        name = sourceName;
      };

      nativeBuildInputs = [ prev.darwin.xattr ];

      dontFixup = true;
      dontUnpack = true;

      installPhase = ''
        runHook preInstall

        pkg_dir="$TMPDIR/${pname}-pkg"
        rm -rf "$pkg_dir"
        mkdir -p "$out/Applications"
        ${prev.lib.optionalString createBin ''mkdir -p "$out/bin"''}
        /usr/sbin/pkgutil --expand-full "$src" "$pkg_dir"
        ${findPayload}
        if [ -z "''${payload_path:-}" ] || [ ! -d "$payload_path" ]; then
          echo "Expected ${if copyContents then "Contents payload" else bundleName} in ${pname} pkg" >&2
          find "$pkg_dir" -maxdepth 4 -type d >&2
          exit 1
        fi

        ${copyPayload}
        ${prev.darwin.xattr}/bin/xattr -cr "$out/Applications/${bundleName}"
        ${postInstallApp}
        ${prev.lib.optionalString createBin ''
          if [ ! -e "$out/Applications/${bundleName}/Contents/MacOS/${resolvedExecutableName}" ]; then
            echo "Expected executable ${resolvedExecutableName} in ${bundleName}" >&2
            find "$out/Applications/${bundleName}/Contents/MacOS" -maxdepth 1 -type f >&2
            exit 1
          fi
          ln -s "$out/Applications/${bundleName}/Contents/MacOS/${resolvedExecutableName}" "$out/bin/${binaryName}"
        ''}

        runHook postInstall
      '';
    };

  mkTgzApp =
    {
      pname,
      info,
      bundleName,
      executableName ? null,
      createBin ? true,
      postInstallApp ? "",
      macApp ? { },
      meta ? { },
    }:
    let
      resolvedExecutableName =
        if executableName == null then prev.lib.removeSuffix ".app" bundleName else executableName;
    in
    prev.stdenvNoCC.mkDerivation {
      inherit pname meta;
      inherit (info) version;

      passthru = mkMacAppPassthru {
        inherit bundleName;
        inherit macApp;
      };

      src = mkInfoFetchurl {
        inherit info;
        name = "${pname}-${info.version}-${system}.tar.gz";
      };

      nativeBuildInputs = [ prev.darwin.xattr ];

      dontFixup = true;
      dontUnpack = true;

      installPhase = ''
        runHook preInstall

        unpack_dir="$TMPDIR/${pname}-unpack"
        mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
        tar -xzf "$src" -C "$unpack_dir"

        app_bundle="$(find "$unpack_dir" -maxdepth 4 -type d -name "${bundleName}" -print -quit)"
        if [ -z "''${app_bundle:-}" ] || [ ! -d "$app_bundle" ]; then
          echo "Expected ${bundleName} in ${pname} archive" >&2
          find "$unpack_dir" -maxdepth 4 -type d -name "*.app" >&2
          exit 1
        fi

        cp -R "$app_bundle" "$out/Applications/${bundleName}"
        ${prev.darwin.xattr}/bin/xattr -cr "$out/Applications/${bundleName}"
        ${postInstallApp}
        ${prev.lib.optionalString createBin ''
          if [ ! -e "$out/Applications/${bundleName}/Contents/MacOS/${resolvedExecutableName}" ]; then
            echo "Expected executable ${resolvedExecutableName} in ${bundleName}" >&2
            find "$out/Applications/${bundleName}/Contents/MacOS" -maxdepth 1 -type f >&2
            exit 1
          fi
          ln -s "$out/Applications/${bundleName}/Contents/MacOS/${resolvedExecutableName}" "$out/bin/${pname}"
        ''}

        runHook postInstall
      '';
    };
}
