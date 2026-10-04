{
  cmake,
  fetchFromGitHub,
  fetchurl,
  lib,
  lld,
  libiconv,
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
  # 0.1.20 replaced the self-contained Swift helper with an in-process Cua
  # Driver host. The cursor overlay assets disappeared in that cut.
  hasCuaHost = lib.versionAtLeast version "0.1.20";
  wakuSrc = fetchFromGitHub {
    owner = "egoist";
    repo = "waku";
    rev = source.commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };
  # Pinned Cua Driver SDK 0.28.0, rebuilt with Waku's tiny host/cursor ABI
  # extension from resources/computer-use/cua-host.rs. Instantiated only for
  # 0.1.20+ so the current 0.1.19 pin does not fetch or build Cua.
  cuaDriverSdk = if hasCuaHost then rustPlatform.buildRustPackage {
    pname = "cua-driver-sdk";
    version = "0.28.0";
    src = fetchurl {
      url = "https://github.com/trycua/cua/archive/1b50c02e2d34734f64d2d22f54eb76cc97b4a663.tar.gz";
      hash = "sha256-nSftPDKDAEUkXcX604t02s91xrSjbyQe1EX608NRHr8=";
    };
    sourceRoot = "cua-1b50c02e2d34734f64d2d22f54eb76cc97b4a663/libs/cua-driver/rust";
    cargoLock.lockFile = ./cua-driver.lock;
    cargoBuildFlags = [
      "--package"
      "cua-driver-sdk"
    ];
    # Update 37165499384: Xcode 16.4 ld refused Nix apple-sdk-14.4
    # libdispatch.tbd ("Link against the umbrella framework
    # System.framework instead"). Cua's platform-macos crate asked for
    # `#[link(name = "dispatch")]` and added `$SDKROOT/usr/lib/system`.
    patches = [ ./cua-libsystem.patch ];
    doCheck = false;
    # apple-metal / apple-cf / screencapturekit build.rs run `swift build`.
    # Nix's stdenv DEVELOPER_DIR is the SDK-only store tree. Discover
    # the host Xcode swift with that unset (Update 37101218094 still
    # failed with DEVELOPER_DIR left in place for xcrun). Do not use
    # `xcode-select -p` (CLT vs Xcode; Update 37090922383). Do not wrap
    # swift: SwiftPM execs the tool (Update 37074798271). Do not unset
    # NIX_LDFLAGS globally — rustc still needs libiconv (Update
    # 37037587993). Pin DEVELOPER_DIR and SDKROOT on the `swift` spawn
    # so cargo's process env cannot put the Nix SDK back. Update
    # 37107263529 found Xcode 16.4 swift 6.1.2, then SwiftPM compiled
    # the manifest against apple-sdk-14.4 (Swift 5.10). Export host
    # SDKROOT after discovery so platform-macos build.rs does not add
    # Nix apple-sdk-14.4 `/usr/lib/system` (Update 37165499384). Update
    # 37114244846 then used the host MacOSX15.5.sdk and died on
    # `sandbox-exec: sandbox_apply: Operation not permitted` inside
    # Nix's sandbox. Pass `--disable-sandbox` the same way
    # baseten-switch does. Pin HOME on the spawn — cargo build
    # scripts still see /var/empty after preBuild exports. Update
    # 37156933929 got past sandbox-exec on apple-cf/apple-metal, then
    # screencapturekit-8.0.1 still spawned unpinned `swift` against
    # apple-sdk-14.4. Patch every vendor build.rs that calls swift.
    # Update 37172173272 compiled those Swift .o files with host Xcode
    # 16.4, then rustc linked libcua_driver_sdk.dylib without
    # libswiftCore (`_swift_willThrow`, `_swift_weakLoadStrong`,
    # `_swift_task_switch`). Append the host toolchain's
    # lib/swift/macosx dylibs to NIX_LDFLAGS. Do not add
    # $SDKROOT/usr/lib/swift (TBD). Do not only pass -L (Update
    # 37172173272 repair).
    buildInputs = [ libiconv ];
    preBuild = ''
      xcodeSwift="$(
        env -u DEVELOPER_DIR -u SDKROOT /usr/bin/xcrun --sdk macosx --find swift
      )"
      xcodeSdk="$(
        env -u DEVELOPER_DIR -u SDKROOT /usr/bin/xcrun --sdk macosx --show-sdk-path
      )"
      xcodeToolchain="$(/usr/bin/dirname "$xcodeSwift")"
      developerDir="$(printf '%s\n' "$xcodeSwift" | /usr/bin/sed 's|/Toolchains/.*||')"
      case "$xcodeSwift" in
        /nix/store/*)
          echo "xcrun resolved a Nix store swift: $xcodeSwift" >&2
          exit 1
          ;;
      esac
      case "$xcodeSdk" in
        /nix/store/*)
          echo "xcrun resolved a Nix store SDK: $xcodeSdk" >&2
          exit 1
          ;;
      esac
      if [ "$developerDir" = "$xcodeSwift" ]; then
        echo "xcrun swift is not under an Xcode Toolchains tree: $xcodeSwift" >&2
        exit 1
      fi
      export DEVELOPER_DIR="$developerDir"
      export SDKROOT="$xcodeSdk"
      export CUA_XCODE_SWIFT="$xcodeSwift"
      export CUA_XCODE_DEVELOPER_DIR="$developerDir"
      export CUA_XCODE_SDKROOT="$xcodeSdk"
      export CUA_XCODE_HOME="$TMPDIR/cua-swiftpm-home"
      export PATH="$xcodeToolchain:$PATH"
      export HOME="$CUA_XCODE_HOME"
      export CFFIXED_USER_HOME="$CUA_XCODE_HOME"
      export SWIFTPM_MODULECACHE_OVERRIDE="$TMPDIR/cua-swiftpm-module-cache"
      mkdir -p "$HOME" "$SWIFTPM_MODULECACHE_OVERRIDE"
      echo "cua-driver-sdk host Xcode swift=$xcodeSwift DEVELOPER_DIR=$developerDir SDKROOT=$xcodeSdk" >&2
      patched=0
      for build_rs in \
        cargo-vendor-dir/*/build.rs \
        "$NIX_BUILD_TOP"/cargo-vendor-dir/*/build.rs
      do
        if [ ! -f "$build_rs" ]; then
          continue
        fi
        if ! grep -q 'Command::new("swift")' "$build_rs"; then
          continue
        fi
        /usr/bin/sed -i.bak \
          's/Command::new("swift")/Command::new(std::env::var("CUA_XCODE_SWIFT").expect("CUA_XCODE_SWIFT")).env("DEVELOPER_DIR", std::env::var("CUA_XCODE_DEVELOPER_DIR").expect("CUA_XCODE_DEVELOPER_DIR")).env("SDKROOT", std::env::var("CUA_XCODE_SDKROOT").expect("CUA_XCODE_SDKROOT")).env("HOME", std::env::var("CUA_XCODE_HOME").expect("CUA_XCODE_HOME")).env("CFFIXED_USER_HOME", std::env::var("CUA_XCODE_HOME").expect("CUA_XCODE_HOME"))/' \
          "$build_rs"
        if grep -q '"build",' "$build_rs"; then
          /usr/bin/sed -i.bak \
            's/"build",/"build", "--disable-sandbox",/' \
            "$build_rs"
        fi
        rm -f "$build_rs.bak"
        if ! grep -q -- '--disable-sandbox' "$build_rs"; then
          echo "failed to add --disable-sandbox to $build_rs" >&2
          exit 1
        fi
        patched=$((patched + 1))
      done
      if [ "$patched" -eq 0 ]; then
        echo "failed to pin host Xcode swift on vendor build.rs" >&2
        exit 1
      fi
      # link host Xcode Swift runtime into rustc
      if [ ! -d "$xcodeToolchain/../lib/swift/macosx" ]; then
        echo "host Xcode Swift runtime directory missing under $xcodeToolchain" >&2
        exit 1
      fi
      xcodeSwiftLib="$(
        cd "$xcodeToolchain/../lib/swift/macosx" && pwd
      )"
      case "$xcodeSwiftLib" in
        /nix/store/*)
          echo "xcrun resolved a Nix store Swift runtime: $xcodeSwiftLib" >&2
          exit 1
          ;;
      esac
      swiftLink=""
      for swiftDylib in "$xcodeSwiftLib"/libswift*.dylib; do
        if [ ! -f "$swiftDylib" ]; then
          echo "host Xcode Swift runtime has no libswift*.dylib under $xcodeSwiftLib" >&2
          exit 1
        fi
        swiftName="$(
          /usr/bin/basename "$swiftDylib" .dylib | /usr/bin/sed 's/^lib//'
        )"
        swiftLink="$swiftLink -l$swiftName"
      done
      export NIX_LDFLAGS="$NIX_LDFLAGS -L$xcodeSwiftLib$swiftLink"
      echo "cua-driver-sdk host Xcode Swift runtime=$xcodeSwiftLib$swiftLink" >&2
    '';
    postPatch = ''
      cat ${wakuSrc}/resources/computer-use/cua-host.rs >> crates/cua-driver-sdk/src/abi.rs
    '';
    # Library crate: cargo install has nothing to place. Copy the Darwin
    # cdylib and the two headers the Swift helper compiles against.
    installPhase = ''
      runHook preInstall
      mkdir -p "$out/lib" "$out/include"
      cp "target/${rustTarget}/release/libcua_driver_sdk.dylib" "$out/lib/"
      cp include/cua_driver_abi.h "$out/include/"
      cp ${wakuSrc}/resources/computer-use/cua-host.h "$out/include/"
      runHook postInstall
    '';
    meta = {
      description = "Cua Driver SDK with Waku host cursor entrypoints";
      homepage = "https://github.com/trycua/cua";
      license = lib.licenses.mit;
      platforms = [ "aarch64-darwin" ];
    };
  } else null;
  legacyInstallPhase = ''
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
  cuaInstallPhase = if hasCuaHost then ''
    runHook preInstall

    app="$out/Applications/${appBundleName}"
    contents="$app/Contents"
    helper="$contents/Helpers/${helperBundleName}"
    helperContents="$helper/Contents"
    helperFrameworks="$helperContents/Frameworks"
    helperExecutable="$helperContents/MacOS/${helperName}"
    cuaLibrary="$helperFrameworks/libcua_driver_sdk.dylib"
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
      "$helperFrameworks" \
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
      resources/computer-use/CUA-LICENSE \
      "$helperContents/Resources/CUA-LICENSE"
    install -m0644 \
      resources/computer-use/Info.plist \
      "$helperContents/Info.plist"
    install -m0755 \
      ${cuaDriverSdk}/lib/libcua_driver_sdk.dylib \
      "$cuaLibrary"

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
    /usr/bin/swiftc \
      -O \
      -parse-as-library \
      -module-cache-path "$swiftModuleCache" \
      -target ${minimumMacosTarget} \
      -import-objc-header ${cuaDriverSdk}/include/cua-host.h \
      -L "$helperFrameworks" -lcua_driver_sdk \
      -Xlinker -rpath -Xlinker @executable_path/../Frameworks \
      resources/computer-use/WakuComputerUse.swift \
      resources/computer-use/CuaDriver.swift \
      -o "$helperExecutable"

    helperFingerprint="$({
      /usr/bin/shasum -a 256 \
        resources/computer-use/WakuComputerUse.swift \
        resources/computer-use/CuaDriver.swift \
        resources/computer-use/Info.plist \
        resources/computer-use/CUA-LICENSE \
        ${cuaDriverSdk}/include/cua_driver_abi.h \
        ${cuaDriverSdk}/include/cua-host.h \
        "$cuaLibrary"
      printf '%s\n' \
        "cua-in-process-v1" \
        "${helperName}" \
        "sh.waku.computer-use" \
        "-" \
        "${minimumMacosTarget}"
      /usr/bin/swiftc -version
    } | /usr/bin/shasum -a 256 | awk '{ print $1 }')"
    printf '%s\n' "$helperFingerprint" \
      > "$helperContents/Resources/.waku-helper-fingerprint"

    install -m0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
    ln -s "$executable" "$out/bin/${pname}"

    runHook postInstall

    # Sign only after the post-install hook and every file mutation. The
    # embedded Cua dylib signs first, then the TCC-facing helper; executable
    # leaves precede the outer app.
    /usr/bin/xattr -cr "$app"
    /usr/bin/codesign --force --sign - "$cuaLibrary"
    /usr/bin/codesign --force --identifier "sh.waku.computer-use" --sign - "$helper"
    /usr/bin/codesign --force --identifier "sh.waku.js-repl" --sign - "$repl"
    /usr/bin/codesign --force --identifier "sh.waku.daemon" --sign - "$daemon"
    /usr/bin/codesign --force --identifier "sh.waku" --sign - "$app"
  '' else null;
  legacyInstallCheckPhase = ''
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
  cuaInstallCheckPhase = if hasCuaHost then ''
    runHook preInstallCheck

    app="$out/Applications/${appBundleName}"
    contents="$app/Contents"
    helper="$contents/Helpers/${helperBundleName}"
    helperContents="$helper/Contents"
    helperExecutable="$helperContents/MacOS/${helperName}"
    cuaLibrary="$helperContents/Frameworks/libcua_driver_sdk.dylib"
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
      "$cuaLibrary" \
      "$helperContents/Resources/CUA-LICENSE" \
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
    /usr/bin/lipo "$cuaLibrary" -verify_arch arm64
    for machO in "$executable" "$repl" "$daemon" "$helperExecutable"; do
      test "$(
        /usr/bin/otool -l "$machO" |
          awk '$1 == "cmd" && $2 == "LC_BUILD_VERSION" { inBuildVersion = 1; next } \
            inBuildVersion && $1 == "minos" { print $2; exit }'
      )" = "${minimumMacosVersion}"
    done
    /usr/bin/codesign --verify --strict --verbose=2 "$cuaLibrary"
    /usr/bin/codesign --verify --strict --verbose=2 "$helper"
    /usr/bin/codesign --verify --strict --verbose=2 "$repl"
    /usr/bin/codesign --verify --strict --verbose=2 "$daemon"
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"

    runHook postInstallCheck
  '' else null;
in
rustPlatform.buildRustPackage {
  inherit pname version;

  strictDeps = true;

  src = wakuSrc;

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

  buildInputs = lib.optionals hasCuaHost [ cuaDriverSdk ];

  dontUseCmakeConfigure = true;

  env.NIX_CFLAGS_LINK = "-fuse-ld=lld";

  patches = [ ./runtime-shaders.patch ];

  # The upstream workspace includes provider, daemon, and UI integration tests.
  # The package checks the exact release artifacts without launching the app.
  doCheck = false;

  installPhase = if hasCuaHost then cuaInstallPhase else legacyInstallPhase;

  doInstallCheck = true;
  installCheckPhase =
    if hasCuaHost then cuaInstallCheckPhase else legacyInstallCheckPhase;

  # Generic fixup would mutate Mach-O leaves after the final nested signing.
  dontFixup = true;

  passthru = {
    inherit buildNumber hasCuaHost;
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
