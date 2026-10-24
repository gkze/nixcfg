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
  mkMacAppPassthru = import ../mk-mac-app-passthru.nix;
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
        platforms
        sourceProvenance
        ;
    }
    // prev.lib.optionalAttrs (mainProgram != null) { inherit mainProgram; }
    // meta;

  resolveExecutableName =
    bundleName: executableName:
    if executableName == null then prev.lib.removeSuffix ".app" bundleName else executableName;

  guardedBinLink = import ./guarded-bin-link.nix;

  # Archives can carry read-only payloads: ChatGPT 26.1007.21159 ships a 0444
  # `.mcp.json` inside its bundled plugins, and clearing an extended attribute
  # needs write access to the file. Make the copied bundle writable before the
  # sweep; the store normalizes modes again on the way out.
  clearBundleXattrs = bundlePath: ''
    chmod -R u+w ${bundlePath}
    ${prev.darwin.xattr}/bin/xattr -cr ${bundlePath}
  '';

  # `bundle` is a shell word the caller has already quoted (same convention as
  # clearBundleXattrs). Keep strict verification available after postInstall as
  # well as immediately after signing: either hook may own the final artifact.
  verifyBundleSignature = bundle: ''
    /usr/bin/codesign --verify --deep --strict ${bundle}
  '';

  # Ad-hoc re-sign a bundle after modifying it, then prove the new signature
  # is valid. `preserveMetadata` keeps vendor signing metadata (identifier,
  # entitlements, flags, runtime) that an ad-hoc identity can still carry;
  # `entitlements` replaces the entitlements. `verify = false` is for callers
  # that verify in installCheckPhase instead.
  signAndVerifyBundle =
    {
      bundle,
      preserveMetadata ? [ ],
      entitlements ? null,
      verify ? true,
    }:
    let
      flags = [
        "--force"
        "--deep"
        "--sign -"
      ]
      ++ prev.lib.optional (
        preserveMetadata != [ ]
      ) "--preserve-metadata=${prev.lib.concatStringsSep "," preserveMetadata}"
      ++ prev.lib.optional (entitlements != null) "--entitlements ${entitlements}";
    in
    ''
      /usr/bin/codesign ${prev.lib.concatStringsSep " " flags} ${bundle}
    ''
    + prev.lib.optionalString verify (verifyBundleSignature bundle);

  # Shared derivation assembly for every mk*App builder below. Each builder is
  # a thin wrapper that resolves its naming defaults and supplies an `unpack`
  # strategy: the unpack tooling, any strategy-specific derivation attributes
  # (`dontUnpack` for manual unpackers, `sourceRoot`/`unpackCmd` for stdenv
  # unpacking), and the installPhase built around that strategy. Prebuilt app
  # builders disable fixup by default: rewriting a bundled script invalidates
  # the signature's resource seal, even when the main executable is unchanged.
  mkUnpackedApp =
    {
      pname,
      info,
      bundleName,
      dontFixup,
      macApp,
      meta,
      stdenv ? prev.stdenvNoCC,
      srcName ? null,
      unpack,
    }:
    stdenv.mkDerivation (
      {
        inherit pname dontFixup meta;
        inherit (info) version;
        inherit (unpack) nativeBuildInputs installPhase;

        passthru = mkMacAppPassthru { inherit bundleName macApp; };

        src = mkInfoFetchurl {
          inherit info;
          name = srcName;
        };
      }
      // (unpack.drvAttrs or { dontUnpack = true; })
    );
