{
  cmake,
  fetchFromGitHub,
  lib,
  lld,
  outputs,
  pkg-config,
  python3,
  rustPlatform,
  stdenv,
  swift,
  ...
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  pname = "waku";
  appName = "Waku";
  appBundleName = "${appName}.app";
  helperName = "Waku Computer Use";
  helperBundleName = "${helperName}.app";
  minimumMacosVersion = "14.0";
  minimumMacosTarget = "arm64-apple-macos${minimumMacosVersion}";
  source = outputs.lib.sourceEntry pname;
  inherit (source) version;
  versionParts = map lib.toInt (lib.splitString "." version);
  buildNumber = toString (
    ((lib.elemAt versionParts 0) * 1000000)
    + ((lib.elemAt versionParts 1) * 1000)
    + (lib.elemAt versionParts 2)
  );
  rustTarget = stdenv.hostPlatform.rust.rustcTarget;
in
rustPlatform.buildRustPackage {
  inherit pname version;

  strictDeps = true;

  src = fetchFromGitHub {
    owner = "egoist";
    repo = "waku";
    rev = source.commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  cargoHash = outputs.lib.sourceHash pname "cargoHash";

  cargoBuildFlags = [
    "--package"
    "waku"
    "--bin"
    "waku"
    "--bin"
    "waku_js_repl"
    "--package"
    "waku-daemon"
    "--bin"
    "waku-daemon"
  ];

  # The proprietary Xcode Metal toolchain is optional and unavailable on a
  # clean host. GPUI's public runtime shader path compiles the same sources via
  # the Metal framework at runtime and is the nixpkgs Darwin packaging seam.
  buildFeatures = [ "gpui_platform/runtime_shaders" ];

  nativeBuildInputs = [
    cmake
    lld
    pkg-config
    python3
    rustPlatform.bindgenHook
    swift
  ];

  dontUseCmakeConfigure = true;

  env.NIX_CFLAGS_LINK = "-fuse-ld=lld";

  patches = [ ./runtime-shaders.patch ];

  # The upstream workspace includes provider, daemon, and UI integration tests.
  # The package checks the exact release artifacts without launching the app.
  doCheck = false;

  installPhase = ''
    runHook preInstall

    app="$out/Applications/${appBundleName}"
    contents="$app/Contents"
    helper="$contents/Helpers/${helperBundleName}"
    helperContents="$helper/Contents"
    helperExecutable="$helperContents/MacOS/${helperName}"
    executable="$contents/MacOS/${appName}"
    repl="$contents/Resources/waku_js_repl"
    daemon="$contents/MacOS/waku-daemon"

    mkdir -p \
      "$contents/MacOS" \
      "$contents/Resources/computer-use" \
      "$contents/Resources/skills/waku-computer-use" \
      "$contents/Helpers" \
      "$helperContents/MacOS" \
      "$helperContents/Resources" \
      "$out/bin" \
      "$out/share/licenses/${pname}"

    install -m0755 "target/${rustTarget}/release/waku" "$executable"
    install -m0755 "target/${rustTarget}/release/waku_js_repl" "$repl"
    install -m0755 "target/${rustTarget}/release/waku-daemon" "$daemon"
    install -m0644 resources/Info.plist "$contents/Info.plist"
    install -m0644 resources/AppIcon.icns "$contents/Resources/AppIcon.icns"
    install -m0644 \
      resources/computer-use/pi-extension.ts \
      "$contents/Resources/computer-use/pi-extension.ts"
    install -m0644 \
      resources/computer-use/SKILL.md \
      "$contents/Resources/skills/waku-computer-use/SKILL.md"
    install -m0644 \
      resources/computer-use/Info.plist \
      "$helperContents/Info.plist"
    install -m0644 \
      resources/computer-use/menubar-cursor.png \
      resources/computer-use/overlay-cursor.svg \
      "$helperContents/Resources/"

    /usr/bin/plutil -replace CFBundleDisplayName -string "${appName}" \
      "$contents/Info.plist"
    /usr/bin/plutil -replace CFBundleExecutable -string "${appName}" \
      "$contents/Info.plist"
    /usr/bin/plutil -replace CFBundleIdentifier -string "sh.waku" \
      "$contents/Info.plist"
    /usr/bin/plutil -replace CFBundleName -string "${appName}" \
      "$contents/Info.plist"
    /usr/bin/plutil -replace CFBundleShortVersionString -string \
      ${lib.escapeShellArg version} "$contents/Info.plist"
    /usr/bin/plutil -replace CFBundleVersion -string \
      ${lib.escapeShellArg buildNumber} "$contents/Info.plist"
    /usr/bin/plutil -replace LSMinimumSystemVersion -string \
      "${minimumMacosVersion}" "$contents/Info.plist"
    for key in SUFeedURL SUPublicEDKey; do
      /usr/libexec/PlistBuddy -c "Delete :$key" "$contents/Info.plist"
    done

    /usr/bin/plutil -replace CFBundleDisplayName -string "${helperName}" \
      "$helperContents/Info.plist"
    /usr/bin/plutil -replace CFBundleExecutable -string "${helperName}" \
      "$helperContents/Info.plist"
    /usr/bin/plutil -replace CFBundleIdentifier -string \
      "sh.waku.computer-use" "$helperContents/Info.plist"
    /usr/bin/plutil -replace CFBundleName -string "${helperName}" \
      "$helperContents/Info.plist"
    /usr/bin/plutil -replace CFBundleShortVersionString -string \
      ${lib.escapeShellArg version} "$helperContents/Info.plist"
    /usr/bin/plutil -replace CFBundleVersion -string \
      ${lib.escapeShellArg buildNumber} "$helperContents/Info.plist"
    /usr/bin/plutil -replace LSMinimumSystemVersion -string \
      "${minimumMacosVersion}" "$helperContents/Info.plist"

    swiftModuleCache="$TMPDIR/swift-module-cache"
    mkdir -p "$swiftModuleCache"
    ${lib.getExe' swift "swiftc"} \
      -O \
      -parse-as-library \
      -module-cache-path "$swiftModuleCache" \
      -target ${minimumMacosTarget} \
      resources/computer-use/WakuComputerUse.swift \
      -o "$helperExecutable"

    helperFingerprint="$({
      /usr/bin/shasum -a 256 \
        resources/computer-use/WakuComputerUse.swift \
        resources/computer-use/Info.plist \
        resources/computer-use/menubar-cursor.png \
        resources/computer-use/overlay-cursor.svg
      printf '%s\n' \
        "standalone-service-v2" \
        "${helperName}" \
        "sh.waku.computer-use" \
        "-" \
        "${minimumMacosTarget}"
      ${lib.getExe' swift "swiftc"} -version
    } | /usr/bin/shasum -a 256 | awk '{ print $1 }')"
    printf '%s\n' "$helperFingerprint" \
      > "$helperContents/Resources/.waku-helper-fingerprint"

    install -m0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
    ln -s "$executable" "$out/bin/${pname}"

    runHook postInstall

    # Sign only after the post-install hook and every file mutation. The
    # TCC-facing helper signs first; executable leaves precede the outer app.
    /usr/bin/xattr -cr "$app"
    /usr/bin/codesign --force --identifier "sh.waku.computer-use" --sign - "$helper"
    /usr/bin/codesign --force --identifier "sh.waku.js-repl" --sign - "$repl"
    /usr/bin/codesign --force --identifier "sh.waku.daemon" --sign - "$daemon"
    /usr/bin/codesign --force --identifier "sh.waku" --sign - "$app"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    app="$out/Applications/${appBundleName}"
    contents="$app/Contents"
    helper="$contents/Helpers/${helperBundleName}"
    helperContents="$helper/Contents"
    helperExecutable="$helperContents/MacOS/${helperName}"
    executable="$contents/MacOS/${appName}"
    repl="$contents/Resources/waku_js_repl"
    daemon="$contents/MacOS/waku-daemon"
    infoPlist="$contents/Info.plist"
    helperInfoPlist="$helperContents/Info.plist"
    fingerprint="$helperContents/Resources/.waku-helper-fingerprint"

    for path in \
      "$app" \
      "$executable" \
      "$repl" \
      "$daemon" \
      "$helper" \
      "$helperExecutable" \
      "$contents/Resources/AppIcon.icns" \
      "$contents/Resources/computer-use/pi-extension.ts" \
      "$contents/Resources/skills/waku-computer-use/SKILL.md" \
      "$fingerprint" \
      "$out/bin/${pname}"; do
      if [ ! -e "$path" ]; then
        echo "missing required Waku runtime path: $path" >&2
        exit 1
      fi
    done

    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$infoPlist")" = \
      "sh.waku"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$infoPlist")" = \
      ${lib.escapeShellArg version}
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$infoPlist")" = \
      ${lib.escapeShellArg buildNumber}
    test "$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$infoPlist")" = \
      "${minimumMacosVersion}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$helperInfoPlist")" = \
      "sh.waku.computer-use"
    test "$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$helperInfoPlist")" = \
      "${minimumMacosVersion}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :NSAccessibilityUsageDescription' "$helperInfoPlist")" = \
      "Waku uses Accessibility access only when you approve an agent controlling another app."
    test "$(/usr/libexec/PlistBuddy -c 'Print :NSScreenCaptureUsageDescription' "$helperInfoPlist")" = \
      "Waku uses Screen Recording access to show the agent the app window you approve."
    grep -Eq '^[0-9a-f]{64}$' "$fingerprint"

    for key in SUFeedURL SUPublicEDKey; do
      if /usr/libexec/PlistBuddy -c "Print :$key" "$infoPlist" >/dev/null 2>&1; then
        echo "unexpected Sparkle feed key in source-built Waku: $key" >&2
        exit 1
      fi
    done
    if [ -d "$contents/Frameworks" ]; then
      echo "unexpected Frameworks directory in source-built Waku" >&2
      exit 1
    fi
    if find "$app" -name 'Sparkle*' -print -quit | grep -q .; then
      echo "unexpected Sparkle payload in source-built Waku" >&2
      exit 1
    fi

    /usr/bin/lipo "$executable" -verify_arch arm64
    /usr/bin/lipo "$repl" -verify_arch arm64
    /usr/bin/lipo "$daemon" -verify_arch arm64
    /usr/bin/lipo "$helperExecutable" -verify_arch arm64
    for machO in "$executable" "$repl" "$daemon" "$helperExecutable"; do
      test "$(
        /usr/bin/otool -l "$machO" |
          awk '$1 == "cmd" && $2 == "LC_BUILD_VERSION" { inBuildVersion = 1; next } \
            inBuildVersion && $1 == "minos" { print $2; exit }'
      )" = "${minimumMacosVersion}"
    done
    /usr/bin/codesign --verify --strict --verbose=2 "$helper"
    /usr/bin/codesign --verify --strict --verbose=2 "$repl"
    /usr/bin/codesign --verify --strict --verbose=2 "$daemon"
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"

    runHook postInstallCheck
  '';

  # Generic fixup would mutate Mach-O leaves after the final nested signing.
  dontFixup = true;

  passthru = {
    inherit buildNumber;
    macApp = {
      bundleId = "sh.waku";
      bundleName = appBundleName;
      bundleRelPath = "Applications/Waku.app";
      installMode = "copy";
    };
  };

  meta = {
    description = "Fast native control plane for local coding agents";
    homepage = "https://github.com/egoist/waku";
    changelog = "https://github.com/egoist/waku/blob/${source.commit}/CHANGELOG.md";
    license = lib.licenses.gpl3Only;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = [ lib.sourceTypes.fromSource ];
  };
}
