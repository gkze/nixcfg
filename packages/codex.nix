{
  craneLib,
  inputs,
  outputs,
  lib,
  stdenv,
  clang,
  cmake,
  gitMinimal,
  installShellFiles,
  makeBinaryWrapper,
  pkg-config,
  ninja,
  go,
  perl,
  libclang,
  openssl,
  apple-sdk_15,
  ripgrep,
  gnused,
  gnugrep,
  ...
}:
let
  slib = outputs.lib;
  version = slib.getFlakeVersion "codex";
  src = "${inputs.codex}/codex-rs";
  cargoLock = "${inputs.codex}/codex-rs/Cargo.lock";

  cargoVendorDir = craneLib.vendorCargoDeps {
    inherit cargoLock;
    overrideVendorGitCheckout =
      ps: drv:
      if (lib.any (p: p.name == "crossterm") ps) then
        drv.overrideAttrs (old: {
          postUnpack = (old.postUnpack or "") + ''
            mkdir -p $sourceRoot/examples/interactive-demo
            touch $sourceRoot/examples/interactive-demo/README.md
          '';
        })
      else if (lib.any (p: p.name == "runfiles") ps) then
        drv.overrideAttrs (old: {
          postUnpack = (old.postUnpack or "") + ''
            find $sourceRoot -name Cargo.toml -print0 | while IFS= read -r -d "" toml; do
              dir=$(dirname "$toml")

              while IFS= read -r line; do
                val=$(echo "$line" | ${gnused}/bin/sed -n 's/^.*readme\s*=\s*"\(.*\)".*/\1/p')
                if [ -n "$val" ] && [ ! -f "$dir/$val" ]; then
                  ${gnused}/bin/sed -i "\\|$line|d" "$toml"
                fi
              done < <(${gnugrep}/bin/grep -i '^\s*\(package\.\)\?readme\s*=' "$toml" || true)

              while IFS= read -r line; do
                val=$(echo "$line" | ${gnused}/bin/sed -n 's/^.*license-file\s*=\s*"\(.*\)".*/\1/p')
                if [ -n "$val" ] && [ ! -f "$dir/$val" ]; then
                  ${gnused}/bin/sed -i "\\|$line|d" "$toml"
                fi
              done < <(${gnugrep}/bin/grep -i '^\s*\(package\.\)\?license-file\s*=' "$toml" || true)

              if ! grep -q '^\[workspace\]' "$toml" 2>/dev/null; then
                case "$toml" in
                  */testdata/*)
                    printf '\n[workspace]\n' >> "$toml"
                    ;;
                esac
              fi
            done
          '';
        })
      else
        drv;
  };

  commonArgs = {
    inherit
      src
      version
      cargoLock
      cargoVendorDir
      ;
    pname = "codex";
    strictDeps = true;
    cargoExtraArgs = "--offline";

    nativeBuildInputs = [
      clang
      cmake
      gitMinimal
      installShellFiles
      makeBinaryWrapper
      pkg-config
      ninja
      go
      perl
    ];

    buildInputs = [
      libclang
      openssl
    ]
    ++ lib.optionals stdenv.hostPlatform.isDarwin [ apple-sdk_15 ];

    LIBCLANG_PATH = "${lib.getLib libclang}/lib";
    NIX_CFLAGS_COMPILE = toString (
      lib.optionals stdenv.cc.isClang [
        "-Wno-error=character-conversion"
      ]
    );
  };

  cargoArtifacts = craneLib.buildDepsOnly commonArgs;
in
craneLib.buildPackage (
  commonArgs
  // {
    inherit cargoArtifacts;
    doCheck = false;

    postInstall = ''
      installShellCompletion --cmd codex \
        --bash <($out/bin/codex completion bash) \
        --fish <($out/bin/codex completion fish) \
        --zsh <($out/bin/codex completion zsh)
    '';

    postFixup = ''
      wrapProgram $out/bin/codex --prefix PATH : ${lib.makeBinPath [ ripgrep ]}
    '';

    meta = {
      description = "Lightweight coding agent that runs in your terminal";
      homepage = "https://github.com/openai/codex";
      license = lib.licenses.asl20;
      mainProgram = "codex";
      platforms = lib.platforms.unix;
    };
  }
)
