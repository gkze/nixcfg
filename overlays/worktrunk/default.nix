{
  final,
  inputs,
  ...
}:
let
  inherit (final)
    lib
    libiconv
    pkg-config
    stdenv
    tree-sitter
    ;

  src = inputs.worktrunk;
  toolchainChannel =
    (builtins.fromTOML (builtins.readFile "${src}/rust-toolchain.toml")).toolchain.channel;
  rustToolchain = final.rust-bin.stable.${toolchainChannel}.default;
  craneLib = (inputs.crane.mkLib final).overrideToolchain rustToolchain;

  filteredSrc = lib.cleanSourceWith {
    inherit src;
    filter =
      path: type:
      (craneLib.filterCargoSources path type)
      || (baseNameOf path == "gemini-extension.json")
      || (lib.hasInfix "/templates/" path)
      || (baseNameOf (dirOf path) == "templates")
      || (lib.hasInfix "/dev/" path)
      || (baseNameOf (dirOf path) == "dev");
  };

  commonArgs = {
    src = filteredSrc;
    strictDeps = true;

    nativeBuildInputs = [ pkg-config ];

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

  cargoArtifacts = craneLib.buildDepsOnly commonArgs;
in
{
  worktrunk = craneLib.buildPackage (
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
  );
}
