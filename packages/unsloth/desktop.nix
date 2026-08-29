{
  backend,
  cargo-tauri,
  cargoHash,
  frontend,
  lib,
  pkg-config,
  python3,
  rustPlatform,
  rustToolchain,
  src,
  stdenv,
  version,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
rustPlatform.buildRustPackage {
  pname = "unsloth-desktop";
  inherit
    cargoHash
    src
    version
    ;

  cargoRoot = "studio/src-tauri";
  buildAndTestSubdir = "studio";
  strictDeps = true;

  # Cargo source identity and release metadata must be identical while
  # vendoring and during the final offline build.
  cargoPatches = [
    ./studio-release-version.patch
    ./studio-fix-path-env-revision.patch
  ];

  nativeBuildInputs = [
    cargo-tauri.hook
    pkg-config
    python3
  ];

  env = {
    CARGO_NET_OFFLINE = "true";
    CMAKE_OSX_DEPLOYMENT_TARGET = "13.3";
    MACOSX_DEPLOYMENT_TARGET = "13.3";
    UNSLOTH_NIX_BACKEND = "${backend}/bin/unsloth";
    UNSLOTH_NIX_MANAGED = "1";
  };

  postPatch = ''
    PYTHONPATH=${
      lib.fileset.toSource {
        root = ../..;
        fileset = lib.fileset.unions [
          ../../lib/__init__.py
          ../../lib/exact_text_patch.py
        ];
      }
    } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD"
    rm -rf studio/frontend/dist
    mkdir -p studio/frontend/dist
    cp -R ${frontend}/dist/. studio/frontend/dist/
    chmod -R u+w studio/frontend/dist
  '';

  cargoBuildFlags = [ "--locked" ];
  tauriBundleType = "app";
  tauriBuildFlags = [
    "--no-sign"
    "--verbose"
  ];
  doCheck = false;

  # cargo-tauri propagates the Cargo used to build its CLI. Keep the audited
  # Rust 1.89 toolchain first for the hook's later `cargo tauri` invocation.
  preBuild = ''
    export PATH="${rustToolchain}/bin:$PATH"
  '';

  postInstall = ''
    mkdir -p "$out/bin" "$out/share/licenses/unsloth-desktop"
    ln -s ../Applications/Unsloth.app/Contents/MacOS/unsloth-studio \
      "$out/bin/unsloth-studio"
    install -m0644 "$NIX_BUILD_TOP/$sourceRoot/studio/LICENSE.AGPL-3.0" \
      "$out/share/licenses/unsloth-desktop/LICENSE"
  '';

  postFixup = ''
    app="$out/Applications/Unsloth.app"
    /usr/bin/xattr -cr "$app"
    /usr/bin/codesign --force --deep --sign - "$app"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    app="$out/Applications/Unsloth.app"
    executable="$app/Contents/MacOS/unsloth-studio"
    plist="$app/Contents/Info.plist"
    test -d "$app"
    test -x "$executable"
    test -L "$out/bin/unsloth-studio"
    /usr/bin/lipo "$executable" -verify_arch arm64

    test "$(${lib.getExe python3} - "$plist" <<'PY'
    import plistlib
    import sys

    with open(sys.argv[1], "rb") as stream:
        plist = plistlib.load(stream)
    expected = {
        "CFBundleExecutable": "unsloth-studio",
        "CFBundleIdentifier": "ai.unsloth.studio",
        "CFBundleName": "Unsloth",
        "CFBundleShortVersionString": "${version}",
    }
    for key, value in expected.items():
        if plist.get(key) != value:
            raise SystemExit(f"{key}: expected {value!r}, got {plist.get(key)!r}")
    print("ok")
    PY
    )" = ok

    ${stdenv.cc.bintools}/bin/strings -a "$executable" | \
      grep -F '${backend}/bin/unsloth' >/dev/null
    if grep -R -a -F \
      'https://github.com/unslothai/unsloth/releases/latest/download/latest.json' \
      "$app"
    then
      echo "Unsloth.app retains its mutable updater endpoint" >&2
      exit 1
    fi
    if find "$app" -type f \
      \( -name install.sh -o -name install.ps1 -o -name setup.sh -o -name setup.ps1 \
        -o -name install_python_stack.py \) \
      -print -quit | grep -q .
    then
      echo "Unsloth.app embeds a mutable backend installer" >&2
      exit 1
    fi
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"

    runHook postInstallCheck
  '';

  passthru.macApp = {
    bundleId = "ai.unsloth.studio";
    bundleName = "Unsloth.app";
    bundleRelPath = "Applications/Unsloth.app";
    installMode = "copy";
  };

  meta = {
    description = "Source-built Nix-owned Unsloth Studio";
    homepage = "https://github.com/unslothai/unsloth";
    license = lib.licenses.agpl3Only;
    mainProgram = "unsloth-studio";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = [ lib.sourceTypes.fromSource ];
  };
}
