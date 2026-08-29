{
  cacert,
  curl,
  fetchFromGitHub,
  lib,
  outputs,
  selfSource,
  stdenvNoCC,
  xcodegen,
  ...
}:
let
  pname = "clearly";
  appName = "Clearly";
  appBundleName = "${appName}.app";
  inherit (selfSource) version;

  src = fetchFromGitHub {
    owner = "Shpigford";
    repo = pname;
    rev = selfSource.commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  dependencyUrls = selfSource.urls or (throw "clearly sources.json is missing Swift dependency URLs");

  # Upstream does not commit Package.resolved. The updater resolves the
  # release's SwiftPM version ranges to immutable GitHub commits and stores
  # those archive URLs in sources.json. Bundle the normalized source trees in
  # one fixed-output derivation so the Xcode build has no network dependency.
  swiftDeps = stdenvNoCC.mkDerivation {
    pname = "${pname}-swift-deps";
    inherit version;

    nativeBuildInputs = [
      cacert
      curl
    ];
    SSL_CERT_FILE = "${cacert}/etc/ssl/certs/ca-bundle.crt";
    strictDeps = true;
    dontUnpack = true;

    buildPhase = ''
      runHook preBuild

      fetchDependency() {
        local url="$1"
        local destination="$2"

        mkdir -p "$destination"
        curl \
          --fail \
          --location \
          --proto '=https' \
          --retry 3 \
          --show-error \
          --silent \
          --tlsv1.2 \
          "$url" \
          | tar -xzf - --strip-components=1 -C "$destination"
      }

      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall

      fetchDependency \
        ${lib.escapeShellArg dependencyUrls."cmark-gfm"} \
        "$out/cmark-gfm"
      fetchDependency \
        ${lib.escapeShellArg dependencyUrls.KeyboardShortcuts} \
        "$out/KeyboardShortcuts"

      runHook postInstall
    '';

    outputHashAlgo = "sha256";
    outputHashMode = "recursive";
    outputHash = outputs.lib.sourceHash pname "vendorHash";
  };

  darwinArch =
    if stdenvNoCC.hostPlatform.isAarch64 then
      "arm64"
    else if stdenvNoCC.hostPlatform.isx86_64 then
      "x86_64"
    else
      throw "clearly: unsupported Darwin architecture ${stdenvNoCC.hostPlatform.system}";
in
stdenvNoCC.mkDerivation {
  inherit
    pname
    src
    version
    ;

  nativeBuildInputs = [ xcodegen ];
  strictDeps = true;

  # Clearly's App Store release path is the upstream-supported build without
  # Sparkle. Nix owns updates, and a store-backed app cannot replace itself.
  patches = [ ./nix-managed.patch ];

  postPatch = ''
    ln -s ${swiftDeps} nix-swift-deps

    for key in SUFeedURL SUPublicEDKey SUEnableInstallerLauncherService; do
      /usr/libexec/PlistBuddy -c "Delete :$key" Clearly/Info.plist
    done
  '';

  buildPhase = ''
    runHook preBuild

    export HOME="$TMPDIR/home"
    export CFFIXED_USER_HOME="$HOME"
    export USER="nix-builder"
    export CLANG_MODULE_CACHE_PATH="$TMPDIR/clang-module-cache"
    export SWIFTPM_MODULECACHE_OVERRIDE="$TMPDIR/swift-module-cache"
    mkdir -p \
      "$HOME" \
      "$CLANG_MODULE_CACHE_PATH" \
      "$SWIFTPM_MODULECACHE_OVERRIDE"

    xcodegen generate
    /usr/bin/xcodebuild \
      -IDEPackageSupportDisableManifestSandbox=1 \
      -quiet \
      -project Clearly.xcodeproj \
      -scheme Clearly \
      -configuration Release \
      -sdk macosx \
      -destination 'platform=macOS,arch=${darwinArch}' \
      -derivedDataPath "$TMPDIR/DerivedData" \
      -disableAutomaticPackageResolution \
      CODE_SIGNING_ALLOWED=NO \
      CODE_SIGNING_REQUIRED=NO \
      CODE_SIGN_IDENTITY= \
      COMPILER_INDEX_STORE_ENABLE=NO \
      DEVELOPMENT_TEAM= \
      'OTHER_SWIFT_FLAGS=$(inherited) -disable-sandbox' \
      build

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    appBundle="$TMPDIR/DerivedData/Build/Products/Release/${appBundleName}"
    quickLookExtension="$appBundle/Contents/PlugIns/ClearlyQuickLook.appex"
    if [ ! -d "$quickLookExtension" ]; then
      echo "missing Clearly Quick Look extension: $quickLookExtension" >&2
      exit 1
    fi

    mkdir -p "$out/Applications" "$out/bin" "$out/share/licenses/${pname}"
    cp -R "$appBundle" "$out/Applications/${appBundleName}"
    install -m0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
    ln -s \
      "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" \
      "$out/bin/${pname}"

    installedApp="$out/Applications/${appBundleName}"
    installedExtension="$installedApp/Contents/PlugIns/ClearlyQuickLook.appex"
    /usr/bin/xattr -cr "$installedApp"
    /usr/bin/codesign \
      --force \
      --options runtime \
      --sign - \
      --entitlements ClearlyQuickLook/ClearlyQuickLook.entitlements \
      "$installedExtension"
    /usr/bin/codesign \
      --force \
      --options runtime \
      --sign - \
      --entitlements Clearly/Clearly-AppStore.entitlements \
      "$installedApp"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    appBundle="$out/Applications/${appBundleName}"
    quickLookExtension="$appBundle/Contents/PlugIns/ClearlyQuickLook.appex"
    infoPlist="$appBundle/Contents/Info.plist"
    extensionInfoPlist="$quickLookExtension/Contents/Info.plist"

    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$infoPlist")" = \
      "com.sabotage.clearly"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$infoPlist")" = \
      "${version}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleName' "$infoPlist")" = \
      "${appName}"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$extensionInfoPlist")" = \
      "com.sabotage.clearly.quicklook"

    for key in SUFeedURL SUPublicEDKey SUEnableInstallerLauncherService; do
      if /usr/libexec/PlistBuddy -c "Print :$key" "$infoPlist" >/dev/null 2>&1; then
        echo "unexpected Sparkle key in source-built app: $key" >&2
        exit 1
      fi
    done
    if find "$appBundle" -name 'Sparkle*' -print -quit | grep -q .; then
      echo "unexpected Sparkle payload in source-built app" >&2
      exit 1
    fi

    /usr/bin/lipo "$appBundle/Contents/MacOS/${appName}" \
      -verify_arch ${darwinArch}
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$appBundle"
    test -x "$out/bin/${pname}"

    runHook postInstallCheck
  '';

  # Generic fixup may mutate Mach-O payloads after the final nested signing.
  dontFixup = true;

  passthru = {
    inherit swiftDeps;
    macApp = {
      bundleName = appBundleName;
      bundleRelPath = "Applications/${appBundleName}";
      installMode = "copy";
    };
  };

  meta = with lib; {
    description = "Native Markdown editor for macOS";
    homepage = "https://clearly.md/";
    license = licenses.fsl11Mit;
    mainProgram = pname;
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ fromSource ];
  };
}
