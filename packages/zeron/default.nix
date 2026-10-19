{
  cmake,
  fetchFromGitHub,
  imagemagick,
  lib,
  libicns,
  outputs,
  pkg-config,
  python3,
  rustPlatform,
  stdenv,
  ...
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  pname = "zeron";
  source = outputs.lib.sourceEntry pname;
  inherit (source) version;
  rustTarget = stdenv.hostPlatform.rust.rustcTarget;
in
rustPlatform.buildRustPackage {
  inherit pname version;

  strictDeps = true;

  src = fetchFromGitHub {
    owner = "zeronsh";
    repo = "comet";
    rev = source.commit;
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  cargoHash = outputs.lib.sourceHash pname "cargoHash";
  cargoBuildFlags = [
    "-p"
    "zeron"
  ];

  nativeBuildInputs = [
    cmake
    pkg-config
    python3
  ];

  env.ZERON_NIX_MANAGED = "1";

  postPatch = ''
    ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD"
  '';

  # The upstream workspace test matrix includes daemon, network, and Linux
  # integration suites. Package validation exercises the release app target.
  doCheck = false;

  installPhase = ''
    runHook preInstall

    app="$out/Applications/Zeron.app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" "$out/bin"
    install -m0755 "target/${rustTarget}/release/zeron" \
      "$app/Contents/MacOS/zeron"
    substitute dist/macos/Info.plist "$app/Contents/Info.plist" \
      --replace-fail __VERSION__ ${lib.escapeShellArg version}

    icon_dir="$TMPDIR/zeron-icons"
    mkdir -p "$icon_dir"
    for size in 16 32 64 128 256; do
      ${lib.getExe imagemagick} dist/macos/icon-1024.png \
        -resize "''${size}x''${size}" "$icon_dir/''${size}.png"
    done
    ${lib.getExe imagemagick} dist/macos/icon-1024.png \
      -resize 512x512 "$icon_dir/512.png"
    cp dist/macos/icon-1024.png "$icon_dir/1024.png"
    ${lib.getExe' libicns "png2icns"} \
      "$app/Contents/Resources/zeron.icns" "$icon_dir"/*.png >/dev/null

    install -Dm0644 LICENSE "$out/share/licenses/${pname}/LICENSE"
    ln -s "$app/Contents/MacOS/zeron" "$out/bin/zeron"

    runHook postInstall
  '';

  postFixup = ''
    app="$out/Applications/Zeron.app"
    /usr/bin/codesign --force --sign - "$app"
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    app="$out/Applications/Zeron.app"
    executable="$app/Contents/MacOS/zeron"
    plist="$app/Contents/Info.plist"

    for path in "$app" "$executable" "$plist" "$out/bin/zeron"; do
      if [ ! -e "$path" ]; then
        echo "missing required Zeron runtime path: $path" >&2
        exit 1
      fi
    done

    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist")" = \
      "sh.zeron.app"
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")" = \
      ${lib.escapeShellArg version}
    test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist")" = \
      ${lib.escapeShellArg version}
    /usr/bin/file "$executable" | grep -F "Mach-O 64-bit executable arm64"
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"

    if update_output="$("$out/bin/zeron" update --check 2>&1)"; then
      echo "Nix-managed Zeron unexpectedly accepted its self-update command" >&2
      exit 1
    fi
    case "$update_output" in
      *"updates are managed by Nix"*) ;;
      *)
        echo "Zeron self-update policy failed closed with an unexpected error:" >&2
        echo "$update_output" >&2
        exit 1
        ;;
    esac

    runHook postInstallCheck
  '';

  passthru.macApp = {
    bundleId = "sh.zeron.app";
    bundleName = "Zeron.app";
    bundleRelPath = "Applications/Zeron.app";
    installMode = "copy";
  };

  meta = {
    description = "Local-first control plane for coding agents";
    homepage = "https://github.com/zeronsh/comet";
    changelog = "https://github.com/zeronsh/comet/releases/tag/v${version}";
    license = lib.licenses.mit;
    mainProgram = pname;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = [ lib.sourceTypes.fromSource ];
  };
}
