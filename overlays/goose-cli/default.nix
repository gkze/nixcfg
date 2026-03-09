{
  prev,
  slib,
  sources,
  ...
}:
{
  goose-cli = prev.goose-cli.overrideAttrs (
    old:
    let
      version = slib.stripVersionPrefix sources.goose-cli.version;
      upstreamSrc = prev.fetchFromGitHub {
        owner = "block";
        repo = "goose";
        tag = "v${version}";
        hash = slib.sourceHash "goose-cli" "srcHash";
      };
      v8Source = sources.goose-v8;
      v8HashEntry = builtins.head (
        builtins.filter (entry: entry.hashType == "srcHash") (v8Source.hashes or [ ])
      );
      rustyV8Src = prev.fetchgit {
        url = "https://github.com/jh-block/rusty_v8.git";
        rev = v8Source.version;
        fetchSubmodules = true;
        inherit (v8HashEntry) hash;
      };
      src = prev.runCommand "goose-cli-${version}-src" { } ''
        cp -r ${upstreamSrc} $out
        chmod -R u+w $out

        mkdir -p $out/vendor
        cp -r ${rustyV8Src} $out/vendor/v8-goose-src
        chmod -R u+w $out/vendor/v8-goose-src

        substituteInPlace $out/vendor/v8/Cargo.toml \
          --replace-fail 'v8-goose = { version = "145.0.2" }' \
          'v8-goose = { path = "../v8-goose-src" }'

        substituteInPlace $out/vendor/v8-goose-src/Cargo.toml \
          --replace-fail 'name = "v8"' 'name = "v8-goose"'

        substituteInPlace $out/vendor/v8-goose-src/build.rs \
          --replace-fail '  download_rust_toolchain();' '  // Nix: skip rust toolchain downloader.'

        mkdir -p $out/vendor/v8-goose-src/third_party/rust-toolchain
        printf '%s\n' '${prev.rustc.version}' > \
          $out/vendor/v8-goose-src/third_party/rust-toolchain/VERSION

        mkdir -p $out/vendor/v8-goose-src/third_party/rust-toolchain/bin
        ln -sf ${prev.rust-bindgen}/bin/bindgen \
          $out/vendor/v8-goose-src/third_party/rust-toolchain/bin/bindgen
        ln -sf ${prev.rustc}/bin/rustc \
          $out/vendor/v8-goose-src/third_party/rust-toolchain/bin/rustc
        ln -sf ${prev.cargo}/bin/cargo \
          $out/vendor/v8-goose-src/third_party/rust-toolchain/bin/cargo
        ln -sf ${prev.rustfmt}/bin/rustfmt \
          $out/vendor/v8-goose-src/third_party/rust-toolchain/bin/rustfmt

        mkdir -p $out/vendor/v8-goose-src/third_party/rust-toolchain/lib/rustlib/src/rust
        ln -sf ${prev.rustPlatform.rustLibSrc} \
          $out/vendor/v8-goose-src/third_party/rust-toolchain/lib/rustlib/src/rust/library

        # Build a clang_base_path layout that Chromium GN expects.
        mkdir -p $out/vendor/v8-goose-src/third_party/llvm-build/Release+Asserts
        ln -sf ${prev.llvmPackages.clang}/bin \
          $out/vendor/v8-goose-src/third_party/llvm-build/Release+Asserts/bin
        mkdir -p $out/vendor/v8-goose-src/third_party/llvm-build/Release+Asserts/lib/clang
        ln -sf ${prev.llvmPackages.clang}/resource-root \
          $out/vendor/v8-goose-src/third_party/llvm-build/Release+Asserts/lib/clang/22

        substituteInPlace $out/vendor/v8-goose-src/build/rust/std/BUILD.gn \
          --replace-fail '      stdlib_files += [ "adler" ]' \
          '      stdlib_files += [ "adler2" ]'
      '';
    in
    {
      inherit version src;

      cargoHash = slib.sourceHash "goose-cli" "cargoHash";
      cargoDeps = prev.rustPlatform.fetchCargoVendor {
        inherit src;
        hash = slib.sourceHash "goose-cli" "cargoHash";
      };
    }
    // prev.lib.optionalAttrs prev.stdenv.hostPlatform.isDarwin {
      nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
        prev.cmake
        prev.curl
        prev.gn
        prev.installShellFiles
        prev.ninja
        prev.python3
        prev.xcodebuild
      ];

      checkFlags = (old.checkFlags or [ ]) ++ [
        "--skip=agents::prompt_manager::tests::test_all_platform_extensions"
        "--skip=test_model_list"
      ];

      env = (old.env or { }) // {
        CLANG_BASE_PATH = "${src}/vendor/v8-goose-src/third_party/llvm-build/Release+Asserts";
        GN_ARGS = ''
          mac_sdk_min="14.4"
          mac_deployment_target="14.0"
          mac_min_system_version="14.0"
          rust_bindgen_root="//third_party/rust-toolchain"
          rust_sysroot_absolute="${prev.rustc}"
          rustc_version="${prev.rustc.version}"
          use_lld=false
        '';
        GN = "${prev.gn}/bin/gn";
        NINJA = "${prev.ninja}/bin/ninja";
        PYTHON = "${prev.python3}/bin/python3";
        RUSTC_BOOTSTRAP = "1";
        V8_FROM_SOURCE = "1";
      };

      postInstall = (old.postInstall or "") + ''
        installShellCompletion --cmd goose \
          --bash <($out/bin/goose completion bash) \
          --fish <($out/bin/goose completion fish) \
          --zsh <($out/bin/goose completion zsh)
      '';
    }
  );
}
