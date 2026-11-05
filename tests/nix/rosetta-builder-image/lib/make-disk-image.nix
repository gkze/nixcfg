# Stand-in for nixpkgs' nixos/lib/make-disk-image.nix. It records the arguments
# it was called with and returns an uninstantiated derivation shaped like the
# one `vmTools.runInLinuxVM` produces, including the passthru that
# nix-rosetta-builder reads.
args:
let
  image = derivation {
    name = "fixture-disk-image";
    system = "aarch64-linux";
    builder = "/bin/sh";
    requiredSystemFeatures = [
      "kvm"
      "benchmark"
    ];
  };
  passthru = {
    filePath = "nixos.qcow2";
    recordedArgs = removeAttrs args [
      "config"
      "lib"
      "pkgs"
    ];
  };
in
image // passthru // { inherit passthru; }
