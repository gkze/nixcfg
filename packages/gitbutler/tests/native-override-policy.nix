# Opt-in integration probe; intentionally excluded from the default check graph
# because it evaluates one complete GitButler crate2nix package graph.
# Run from the repository root with:
#
#   nix eval --offline --impure --option allow-import-from-derivation false \
#     --json --file packages/gitbutler/tests/native-override-policy.nix
let
  root = ../../..;
  outputs = builtins.getFlake "git+file://${toString root}";
  system = builtins.currentSystem;
  supportedSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];
  pkgs = outputs.pkgs.${system};
  inherit (pkgs) lib;

  package = lib.callPackageWith (pkgs // { inherit pkgs; }) ../default.nix {
    inherit (outputs) inputs;
    inherit outputs;
  };

  overrideAttrsFor =
    crateName:
    let
      override = package.passthru.crateOverrides.${crateName} or (_attrs: { });
    in
    override {
      inherit crateName;
      version = "0.0.0";
      buildInputs = [ ];
      nativeBuildInputs = [ ];
    };

  hasInput =
    expected: inputs: builtins.any (input: (input.drvPath or null) == expected.drvPath) inputs;
  inputNames = inputs: map lib.getName inputs;
  showInputs = inputs: lib.concatStringsSep ", " (inputNames inputs);

  sqliteAttrs = overrideAttrsFor "libsqlite3-sys";
  libgit2Attrs = overrideAttrsFor "libgit2-sys";
  opensslAttrs = overrideAttrsFor "openssl-sys";
  awsLcAttrs = overrideAttrsFor "aws-lc-sys";
in
# This deliberately evaluates the effective public package overrides. AST
# inspection cannot establish which nixpkgs inputs survive override composition.
assert builtins.elem system supportedSystems;
assert lib.assertMsg
  (
    !(hasInput pkgs.sqlite (sqliteAttrs.buildInputs or [ ]))
    && !(hasInput pkgs.pkg-config (sqliteAttrs.nativeBuildInputs or [ ]))
  )
  ''
    libsqlite3-sys inherited nixpkgs's system-SQLite override instead of using bundled SQLite:
      build inputs: ${showInputs (sqliteAttrs.buildInputs or [ ])}
      native inputs: ${showInputs (sqliteAttrs.nativeBuildInputs or [ ])}
  '';
assert lib.assertMsg
  (
    !(hasInput pkgs.libgit2 (libgit2Attrs.buildInputs or [ ]))
    && !(libgit2Attrs ? LIBGIT2_SYS_USE_PKG_CONFIG)
  )
  ''
    libgit2-sys inherited nixpkgs's system-libgit2 policy:
      build inputs: ${showInputs (libgit2Attrs.buildInputs or [ ])}
      LIBGIT2_SYS_USE_PKG_CONFIG: ${toString (libgit2Attrs.LIBGIT2_SYS_USE_PKG_CONFIG or "absent")}
  '';
assert lib.assertMsg (hasInput pkgs.zlib (libgit2Attrs.buildInputs or [ ])) ''
  libgit2-sys lost the zlib input required by its vendored build:
    build inputs: ${showInputs (libgit2Attrs.buildInputs or [ ])}
'';
assert lib.assertMsg
  (
    hasInput pkgs.openssl (opensslAttrs.buildInputs or [ ])
    && hasInput pkgs.pkg-config (opensslAttrs.nativeBuildInputs or [ ])
    && (opensslAttrs.OPENSSL_NO_VENDOR or null) == "1"
  )
  ''
    openssl-sys did not preserve nixpkgs's OpenSSL/pkg-config inputs plus OPENSSL_NO_VENDOR:
      build inputs: ${showInputs (opensslAttrs.buildInputs or [ ])}
      native inputs: ${showInputs (opensslAttrs.nativeBuildInputs or [ ])}
      OPENSSL_NO_VENDOR: ${toString (opensslAttrs.OPENSSL_NO_VENDOR or "absent")}
  '';
assert lib.assertMsg
  (
    hasInput pkgs.cmake (awsLcAttrs.nativeBuildInputs or [ ])
    && (awsLcAttrs.env.AWS_LC_SYS_CMAKE_BUILDER or null) == 1
  )
  ''
    aws-lc-sys did not preserve nixpkgs's CMake builder policy:
      native inputs: ${showInputs (awsLcAttrs.nativeBuildInputs or [ ])}
      AWS_LC_SYS_CMAKE_BUILDER: ${toString (awsLcAttrs.env.AWS_LC_SYS_CMAKE_BUILDER or "absent")}
  '';
{
  check = true;
  awsLcSys = {
    cmake = inputNames (awsLcAttrs.nativeBuildInputs or [ ]);
    cmakeBuilder = awsLcAttrs.env.AWS_LC_SYS_CMAKE_BUILDER;
  };
  libgit2Sys = {
    buildInputs = inputNames (libgit2Attrs.buildInputs or [ ]);
    usesPkgConfig = libgit2Attrs ? LIBGIT2_SYS_USE_PKG_CONFIG;
  };
  libsqlite3Sys = {
    buildInputs = inputNames (sqliteAttrs.buildInputs or [ ]);
    nativeBuildInputs = inputNames (sqliteAttrs.nativeBuildInputs or [ ]);
  };
  opensslSys = {
    buildInputs = inputNames (opensslAttrs.buildInputs or [ ]);
    nativeBuildInputs = inputNames (opensslAttrs.nativeBuildInputs or [ ]);
    noVendor = opensslAttrs.OPENSSL_NO_VENDOR;
  };
}
