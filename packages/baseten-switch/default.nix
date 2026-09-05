{
  buildGoModule,
  fetchFromGitHub,
  lib,
  outputs,
  runCommand,
  selfSource,
  ...
}:
let
  pname = "baseten-switch";
  inherit (selfSource) commit version;

  src = fetchFromGitHub {
    owner = "basetenlabs";
    repo = pname;
    rev = commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  meta = with lib; {
    description = "Local gateway routing AI coding harnesses between native providers and Baseten";
    homepage = "https://github.com/basetenlabs/baseten-switch";
    license = licenses.mit;
    mainProgram = "baseten-switch";
    platforms = platforms.darwin;
    sourceProvenance = with sourceTypes; [ fromSource ];
  };

  package = buildGoModule {
    inherit
      meta
      pname
      src
      version
      ;

    modRoot = "gateway";
    vendorHash = outputs.lib.sourceHash pname "vendorHash";

    # Finder-launched apps do not inherit Home Manager's PATH. Keep the
    # packaged CLI discoverable and use Xcode's Swift toolchain through a
    # reviewed patch that must continue to apply to the pinned release.
    patches = [ ./nix-managed.patch ];

    # Match upstream's release build: one universal Go CLI plus one universal,
    # ad-hoc-signed SwiftUI app bundle. The upstream CI build number is not part
    # of the source tag, so the valid, monotonic package version is used here.
    buildPhase = ''
      runHook preBuild

      repo_root="$(dirname "$PWD")"
      cli_stage="$TMPDIR/baseten-switch-cli"
      for go_arch in arm64 amd64; do
        env GOOS=darwin GOARCH="$go_arch" CGO_ENABLED=0 \
          go build -trimpath \
            -ldflags "-s -w -buildid= -X github.com/basetenlabs/baseten-switch/gateway/internal/version.Version=v${version}" \
            -o "$cli_stage-$go_arch" ./cmd/baseten-switch
      done
      /usr/bin/lipo -create \
        -output "$cli_stage" \
        "$cli_stage-arm64" \
        "$cli_stage-amd64"
      chmod 0755 "$cli_stage"
      /usr/bin/codesign --force --sign - "$cli_stage"

      swift_home="$TMPDIR/swift-home"
      swift_build="$TMPDIR/swift-build"
      swift_clang_cache="$TMPDIR/clang-module-cache"
      swift_module_cache="$TMPDIR/swift-module-cache"
      mkdir -p "$swift_home" "$swift_clang_cache" "$swift_module_cache"
      env \
        -u AR \
        -u CC \
        -u CXX \
        -u LD \
        -u NIX_CFLAGS_COMPILE \
        -u NIX_LDFLAGS \
        HOME="$swift_home" \
        CFFIXED_USER_HOME="$swift_home" \
        DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer" \
        PATH="/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH" \
        SDKROOT="/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk" \
        CLANG_MODULE_CACHE_PATH="$swift_clang_cache" \
        SWIFTPM_MODULECACHE_OVERRIDE="$swift_module_cache" \
        BASETEN_SWITCH_SWIFT_BUILD_FLAGS="--disable-sandbox --scratch-path $swift_build" \
        BASETEN_SWITCH_MARKETING_VERSION="${version}" \
        BASETEN_SWITCH_BUILD_NUMBER="${version}" \
        BASETEN_SWITCH_RELEASE_SIGNING_MODE=adhoc \
        "$repo_root/scripts/build-menubar.sh" --variant stable --release

      runHook postBuild
    '';

    # buildGoModule's default check phase calls a helper from its default build
    # phase. Define the source project's release-gate test explicitly because
    # the universal CLI/app build above replaces that phase.
    checkPhase = ''
      runHook preCheck
      go test -p 1 ./...

      repo_root="$(dirname "$PWD")"
      swift_test_home="$TMPDIR/swift-test-home"
      swift_test_build="$TMPDIR/swift-test-build"
      swift_test_clang_cache="$TMPDIR/swift-test-clang-module-cache"
      swift_test_module_cache="$TMPDIR/swift-test-module-cache"
      mkdir -p \
        "$swift_test_home" \
        "$swift_test_clang_cache" \
        "$swift_test_module_cache"
      (
        cd "$repo_root/mac/BasetenSwitch"
        env \
          -u AR \
          -u CC \
          -u CXX \
          -u LD \
          -u NIX_CFLAGS_COMPILE \
          -u NIX_LDFLAGS \
          HOME="$swift_test_home" \
          CFFIXED_USER_HOME="$swift_test_home" \
          DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer" \
          PATH="/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
          SDKROOT="/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk" \
          CLANG_MODULE_CACHE_PATH="$swift_test_clang_cache" \
          SWIFTPM_MODULECACHE_OVERRIDE="$swift_test_module_cache" \
          /usr/bin/swift test \
            --disable-sandbox \
            --scratch-path "$swift_test_build"
      )
      runHook postCheck
    '';

    installPhase = ''
      runHook preInstall

      repo_root="$(dirname "$PWD")"
      app_bundle="$out/Applications/Baseten Switch.app"
      bundled_cli="$app_bundle/Contents/Resources/baseten-switch"
      mkdir -p "$out/Applications" "$out/bin"
      cp -R \
        "$repo_root/mac/BasetenSwitch/dist/Baseten Switch.app" \
        "$app_bundle"
      install -m0755 "$TMPDIR/baseten-switch-cli" "$bundled_cli"
      ln -s "$bundled_cli" "$out/bin/baseten-switch"
      /usr/bin/lipo "$bundled_cli" -verify_arch arm64 x86_64
      /usr/bin/codesign \
        --force \
        --deep \
        --sign - \
        --preserve-metadata=identifier,entitlements,flags,runtime \
        "$app_bundle"
      install -Dm0644 "$repo_root/LICENSE" \
        "$out/share/licenses/${pname}/LICENSE"
      install -Dm0644 "$repo_root/THIRD_PARTY_NOTICES.md" \
        "$out/share/licenses/${pname}/THIRD_PARTY_NOTICES.md"

      test "$(/usr/bin/readlink "$out/bin/baseten-switch")" = "$bundled_cli"
      test "$("$bundled_cli" --version)" = "baseten-switch v${version}"
      /usr/bin/codesign --verify --deep --strict --verbose=2 \
        "$app_bundle"
      /usr/bin/codesign --verify --strict --verbose=2 \
        "$bundled_cli"

      runHook postInstall
    '';

    # Fixup would mutate the signed Mach-O payloads after upstream's release
    # script validates them.
    dontFixup = true;

    passthru = {
      # macApps routing intentionally keeps the app-bearing derivation out of
      # home.packages. This app-free view exposes the companion CLI without
      # creating a duplicate managed application bundle.
      cliPackage = runCommand "${pname}-cli-${version}" { inherit meta; } ''
        mkdir -p "$out/bin"
        ln -s "${package}/Applications/Baseten Switch.app/Contents/Resources/baseten-switch" "$out/bin/baseten-switch"
      '';

      macApp = {
        bundleName = "Baseten Switch.app";
        bundleRelPath = "Applications/Baseten Switch.app";
        installMode = "copy";
      };
    };
  };
in
package
