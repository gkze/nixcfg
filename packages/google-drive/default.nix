{
  _7zz,
  darwin,
  fetchurl,
  lib,
  mkMacAppPassthru,
  python3,
  selfSource,
  stdenvNoCC,
  system,
  ...
}:
let
  # 7-Zip and dissect.apfs's INode.open both misread the ever-cloned
  # GoogleDrive.pkg inode (extents keyed by private_id); see extract_apfs_file.py.
  apfsPython = python3.withPackages (ps: [ ps.dissect-apfs ]);
  packageArch =
    {
      aarch64-darwin = "arm64";
    }
    .${system};
in
# Not mkDmgApp7zz/mkPkgApp: the DMG wraps an installer pkg whose payload sits
# in a per-architecture sub-package, so neither the DMG-bundle nor the
# pkg-source helper can locate the app without a nested-unpack mode.
stdenvNoCC.mkDerivation {
  pname = "google-drive";
  inherit (selfSource) version;

  passthru = mkMacAppPassthru { bundleName = "Google Drive.app"; };

  src = fetchurl {
    url = selfSource.urls.${system};
    hash = selfSource.hashes.${system};
  };

  nativeBuildInputs = [
    _7zz
    darwin.xattr
  ];

  dontFixup = true;
  dontUnpack = true;

  installPhase = ''
    runHook preInstall

    unpack_dir="$TMPDIR/google-drive-unpack"
    mkdir -p "$unpack_dir" "$out/Applications" "$out/bin"
    7zz x -tdmg -y "$src" -o"$unpack_dir/dmg" '*.apfs'
    apfs_images=("$unpack_dir"/dmg/*.apfs)
    if [ "''${#apfs_images[@]}" -ne 1 ] || [ ! -f "''${apfs_images[0]}" ]; then
      echo "google-drive: expected exactly one APFS partition in the DMG" >&2
      exit 1
    fi
    ${lib.getExe apfsPython} ${./extract_apfs_file.py} \
      "''${apfs_images[0]}" /GoogleDrive.pkg "$unpack_dir/GoogleDrive.pkg"
    /usr/sbin/pkgutil --expand-full "$unpack_dir/GoogleDrive.pkg" "$unpack_dir/pkg"
    cp -R "$unpack_dir/pkg/GoogleDrive_${packageArch}.pkg/Payload/Google Drive.app" "$out/Applications/Google Drive.app"
    ${darwin.xattr}/bin/xattr -cr "$out/Applications/Google Drive.app"
    ln -s "$out/Applications/Google Drive.app/Contents/MacOS/Google Drive" "$out/bin/google-drive"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Google Drive sync client";
    homepage = "https://www.google.com/drive/";
    license = licenses.unfree;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
    mainProgram = "google-drive";
  };
}
