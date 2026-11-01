{
  final,
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
  # Rio's rust-toolchain.toml pins an exact stable release that can be newer
  # than nixpkgs' rustc (0.5.29 requires 1.99.1 while nixpkgs ships 1.99.0).
  # The updater records that release, so build with exactly that toolchain.
  rustToolchainVersion =
    (selfSource.pins or { }).rustToolchain or (throw "rio sources.json is missing pins.rustToolchain");
  rustToolchain =
    final.rust-bin.stable.${rustToolchainVersion}.default
      or (throw "rust-overlay has no stable Rust ${rustToolchainVersion} for Rio; advance the rust-overlay pin");
  rustPlatform = final.makeRustPlatform {
    cargo = rustToolchain;
    rustc = rustToolchain;
  };
  # Call the nixpkgs package itself: `prev.rio` is the registry forwarder
  # (packages/rio/default.nix, `{ rio, ... }: rio`), whose override accepts and
  # silently drops `rustPlatform`, leaving nixpkgs' rustc in the build.
  nixpkgsRio = final.callPackage (prev.path + "/pkgs/by-name/ri/rio/package.nix") {
    inherit rustPlatform;
  };
in
prev.lib.optionalAttrs (prev.stdenv.hostPlatform.system == "aarch64-darwin") {
  rio = nixpkgsRio.overrideAttrs (old: {
    inherit version src;

    # The nixpkgs package rewrites an older Rio MSRV; this source builds with
    # the exact toolchain its rust-toolchain.toml pins.
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

    # nixpkgs' postInstall already compiles the xterm-rio and rio terminfo
    # entries; the Rio source ships no rio-direct entry.
    postInstall = old.postInstall + ''
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

    passthru = (old.passthru or { }) // final.mkMacAppPassthru { bundleName = "Rio.app"; };

    meta = (old.meta or { }) // {
      platforms = [ "aarch64-darwin" ];
    };
  });
}
