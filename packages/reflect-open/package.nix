{
  cargo-tauri,
  fetchFromGitHub,
  fetchPnpmDeps,
  fetchurl,
  lib,
  nodejs_22,
  nodejs_24,
  onnxruntime,
  outputs,
  perl,
  pkg-config,
  pnpmConfigHook,
  pnpm_11,
  python3,
  rustPlatform,
  stdenv,
  ...
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  pname = "reflect-open";
  appName = "Reflect";
  appBundleName = "${appName}.app";
  appExecutable = "reflect-open";
  appId = "app.reflect.desktop";
  minimumMacosVersion = "14.0";
  expectedPnpmVersion = "11.18.0";
  source = outputs.lib.sourceEntry pname;
  inherit (source) version;

  src = fetchFromGitHub {
    owner = "team-reflect";
    repo = "reflect-open";
    rev = source.commit;
    fetchSubmodules = false;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  nodejs = nodejs_22;
  # Reflect pins pnpm 11.18.0. Keep the explicit project Node toolchain at 22,
  # while pnpm and its Nixpkgs helper use Node 24; specializing that helper to
  # Node 22 deadlocks in Nixpkgs' generic npm install hook on Darwin.
  pnpm = (pnpm_11.override { nodejs-slim = nodejs_24; }).overrideAttrs (_: {
    version = expectedPnpmVersion;
    src = fetchurl {
      url = "https://registry.npmjs.org/pnpm/-/pnpm-${expectedPnpmVersion}.tgz";
      hash = "sha256-KcNcqNKih5iP3uPg824H2bk3g/VntXm3/Vt5ikVj3YE=";
    };
  });
  pnpmDeps =
    let
      args = {
        inherit
          pname
          pnpm
          src
          version
          ;
        fetcherVersion = 4;
        hash = outputs.lib.sourceHash pname "npmDepsHash";
      };
    in
    fetchPnpmDeps args;

  # fastembed/ort-sys asks for API 23. The pinned nixpkgs ONNX Runtime 1.27.1
  # exports that API from a source-built shared library, so no Pyke archive is
  # downloaded or linked into the application.
  onnxruntimeShared =
    (onnxruntime.override {
      coremlSupport = false;
      pythonSupport = false;
    }).overrideAttrs
      {
        # nixpkgs disables ONNX Runtime's checks on Darwin only while CoreML is
        # enabled. This CPU-only override otherwise enables a sandbox-broken
        # suite whose test binaries reference the not-yet-installed output.
        doCheck = false;
      };
in
rustPlatform.buildRustPackage {
  inherit
    pname
    pnpmDeps
    src
    version
    ;

  strictDeps = true;
  cargoHash = outputs.lib.sourceHash pname "cargoHash";
  cargoRoot = ".";
  buildAndTestSubdir = "apps/desktop";

  nativeBuildInputs = [
    cargo-tauri.hook
    nodejs
    perl
    pkg-config
    pnpm
    pnpmConfigHook
    python3
  ];

  buildInputs = [ onnxruntimeShared ];

  env = {
    CARGO_NET_OFFLINE = "true";
    CI = "true";
    MACOSX_DEPLOYMENT_TARGET = minimumMacosVersion;
    npm_config_manage_package_manager_versions = "false";
    ORT_LIB_LOCATION = "${onnxruntimeShared}/lib";
    ORT_PREFER_DYNAMIC_LINK = "1";
    ORT_SKIP_DOWNLOAD = "1";
  };

  postPatch = ''
    ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD"
  '';

  # Tauri runs upstream's source-building sidecar script before the frontend
  # build. The app is signed only after generic Mach-O fixups have completed.
  tauriBuildFlags = [ "--no-sign" ];
  doCheck = false;

  postInstall = ''
    install -Dm0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
    mkdir -p "$out/bin"
    ln -s "../Applications/${appBundleName}/Contents/MacOS/${appExecutable}" \
      "$out/bin/${appExecutable}"
  '';

  postFixup = ''
    appBundle="$out/Applications/${appBundleName}"
    /usr/bin/xattr -cr "$appBundle"
    /usr/bin/codesign --force --sign - \
      "$appBundle/Contents/MacOS/reflect"
    /usr/bin/codesign --force --sign - \
      "$appBundle/Contents/MacOS/reflect-capture-host"
    /usr/bin/codesign --force --sign - \
      --entitlements "${src}/apps/desktop/src-tauri/Entitlements.dev.plist" \
      "$appBundle"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
          runHook preInstallCheck

          appBundle="$out/Applications/${appBundleName}"
          executable="$appBundle/Contents/MacOS/${appExecutable}"
          infoPlist="$appBundle/Contents/Info.plist"

          test -d "$appBundle"
          test -x "$executable"
          test -L "$out/bin/${appExecutable}"
          test ! -e "$appBundle/Contents/embedded.provisionprofile"
          test "$(${lib.getExe python3} - "$infoPlist" <<'PY'
    import plistlib
    import sys

    with open(sys.argv[1], "rb") as plist_file:
        info = plistlib.load(plist_file)

    expected = {
        "CFBundleExecutable": "${appExecutable}",
        "CFBundleIdentifier": "${appId}",
        "CFBundleName": "${appName}",
        "CFBundleShortVersionString": "${version}",
    }
    for key, expected_value in expected.items():
        actual_value = info.get(key)
        if actual_value != expected_value:
            raise SystemExit(f"{key} expected {expected_value!r}, got {actual_value!r}")
    print("ok")
    PY
          )" = ok

          test "$(
            /usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$infoPlist"
          )" = "${minimumMacosVersion}"

          /usr/bin/lipo "$executable" -verify_arch arm64
          while IFS= read -r -d "" machO; do
            if /usr/bin/file "$machO" | grep -q 'Mach-O'; then
              test "$(
                /usr/bin/otool -l "$machO" |
                  /usr/bin/awk '
                    $1 == "cmd" && $2 == "LC_BUILD_VERSION" { inBuildVersion = 1; next }
                    inBuildVersion && $1 == "minos" { print $2; exit }
                  '
              )" = "${minimumMacosVersion}"
            fi
          done < <(find "$appBundle" -type f -print0)
          test "$(
            /usr/bin/otool -l \
              "${onnxruntimeShared}/lib/libonnxruntime.1.dylib" |
              /usr/bin/awk '
                $1 == "cmd" && $2 == "LC_BUILD_VERSION" { inBuildVersion = 1; next }
                inBuildVersion && $1 == "minos" { print $2; exit }
              '
          )" = "${minimumMacosVersion}"
          /usr/bin/otool -L "$executable" | \
            grep -F "${onnxruntimeShared}/lib/libonnxruntime"
          if find "$appBundle" -type f -name 'libonnxruntime*.dylib' -print -quit | \
            grep -q .
          then
            echo "Reflect.app contains a bundled ONNX Runtime dylib" >&2
            exit 1
          fi

          entitlements="$TMPDIR/reflect-open-entitlements.plist"
          /usr/bin/codesign -d --entitlements - --xml \
            "$executable" >"$entitlements"
          test "$(${lib.getExe python3} - "$entitlements" <<'PY'
    import plistlib
    import sys

    with open(sys.argv[1], "rb") as plist_file:
        entitlements = plistlib.load(plist_file)

    expected = {
        "com.apple.security.device.audio-input": True,
        "com.apple.security.personal-information.addressbook": True,
        "com.apple.security.personal-information.calendars": True,
    }
    if entitlements != expected:
        raise SystemExit(f"unexpected Reflect entitlements: {entitlements!r}")
    print("ok")
    PY
          )" = ok

          sidecarEntitlements="$TMPDIR/reflect-open-sidecar-entitlements.plist"
          for sidecar in \
            "$appBundle/Contents/MacOS/reflect" \
            "$appBundle/Contents/MacOS/reflect-capture-host"
          do
            test -x "$sidecar"
            : >"$sidecarEntitlements"
            /usr/bin/codesign -d --entitlements - --xml \
              "$sidecar" >"$sidecarEntitlements"
            test ! -s "$sidecarEntitlements"
          done

          /usr/bin/codesign --verify --deep --strict --verbose=2 "$appBundle"
          if grep -R -a -F \
            'releases/download/updater-beta/latest.json' "$appBundle"
          then
            echo "Nix-owned Reflect still contains its mutable updater endpoint" >&2
            exit 1
          fi

          runHook postInstallCheck
  '';

  passthru = {
    inherit onnxruntimeShared pnpmDeps;

    macApp = {
      bundleId = appId;
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  meta = {
    description = "Local-first notes application";
    homepage = "https://github.com/team-reflect/reflect-open";
    changelog = "https://github.com/team-reflect/reflect-open/releases/tag/v${version}";
    license = lib.licenses.mit;
    mainProgram = appExecutable;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = [ lib.sourceTypes.fromSource ];
  };
}
