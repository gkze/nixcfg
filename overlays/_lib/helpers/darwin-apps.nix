{ prev, system, ... }:
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
      capitalizedAppName =
        (prev.lib.toUpper (builtins.substring 0 1 appName)) + builtins.substring 1 (-1) appName;
      resolvedExecutableName = if executableName == null then capitalizedAppName else executableName;
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
}
