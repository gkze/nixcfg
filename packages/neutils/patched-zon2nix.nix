# Build the pinned zon2nix source with a plain-URL fetch that uses Nix.
#
# Revision 0ece5ed hashes http(s) dependencies with Zig's std.http.Client.
# Every handshake failure becomes TlsInitializationFailed. Zig 0.15 and the
# 0.16 that builds zon2nix load Darwin roots by parsing keychain files and
# ignore SSL_CERT_FILE, so exporting that variable does not fix the client.
# Update 41698221446 prepare-darwin-1 fetched some neutils dependencies,
# then failed the same way on later ones, including a fresh process whose
# first plain-URL hash never reached zig fetch.
#
# `nix store prefetch-file` hashes the downloaded file. That is the fetchurl
# hash already recorded in build.zig.zon.nix (lua-5.1.5 is
# sha256-JkD8VqeV8p0o7xXhPDSkfiI5YLAkDoywqC2bBzhpUzM=). Upstream main's
# nix-prefetch-url --unpack would stop matching those hashes, so this keeps
# the pin and replaces only the plain fetch. The child uses the Nix already
# on PATH, the same client that is talking to this machine's daemon.
#
# The pinned zon2nix flake builds that source with zig-overlay's 0.16.0.
# Evaluating its packages forces `lib.attrNames zig.packages`, and zig-overlay
# constructs `packages` with `lib.mapAttrs` over a nixpkgs import per system.
# Naming the systems therefore evaluates four nixpkgs plus every Zig version.
# Update 41796475737 prepare-darwin-1 died there: the worker disconnected
# while realizing `zon2nix-with-ca-bundle`, immediately after fetching
# codeberg.org/jcollie/zig-overlay, and every other client on that daemon
# kept building. nixpkgs already ships Zig 0.16.0 as `zig_0_16`. Call the
# upstream package with that compiler and never follow the overlay.
#
# The wrapper pins cacert. Hosted Darwin Nix injects SSL_CERT_FILE of
# /no-cert-file.crt into sandboxed builds; a child curl then fails with
# error 77. zon2nix runs outside that sandbox, and the wrapper still
# replaces the variable so a poisoned parent environment cannot win.
{ root, system }:
let
  pkgs = import root.inputs.nixpkgs { inherit system; };
  src = pkgs.applyPatches {
    name = "zon2nix-prefetch-file";
    src = root.inputs.zon2nix;
    patches = [ ./zon2nix-prefetch-file.patch ];
  };
  # package.nix sets `src = lib.cleanSource ./.` to the unpatched pin.
  # The patched tree replaces it. `zig_0_16` is Nixpkgs' Zig 0.16.0, the
  # compiler the pinned flake selects, without evaluating zig-overlay.
  unwrapped = (pkgs.callPackage (root.inputs.zon2nix + "/package.nix") {
    zig = pkgs.zig_0_16;
  }).overrideAttrs (_old: {
    inherit src;
  });
  certFile = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
in
pkgs.runCommand "zon2nix-with-ca-bundle"
  {
    nativeBuildInputs = [ pkgs.makeWrapper ];
  }
  ''
    mkdir -p "$out/bin"
    makeWrapper ${unwrapped}/bin/zon2nix "$out/bin/zon2nix" \
      --set SSL_CERT_FILE "${certFile}" \
      --set NIX_SSL_CERT_FILE "${certFile}" \
      --set CURL_CA_BUNDLE "${certFile}"
  ''
