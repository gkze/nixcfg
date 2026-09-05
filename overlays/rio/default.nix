{
  prev,
  selfSource,
  slib,
  ...
}:
let
  inherit (selfSource) commit version;
  src = prev.fetchFromGitHub {
    owner = "raphamorim";
    repo = "rio";
    rev = commit;
    hash = slib.sourceHash "rio" "srcHash";
  };
in
prev.lib.optionalAttrs (prev.stdenv.hostPlatform.system == "aarch64-darwin") {
  rio = prev.rio.overrideAttrs (old: {
    inherit version src;

    # The nixpkgs package pins an older Rio MSRV; the current source already
    # matches this flake's pinned Rust toolchain.
    postPatch = "";

    cargoDeps = prev.rustPlatform.fetchCargoVendor {
      inherit src;
      hash = slib.sourceHash "rio" "cargoHash";
    };
    cargoBuildFlags = [
      "-p"
      "rioterm"
    ];
    cargoTestFlags = [
      "-p"
      "rioterm"
    ];

    postInstall =
      prev.lib.replaceStrings [ "tic -xe rio,rio-direct" ] [ "tic -xe xterm-rio,rio,rio-direct" ]
        old.postInstall
      + ''
        substituteInPlace "$out/Applications/Rio.app/Contents/Info.plist" \
          --replace-fail '{{.Version}}.{{.Now.Format "20060102150405"}}' '${version}' \
          --replace-fail '{{.Version}}' '${version}'

        rm "$out/Applications/Rio.app/Contents/MacOS/rio"
        mv "$out/bin/rio" "$out/Applications/Rio.app/Contents/MacOS/rio"
        ln -s "../Applications/Rio.app/Contents/MacOS/rio" "$out/bin/rio"
      '';

    postFixup = (old.postFixup or "") + ''
      /usr/bin/xattr -cr "$out/Applications/Rio.app"
      /usr/bin/codesign --force --deep --sign - "$out/Applications/Rio.app"
    '';

    doInstallCheck = true;
    installCheckPhase = (old.installCheckPhase or "") + ''
      runHook preInstallCheck

      test -L "$out/bin/rio"
      test -x "$out/Applications/Rio.app/Contents/MacOS/rio"
      test ! -L "$out/Applications/Rio.app/Contents/MacOS/rio"
      if grep -Fq '{{' "$out/Applications/Rio.app/Contents/Info.plist"; then
        echo "Rio Info.plist still contains GoReleaser template placeholders" >&2
        exit 1
      fi
      /usr/bin/codesign --verify --deep --strict "$out/Applications/Rio.app"

      runHook postInstallCheck
    '';

    passthru = (old.passthru or { }) // {
      macApp = {
        bundleName = "Rio.app";
        bundleRelPath = "Applications/Rio.app";
        installMode = "copy";
      };
    };

    meta = (old.meta or { }) // {
      platforms = [ "aarch64-darwin" ];
    };
  });
}
