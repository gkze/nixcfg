{
  inputs,
  final,
  prev,
  slib,
  ...
}:
let
  inherit (final) craneLib;
  version = slib.getFlakeVersion "codex";
  src = "${inputs.codex}/codex-rs";
  cargoLock = "${inputs.codex}/codex-rs/Cargo.lock";

  # Newer Cargo versions are stricter about workspace membership and missing
  # files referenced in Cargo.toml. Two git dependencies in codex's lock file
  # have issues that break crane's vendoring (which runs `cargo package -l`):
  #
  # 1. nornagon/crossterm: examples/interactive-demo declares readme = "README.md"
  #    but the file doesn't exist.
  # 2. dzbarsky/rules_rust: testdata crates (empty, standalone) are detected as
  #    workspace members but aren't listed in workspace.members; subcrate
  #    references ../README.md which doesn't exist.
  cargoVendorDir = craneLib.vendorCargoDeps {
    inherit cargoLock;
    overrideVendorGitCheckout =
      ps: drv:
      if (prev.lib.any (p: p.name == "crossterm") ps) then
        drv.overrideAttrs (old: {
          postUnpack = (old.postUnpack or "") + ''
            mkdir -p $sourceRoot/examples/interactive-demo
            touch $sourceRoot/examples/interactive-demo/README.md
          '';
        })
      else if (prev.lib.any (p: p.name == "runfiles") ps) then
        drv.overrideAttrs (old: {
          postUnpack = (old.postUnpack or "") + ''
            # The rules_rust repo is full of test fixture Cargo.toml files that
            # reference non-existent readme/license files and have workspace
            # membership issues. Newer Cargo treats these as fatal errors during
            # `cargo package -l`. We only need the "runfiles" crate, so broadly
            # fix all Cargo.toml files in the checkout:

            # 1. Strip readme and license-file references that point to
            #    non-existent files. We check each Cargo.toml individually.
            find $sourceRoot -name Cargo.toml -print0 | while IFS= read -r -d "" toml; do
              dir=$(dirname "$toml")

              # Remove readme lines pointing to missing files
              while IFS= read -r line; do
                val=$(echo "$line" | ${prev.gnused}/bin/sed -n 's/^.*readme\s*=\s*"\(.*\)".*/\1/p')
                if [ -n "$val" ] && [ ! -f "$dir/$val" ]; then
                  ${prev.gnused}/bin/sed -i "\\|$line|d" "$toml"
                fi
              done < <(${prev.gnugrep}/bin/grep -i '^\s*\(package\.\)\?readme\s*=' "$toml" || true)

              # Remove license-file lines pointing to missing files
              while IFS= read -r line; do
                val=$(echo "$line" | ${prev.gnused}/bin/sed -n 's/^.*license-file\s*=\s*"\(.*\)".*/\1/p')
                if [ -n "$val" ] && [ ! -f "$dir/$val" ]; then
                  ${prev.gnused}/bin/sed -i "\\|$line|d" "$toml"
                fi
              done < <(${prev.gnugrep}/bin/grep -i '^\s*\(package\.\)\?license-file\s*=' "$toml" || true)

              # 2. Add [workspace] to crates that cargo thinks are in a
              #    workspace but aren't listed as members
              if ! grep -q '^\[workspace\]' "$toml" 2>/dev/null; then
                # Check if there's a workspace Cargo.toml above that doesn't
                # include this crate. Only add [workspace] to leaf test crates.
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
    # Use --offline instead of --locked to avoid checksum mismatch errors
    # for git dependencies (runfiles from rules_rust)
    cargoExtraArgs = "--offline";

    nativeBuildInputs = [
      prev.clang
      prev.cmake
      prev.gitMinimal
      prev.installShellFiles
      prev.makeBinaryWrapper
      prev.pkg-config
      prev.ninja
      prev.go
      prev.perl
    ];

    buildInputs = [
      prev.libclang
      prev.openssl
    ]
    ++ prev.lib.optionals prev.stdenv.hostPlatform.isDarwin [ prev.apple-sdk_15 ];

    LIBCLANG_PATH = "${prev.lib.getLib prev.libclang}/lib";
    NIX_CFLAGS_COMPILE = toString (
      prev.lib.optionals prev.stdenv.cc.isClang [
        "-Wno-error=character-conversion"
      ]
    );
  };
  cargoArtifacts = craneLib.buildDepsOnly commonArgs;
in
{
  codex = craneLib.buildPackage (
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
        wrapProgram $out/bin/codex --prefix PATH : ${prev.lib.makeBinPath [ prev.ripgrep ]}
      '';

      meta = with prev.lib; {
        description = "Lightweight coding agent that runs in your terminal";
        homepage = "https://github.com/openai/codex";
        license = licenses.asl20;
        mainProgram = "codex";
        platforms = platforms.unix;
      };
    }
  );
}
