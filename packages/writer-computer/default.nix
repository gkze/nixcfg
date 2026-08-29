{
  cargo-tauri,
  fetchFromGitHub,
  fetchPnpmDeps ? null,
  lib,
  nodejs_24,
  outputs,
  pkg-config,
  pnpmConfigHook,
  pnpm_10,
  python3,
  runCommand,
  rustPlatform,
  selfSource,
  stdenv,
  ...
}:
let
  pname = "writer-computer";
  appName = "Writer";
  appBundleName = "${appName}.app";
  appExecutable = "desktop";
  appId = "com.writer-computer";
  inherit (selfSource) version;

  src = fetchFromGitHub {
    owner = "joelbqz";
    repo = pname;
    rev = selfSource.commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  nodejs = nodejs_24;
  pnpm = pnpm_10.override { nodejs-slim = nodejs; };
  pnpmDeps =
    let
      args = {
        inherit
          pname
          pnpm
          src
          version
          ;
        fetcherVersion = 3;
        hash = outputs.lib.sourceHash pname "npmDepsHash";
      };
    in
    if fetchPnpmDeps != null then fetchPnpmDeps args else pnpm.fetchDeps args;

  package = rustPlatform.buildRustPackage {
    inherit
      pname
      pnpmDeps
      src
      version
      ;

    cargoHash = outputs.lib.sourceHash pname "cargoHash";
    cargoRoot = "apps/desktop/src-tauri";
    buildAndTestSubdir = "apps/desktop/src-tauri";
    cargoPatches = [ ./nix-managed.patch ];

    nativeBuildInputs = [
      cargo-tauri.hook
      nodejs
      pkg-config
      pnpm
      pnpmConfigHook
      python3
    ];

    env = {
      # cargo-tauri exposes CI as a strict boolean Clap option. Numeric truthy
      # values are rejected as `--ci 1`; retain non-interactive mode explicitly.
      CI = "true";
      npm_config_manage_package_manager_versions = "false";
    };

    # Tauri executes upstream's `vp build` from the desktop workspace. Export
    # the immutable root workspace binary without rewriting upstream config.
    preBuild = ''
      export PATH="$PWD/node_modules/.bin:$PATH"
    '';

    # Tauri's macOS hook produces Writer.app directly. The policy patch makes
    # Nix the sole updater and removes the app-owned /usr/local CLI mutation.
    tauriBuildFlags = [ "--no-sign" ];
    doCheck = false;

    postInstall = ''
      install -Dm0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
    '';

    # Generic fixup may mutate Mach-O payloads, so sign the final bundle last.
    postFixup = ''
      /usr/bin/xattr -cr "$out/Applications/${appBundleName}"
      /usr/bin/codesign --force --deep --sign - \
        "$out/Applications/${appBundleName}"
    '';

    doInstallCheck = true;
    installCheckPhase = ''
            runHook preInstallCheck

            appBundle="$out/Applications/${appBundleName}"
            executable="$appBundle/Contents/MacOS/${appExecutable}"
            infoPlist="$appBundle/Contents/Info.plist"

            test -d "$appBundle"
            test -x "$executable"
            test "$(${lib.getExe python3} - "$infoPlist" <<'PY'
      import plistlib
      import sys

      with open(sys.argv[1], "rb") as plist_file:
          info = plistlib.load(plist_file)

      expected = {
          "CFBundleExecutable": "${appExecutable}",
          "CFBundleIdentifier": "${appId}",
          "CFBundleShortVersionString": "${version}",
          "CFBundleVersion": "${version}",
      }
      for key, expected_value in expected.items():
          actual_value = info.get(key)
          if actual_value != expected_value:
              raise SystemExit(f"{key} expected {expected_value!r}, got {actual_value!r}")
      print("ok")
      PY
            )" = ok

            /usr/bin/lipo "$executable" -verify_arch arm64
            /usr/bin/codesign --verify --deep --strict --verbose=2 "$appBundle"

            for forbidden in \
              '/usr/local/bin/writer' \
              'releases/latest/download/latest.json' \
              'Check for Updates…'
            do
              if strings "$executable" | grep -F -- "$forbidden"; then
                echo "Nix-owned Writer still contains forbidden app mutation surface: $forbidden" >&2
                exit 1
              fi
            done

            runHook postInstallCheck
    '';

    passthru = {
      inherit pnpmDeps;

      # The executable is intentionally multi-call. This app-free view makes
      # argv[0] equal to `writer` without adding a second managed app bundle.
      cliPackage = runCommand "${pname}-cli-${version}" { } ''
        mkdir -p "$out/bin"
        ln -s \
          "${package}/Applications/${appBundleName}/Contents/MacOS/${appExecutable}" \
          "$out/bin/writer"
      '';

      macApp = {
        bundleName = appBundleName;
        bundleRelPath = "Applications/${appBundleName}";
        installMode = "copy";
      };
    };

    meta = with lib; {
      description = "Local-first Markdown writing environment";
      homepage = "https://github.com/joelbqz/writer-computer";
      license = licenses.gpl3Only;
      mainProgram = "writer";
      platforms = [ "aarch64-darwin" ];
      sourceProvenance = with sourceTypes; [ fromSource ];
    };
  };
in
assert stdenv.hostPlatform.system == "aarch64-darwin";
package
