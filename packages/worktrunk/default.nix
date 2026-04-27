{
  inputs,
  lib,
  pkgs,
  stdenv,
  rust-bin,
  pkg-config,
  tree-sitter,
  libiconv,
  ...
}:
let
  src = inputs.worktrunk;
  toolchainChannel =
    (builtins.fromTOML (builtins.readFile "${src}/rust-toolchain.toml")).toolchain.channel;
  rustToolchain = rust-bin.stable.${toolchainChannel}.default.override {
    extensions = [
      "rust-src"
      "rust-analyzer"
    ];
  };
  craneLib = (inputs.crane.mkLib pkgs).overrideToolchain rustToolchain;

  filteredSrc = lib.cleanSourceWith {
    inherit src;
    filter =
      path: type:
      (craneLib.filterCargoSources path type)
      || (lib.hasInfix "/templates/" path)
      || (baseNameOf (dirOf path) == "templates")
      || (lib.hasInfix "/dev/" path)
      || (baseNameOf (dirOf path) == "dev");
  };

  vendorSrc = lib.cleanSourceWith {
    inherit src;
    filter = path: _type: lib.hasInfix "/vendor/" path || lib.hasSuffix "/vendor" path;
  };

  commonArgs = {
    src = filteredSrc;
    strictDeps = true;

    nativeBuildInputs = [
      pkg-config
    ];

    buildInputs = [
      tree-sitter
    ]
    ++ lib.optionals stdenv.isDarwin [
      libiconv
    ];

    VERGEN_IDEMPOTENT = "1";
    VERGEN_GIT_DESCRIBE =
      src.shortRev or (src.dirtyShortRev or "nix-${src.lastModifiedDate or "unknown"}");
  };

  cargoArtifacts = craneLib.buildDepsOnly (
    builtins.removeAttrs commonArgs [ "src" ]
    // {
      dummySrc = craneLib.mkDummySrc {
        inherit (commonArgs) src;
        extraDummyScript = ''
          rm -rf $out/vendor
          cp -r ${vendorSrc}/vendor $out/vendor
          chmod -R u+w $out/vendor
        '';
      };
    }
  );
in
craneLib.buildPackage (
  commonArgs
  // {
    inherit cargoArtifacts;

    doCheck = false;

    meta = {
      description = "A CLI for Git worktree management, designed for parallel AI agent workflows";
      homepage = "https://github.com/max-sixty/worktrunk";
      license = with lib.licenses; [
        mit
        asl20
      ];
      maintainers = [ ];
      mainProgram = "wt";
    };
  }
)
