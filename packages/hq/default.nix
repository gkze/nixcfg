{
  cctools,
  darwin,
  fetchurl,
  gzip,
  gnutar,
  lib,
  nodejs_24,
  python3,
  selfSource,
  stdenv,
  ...
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
stdenv.mkDerivation {
  pname = "hq";
  inherit (selfSource) version;

  src = fetchurl {
    url = selfSource.urls.${stdenv.hostPlatform.system};
    hash = selfSource.hashes.${stdenv.hostPlatform.system};
  };

  strictDeps = true;
  dontUnpack = true;
  dontFixup = true;

  nativeBuildInputs = [
    cctools
    darwin.xattr
    gzip
    gnutar
    python3
  ];

  buildPhase = ''
    runHook preBuild

    $CC \
      -Wall \
      -Wextra \
      -Werror \
      -mmacosx-version-min=13.0 \
      -DNODE_EXECUTABLE='"${lib.getExe nodejs_24}"' \
      ${./recall-launcher.c} \
      -o recall-desktop-sdk

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    unpackRoot="$TMPDIR/hq-unpack"
    app="$out/Applications/HQ.app"
    mkdir -p "$unpackRoot" "$out/Applications" "$out/bin"
    ${lib.getExe gnutar} -xzf "$src" -C "$unpackRoot"
    if [ ! -d "$unpackRoot/HQ.app" ]; then
      echo "HQ release archive does not contain the reviewed HQ.app root" >&2
      exit 1
    fi
    unexpectedApps="$(${lib.getExe python3} - "$unpackRoot" <<'PY'
    from pathlib import Path
    import sys

    root = Path(sys.argv[1])
    apps = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.app"))
    if apps != ["HQ.app"]:
        raise SystemExit(f"unexpected HQ app inventory: {apps!r}")
    PY
    )"
    test -z "$unexpectedApps"

    cp -R "$unpackRoot/HQ.app" "$app"
    chmod -R u+w "$app"
    rm -rf "$app/Contents/_CodeSignature" "$app/Contents/CodeResources"
    rm -f "$app/Contents/MacOS/._recall-desktop-sdk"
    ${darwin.xattr}/bin/xattr -cr "$app"

    mainExecutable="$app/Contents/MacOS/hq-sync-menubar"
    recallLauncher="$app/Contents/MacOS/recall-desktop-sdk"
    uiRecorder="$app/Contents/Resources/recall-sdk-bridge/node_modules/@recallai/desktop-sdk/Frameworks/libui_recorder.dylib"
    bridge="$app/Contents/Resources/recall-sdk-bridge/bridge.mjs"
    for required in "$mainExecutable" "$uiRecorder" "$bridge"; do
      if [ ! -f "$required" ]; then
        echo "HQ release archive is missing required runtime path: $required" >&2
        exit 1
      fi
    done

    PYTHONPATH=${
      lib.fileset.toSource {
        root = ./.;
        fileset = ./policy_contract.py;
      }
    } ${lib.getExe python3} ${./patch_updater.py} "$mainExecutable"
    install -m0755 recall-desktop-sdk "$recallLauncher"
    ${cctools}/bin/install_name_tool \
      -id @rpath/libui_recorder.dylib \
      "$uiRecorder"

    machoInventory="$TMPDIR/hq-macho-inventory"
    if ! find "$app" -type f -print0 > "$machoInventory"; then
      echo "failed to inventory HQ app files for signing" >&2
      exit 1
    fi
    while IFS= read -r -d $'\0' candidate; do
      if ! fileDescription="$(/usr/bin/file -b "$candidate")"; then
        echo "failed to classify HQ app file: $candidate" >&2
        exit 1
      fi
      case "$fileDescription" in
        *Mach-O*)
          /usr/bin/codesign \
            --force \
            --sign - \
            --timestamp=none \
            --options runtime \
            --entitlements ${./Entitlements.plist} \
            "$candidate"
          ;;
      esac
    done < "$machoInventory"

    frameworkInventory="$TMPDIR/hq-framework-inventory"
    if ! find "$app" -type d -name '*.framework' -print0 > "$frameworkInventory"; then
      echo "failed to inventory HQ frameworks for signing" >&2
      exit 1
    fi
    while IFS= read -r -d $'\0' framework; do
      /usr/bin/codesign \
        --force \
        --sign - \
        --timestamp=none \
        --options runtime \
        --entitlements ${./Entitlements.plist} \
        "$framework"
    done < "$frameworkInventory"

    /usr/bin/codesign \
      --force \
      --sign - \
      --timestamp=none \
      --options runtime \
      --entitlements ${./Entitlements.plist} \
      "$mainExecutable"
    /usr/bin/codesign \
      --force \
      --sign - \
      --timestamp=none \
      --options runtime \
      --entitlements ${./Entitlements.plist} \
      "$app"

    ln -s "$mainExecutable" "$out/bin/hq"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    app="$out/Applications/HQ.app"
    mainExecutable="$app/Contents/MacOS/hq-sync-menubar"
    recallLauncher="$app/Contents/MacOS/recall-desktop-sdk"
    infoPlist="$app/Contents/Info.plist"
    bridge="$app/Contents/Resources/recall-sdk-bridge/bridge.mjs"

    for required in \
      "$app" \
      "$mainExecutable" \
      "$recallLauncher" \
      "$infoPlist" \
      "$bridge" \
      "$out/bin/hq"
    do
      if [ ! -e "$required" ]; then
        echo "missing required Nix-managed HQ path: $required" >&2
        exit 1
      fi
    done

    PYTHONPATH=${
      lib.fileset.toSource {
        root = ./.;
        fileset = ./policy_contract.py;
      }
    } ${lib.getExe python3} ${./validate_artifact.py} \
      "$infoPlist" \
      "$mainExecutable" \
      "${selfSource.version}"

    test "$(${cctools}/bin/lipo -archs "$recallLauncher")" = arm64
    ${cctools}/bin/lipo "$mainExecutable" -verify_arch arm64
    if ! strings "$recallLauncher" | grep -Fqx ${lib.escapeShellArg (lib.getExe nodejs_24)}; then
      echo "HQ Recall launcher does not retain the immutable Node executable" >&2
      exit 1
    fi

    machoInventory="$TMPDIR/hq-install-check-machos"
    signatureAudit="$TMPDIR/hq-signature-audit"
    signedMachoInventory="$signatureAudit/machos"
    mkdir -p "$signatureAudit"
    : > "$signedMachoInventory"
    if ! find "$app" -type f -print0 > "$machoInventory"; then
      echo "failed to inventory final HQ app Mach-O files" >&2
      exit 1
    fi
    machoCount=0
    while IFS= read -r -d $'\0' candidate; do
      if ! fileDescription="$(/usr/bin/file -b "$candidate")"; then
        echo "failed to classify final HQ app file: $candidate" >&2
        exit 1
      fi
      case "$fileDescription" in
        *Mach-O*)
          machoCount=$((machoCount + 1))
          ${cctools}/bin/lipo "$candidate" -verify_arch arm64
          /usr/bin/codesign --verify --strict --verbose=2 "$candidate"
          if ! /usr/bin/codesign -d --verbose=4 --entitlements :- "$candidate" \
            > "$signatureAudit/$machoCount.entitlements.plist" \
            2> "$signatureAudit/$machoCount.details"
          then
            echo "failed to inspect HQ Mach-O signature: $candidate" >&2
            exit 1
          fi
          case "$fileDescription" in
            *executable*)
              : > "$signatureAudit/$machoCount.requires-entitlements"
              ;;
          esac
          printf '%s\0' "$candidate" >> "$signedMachoInventory"
          ;;
      esac
    done < "$machoInventory"
    if [ "$machoCount" -ne 110 ]; then
      echo "HQ Mach-O inventory expected 110 files, got $machoCount" >&2
      exit 1
    fi

    if ! /usr/bin/codesign -d --verbose=4 --entitlements :- "$app" \
      > "$signatureAudit/app.entitlements.plist" \
      2> "$signatureAudit/app.details"
    then
      echo "failed to inspect final HQ app signature" >&2
      exit 1
    fi
    ${lib.getExe python3} ${./validate_signatures.py} \
      "$signatureAudit" \
      "$signedMachoInventory" \
      "$machoCount" \
      "$app"

    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"

    runHook postInstallCheck
  '';

  passthru.macApp = {
    bundleId = "ai.indigo.hq-sync-menubar";
    bundleName = "HQ.app";
    bundleRelPath = "Applications/HQ.app";
    installMode = "copy";
  };

  meta = {
    description = "Nix-managed HQ meeting assistant";
    homepage = "https://github.com/indigoai-us/hq-desktop-app";
    license = lib.licenses.unfree;
    mainProgram = "hq";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