in
{
  inherit mkMacAppPassthru signAndVerifyBundle;

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
      dontFixup ? true,
      macApp ? { },
      meta ? { },
      sourceName ? null,
      stdenv ? prev.stdenvNoCC,
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
    mkUnpackedApp {
      inherit
        pname
        info
        dontFixup
        macApp
        meta
        stdenv
        ;
      bundleName = resolvedBundleName;
      srcName =
        if sourceName == null then "${capitalizedAppName}_${info.version}_${arch}.dmg" else sourceName;

      unpack = {
        nativeBuildInputs = [
          prev.darwin.xattr
          prev.undmg
        ];

        drvAttrs = {
          sourceRoot = resolvedBundleName;

          unpackCmd = ''
            runHook preUnpack
            undmg "$src"
            runHook postUnpack
          '';
        };

        installPhase = ''
          runHook preInstall

          mkdir -p "$out/Applications"
          ${prev.lib.optionalString makeBinary ''mkdir -p "$out/bin"''}
          cp -R . "$out/Applications/${resolvedBundleName}"
          ${clearBundleXattrs ''"$out/Applications/${resolvedBundleName}"''}
          ${postInstallApp}
          ${prev.lib.optionalString codesignApp (signAndVerifyBundle {
            bundle = ''"$out/Applications/${resolvedBundleName}"'';
            verify = true;
          })}
          ${prev.lib.optionalString makeBinary (guardedBinLink {
            bundleName = resolvedBundleName;
            executableName = resolvedExecutableName;
            inherit binaryName;
          })}

          runHook postInstall
          ${prev.lib.optionalString codesignApp (
            verifyBundleSignature ''"$out/Applications/${resolvedBundleName}"''
          )}
        '';
      };
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
      mainProgram ? if createBin then pname else null,
      license ? prev.lib.licenses.unfree,
      platforms ? prev.lib.platforms.darwin,
      sourceProvenance ? with prev.lib.sourceTypes; [ binaryNativeCode ],
      meta ? { },
    }:
    let
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
    mkUnpackedApp {
      inherit
        pname
        info
        bundleName
        macApp
        ;
      dontFixup = true;
      srcName = sourceName;
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

      unpack = {
        nativeBuildInputs = [
          prev._7zz
          prev.darwin.xattr
        ];

        installPhase = ''
          runHook preInstall

          unpack_dir="$TMPDIR/${pname}-unpack"
          mkdir -p "$unpack_dir" "$out/Applications"
          ${prev.lib.optionalString createBin ''mkdir -p "$out/bin"''}
          7zz x -sns- -snld -y "$src" -o"$unpack_dir"
          ${findAppBundle}
          if [ -z "''${app_bundle:-}" ] || [ ! -d "$app_bundle" ]; then
            echo "Expected ${bundleName} in ${pname} DMG" >&2
            find "$unpack_dir" -maxdepth 4 -type d -name "*.app" >&2
            exit 1
          fi

          cp -R "$app_bundle" "$out/Applications/${bundleName}"
          ${clearBundleXattrs ''"$out/Applications/${bundleName}"''}
          ${postInstallApp}
          ${prev.lib.optionalString createBin (guardedBinLink {
            inherit bundleName;
            executableName = resolveExecutableName bundleName executableName;
            binaryName = pname;
          })}

          runHook postInstall
        '';
      };
    };

  # CRX3 container layout: "Cr24" magic, LE32 version, LE32 header length,
  # protobuf header, then a zip whose first entry is the inner DMG. Google
  # distributes Gemini for macOS this way through its Omaha update service.
  mkCrx3DmgApp =
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
      dontFixup ? true,
      macApp ? { },
      meta ? { },
      sourceName ? null,
      stdenv ? prev.stdenvNoCC,
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
      containerName = "${capitalizedAppName}_${info.version}_${arch}.crx3";
      # The Gemini updater (packages/gemini/updater.py) rejects Omaha payloads
      # whose inner artifact is not "<CapitalizedAppName>-<version>.dmg";
      # test_gemini_inner_dmg_name_contract_is_pinned_by_the_helper pins it.
      innerDmgName =
        if sourceName == null then "${capitalizedAppName}-${info.version}.dmg" else sourceName;
    in
    mkUnpackedApp {
      inherit
        pname
        info
        dontFixup
        macApp
        meta
        stdenv
        ;
      bundleName = resolvedBundleName;
      srcName = containerName;

      unpack = {
        nativeBuildInputs = [
          prev.darwin.xattr
          prev.undmg
          prev.unzip
        ];

        drvAttrs = {
          dontUnpack = true;

          installPhase = ''
            runHook preInstall

            unpack_dir="$TMPDIR/${pname}-unpack"
            mkdir -p "$unpack_dir" "$out/Applications"
            ${prev.lib.optionalString makeBinary ''mkdir -p "$out/bin"''}

            magic="$(head -c 4 "$src")"
            if [ "$magic" != "Cr24" ]; then
              echo "Expected a CRX3 container for ${pname}, got magic: $magic" >&2
              exit 1
            fi
            header_len="$(od -An -tu4 -j8 -N4 "$src" | tr -d '[:space:]')"
            inner_zip="$TMPDIR/${pname}-inner.zip"
            tail -c +"$((13 + header_len))" "$src" > "$inner_zip"

            inner_dmg="$unpack_dir/${innerDmgName}"
            if ! unzip -qq -j "$inner_zip" "${innerDmgName}" -d "$unpack_dir"; then
              echo "Expected ${innerDmgName} in ${pname} CRX3 container" >&2
              unzip -l "$inner_zip" >&2
              exit 1
            fi

            dmg_dir="$TMPDIR/${pname}-dmg"
            mkdir -p "$dmg_dir"
            (
              cd "$dmg_dir"
              undmg "$inner_dmg"
            )
            if [ ! -d "$dmg_dir/${resolvedBundleName}" ]; then
              echo "Expected ${resolvedBundleName} in ${pname} DMG" >&2
              find "$dmg_dir" -maxdepth 2 -type d >&2
              exit 1
            fi

            cp -R "$dmg_dir/${resolvedBundleName}" "$out/Applications/${resolvedBundleName}"
            ${clearBundleXattrs ''"$out/Applications/${resolvedBundleName}"''}
            ${postInstallApp}
            ${prev.lib.optionalString codesignApp (signAndVerifyBundle {
              bundle = ''"$out/Applications/${resolvedBundleName}"'';
              verify = true;
            })}
            ${prev.lib.optionalString makeBinary (guardedBinLink {
              bundleName = resolvedBundleName;
              executableName = resolvedExecutableName;
              inherit binaryName;
            })}

            runHook postInstall
            ${prev.lib.optionalString codesignApp (
              verifyBundleSignature ''"$out/Applications/${resolvedBundleName}"''
            )}
          '';
        };
      };
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
      dontFixup ? true,
      postInstallApp ? "",
      verifyAppSignature ? false,
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
    mkUnpackedApp {
      inherit
        pname
        info
        dontFixup
        macApp
        meta
        ;
      bundleName = resolvedBundleName;
      srcName = sourceName;

      unpack = {
        nativeBuildInputs = [
          prev.darwin.xattr
          prev.unzip
        ];

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
          ${clearBundleXattrs ''"$out/Applications/${resolvedBundleName}"''}
          ${postInstallApp}
          ${prev.lib.optionalString makeBinary (guardedBinLink {
            bundleName = resolvedBundleName;
            executableName = resolvedExecutableName;
            inherit binaryName;
          })}

          runHook postInstall
          ${prev.lib.optionalString verifyAppSignature (
            verifyBundleSignature ''"$out/Applications/${resolvedBundleName}"''
          )}
        '';
      };
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
      mainProgram ? if createBin then pname else null,
      license ? prev.lib.licenses.unfree,
      platforms ? prev.lib.platforms.darwin,
      sourceProvenance ? with prev.lib.sourceTypes; [ binaryNativeCode ],
      meta ? { },
    }:
    let
      sourcePayloadPath = sourcePath;
      findPayload =
        if copyContents then
          if sourcePayloadPath == null then
            ''
              payload_path="$(find "$pkg_dir" -maxdepth 6 -type d -name Contents -path "*/Payload/*" -print -quit)"
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
    mkUnpackedApp {
      inherit
        pname
        info
        bundleName
        macApp
        ;
      dontFixup = true;
      srcName = sourceName;
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

      unpack = {
        nativeBuildInputs = [ prev.darwin.xattr ];

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
          ${clearBundleXattrs ''"$out/Applications/${bundleName}"''}
          ${postInstallApp}
          ${prev.lib.optionalString createBin (guardedBinLink {
            inherit bundleName binaryName;
            executableName = resolveExecutableName bundleName executableName;
          })}

          runHook postInstall
        '';
      };
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
    mkUnpackedApp {
      inherit
        pname
        info
        bundleName
        macApp
        meta
        ;
      dontFixup = true;
      srcName = "${pname}-${info.version}-${system}.tar.gz";

      unpack = {
        nativeBuildInputs = [ prev.darwin.xattr ];

        installPhase = ''
          runHook preInstall

          unpack_dir="$TMPDIR/${pname}-unpack"
          mkdir -p "$unpack_dir" "$out/Applications"
          ${prev.lib.optionalString createBin ''mkdir -p "$out/bin"''}
          tar -xzf "$src" -C "$unpack_dir"

          app_bundle="$(find "$unpack_dir" -maxdepth 4 -type d -name "${bundleName}" -print -quit)"
          if [ -z "''${app_bundle:-}" ] || [ ! -d "$app_bundle" ]; then
            echo "Expected ${bundleName} in ${pname} archive" >&2
            find "$unpack_dir" -maxdepth 4 -type d -name "*.app" >&2
            exit 1
          fi

          cp -R "$app_bundle" "$out/Applications/${bundleName}"
          ${clearBundleXattrs ''"$out/Applications/${bundleName}"''}
          ${postInstallApp}
          ${prev.lib.optionalString createBin (guardedBinLink {
            inherit bundleName;
            executableName = resolveExecutableName bundleName executableName;
            binaryName = pname;
          })}

          runHook postInstall
        '';
      };
    };
}
