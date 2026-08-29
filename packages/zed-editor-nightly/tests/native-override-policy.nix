# Opt-in integration probe; intentionally excluded from the default check graph
# because it evaluates one complete Zed crate2nix package graph.
# Run from the repository root with:
#
#   nix eval --offline --impure --option allow-import-from-derivation false \
#     --json --file packages/zed-editor-nightly/tests/native-override-policy.nix
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
  hasNamedInput = name: inputs: builtins.elem name (inputNames inputs);
  showInputs = inputs: lib.concatStringsSep ", " (inputNames inputs);

  neutralAttrs = overrideAttrsFor "client";
  livekitApiAttrs = overrideAttrsFor "livekit_api";
  protoAttrs = overrideAttrsFor "proto";
  mediaAttrs = overrideAttrsFor "media";
  gpuiAppleAttrs = overrideAttrsFor "gpui_apple";
  uiAttrs = overrideAttrsFor "ui";
  zedAttrs = overrideAttrsFor "zed";
  opensslAttrs = overrideAttrsFor "openssl-sys";
  zstdAttrs = overrideAttrsFor "zstd-sys";

  broadNativeInputs = [
    pkgs.cmake
    pkgs.curl
    pkgs.perl
    pkgs.pkg-config
    pkgs.protobuf
  ]
  ++ lib.optionals pkgs.stdenv.hostPlatform.isDarwin [
    pkgs.lld
    pkgs.xcodebuild
  ];
in
# This deliberately evaluates the effective public package overrides. AST
# inspection cannot establish which nixpkgs inputs survive override composition
# or whether project inputs are limited to their audited consumers.
assert builtins.elem system supportedSystems;
assert lib.assertMsg
  (builtins.all (input: !(hasInput input (neutralAttrs.nativeBuildInputs or [ ]))) broadNativeInputs)
  ''
    the neutral client crate inherited project-wide native tools:
      ${showInputs (neutralAttrs.nativeBuildInputs or [ ])}
  '';
assert lib.assertMsg (
  !(neutralAttrs ? PROTOC)
  && !(neutralAttrs ? NIX_LDFLAGS)
  && !(neutralAttrs ? ZSTD_SYS_USE_PKG_CONFIG)
  && !(neutralAttrs ? dontPatchELF)
) "the neutral client crate inherited consumer-specific build policy";
assert lib.assertMsg (
  hasInput pkgs.protobuf (livekitApiAttrs.nativeBuildInputs or [ ])
  && (livekitApiAttrs.PROTOC or null) == "${pkgs.protobuf}/bin/protoc"
  && hasInput pkgs.protobuf (protoAttrs.nativeBuildInputs or [ ])
  && (protoAttrs.PROTOC or null) == "${pkgs.protobuf}/bin/protoc"
) "the two protobuf-generating crates did not receive protoc exactly";
assert lib.assertMsg (
  pkgs.stdenv.hostPlatform.isLinux
  || (
    hasNamedInput "rust-bindgen-hook" (mediaAttrs.nativeBuildInputs or [ ])
    && hasInput pkgs.xcodebuild (mediaAttrs.nativeBuildInputs or [ ])
    && hasInput pkgs.xcodebuild (gpuiAppleAttrs.nativeBuildInputs or [ ])
    && hasInput pkgs.xcodebuild (uiAttrs.nativeBuildInputs or [ ])
    && hasInput pkgs.lld (zedAttrs.nativeBuildInputs or [ ])
    && !(hasInput pkgs.pkg-config (zedAttrs.nativeBuildInputs or [ ]))
  )
) "Darwin shader/media/link consumers lost xcodebuild or lld";
assert lib.assertMsg (
  pkgs.stdenv.hostPlatform.isDarwin
  || (
    !(hasNamedInput "rust-bindgen-hook" (mediaAttrs.nativeBuildInputs or [ ]))
    && hasInput pkgs.pkg-config (zedAttrs.nativeBuildInputs or [ ])
    && zedAttrs.NIX_LDFLAGS != ""
    && zedAttrs.dontPatchELF
  )
) "the Linux Zed runtime/link consumer lost pkg-config or rpath policy";
assert lib.assertMsg
  (
    hasInput pkgs.openssl (opensslAttrs.buildInputs or [ ])
    && hasInput pkgs.pkg-config (opensslAttrs.nativeBuildInputs or [ ])
  )
  ''
    openssl-sys did not preserve nixpkgs's native OpenSSL inputs:
      build inputs: ${showInputs (opensslAttrs.buildInputs or [ ])}
      native inputs: ${showInputs (opensslAttrs.nativeBuildInputs or [ ])}
  '';
assert lib.assertMsg
  (
    hasInput pkgs.zstd (zstdAttrs.buildInputs or [ ])
    && hasInput pkgs.pkg-config (zstdAttrs.nativeBuildInputs or [ ])
    && (zstdAttrs.ZSTD_SYS_USE_PKG_CONFIG or null) == true
  )
  ''
    zstd-sys did not preserve nixpkgs's native zstd policy:
      build inputs: ${showInputs (zstdAttrs.buildInputs or [ ])}
      native inputs: ${showInputs (zstdAttrs.nativeBuildInputs or [ ])}
  '';
{
  check = true;
  consumers = {
    gpuiApple = inputNames (gpuiAppleAttrs.nativeBuildInputs or [ ]);
    livekitApi = inputNames (livekitApiAttrs.nativeBuildInputs or [ ]);
    media = inputNames (mediaAttrs.nativeBuildInputs or [ ]);
    proto = inputNames (protoAttrs.nativeBuildInputs or [ ]);
    ui = inputNames (uiAttrs.nativeBuildInputs or [ ]);
    zed = {
      buildInputs = inputNames (zedAttrs.buildInputs or [ ]);
      nativeBuildInputs = inputNames (zedAttrs.nativeBuildInputs or [ ]);
    };
  };
  neutral = {
    buildInputs = inputNames (neutralAttrs.buildInputs or [ ]);
    nativeBuildInputs = inputNames (neutralAttrs.nativeBuildInputs or [ ]);
  };
}
