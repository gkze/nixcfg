{
  prev,
  slib,
  sources,
  ...
}:
let
  inherit (sources.goose-cli) version;

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
  clangResourceVersion = "22";

  rustyV8Src = prev.fetchgit {
    url = "https://github.com/jh-block/rusty_v8.git";
    rev = v8Source.version;
    fetchSubmodules = true;
    inherit (v8HashEntry) hash;
  };

  chromiumToolchainBundle = prev.runCommand "goose-cli-v8-toolchain-${v8Source.version}" { } ''
    rust_toolchain=$out/third_party/rust-toolchain
    llvm_bundle=$out/third_party/llvm-build/Release+Asserts
    llvm_bin=$llvm_bundle/bin

    mkdir -p $rust_toolchain
    printf '%s\n' '${prev.rustc.version}' > $rust_toolchain/VERSION

    mkdir -p $rust_toolchain/bin
    ln -sf ${prev.rust-bindgen}/bin/bindgen $rust_toolchain/bin/bindgen
    ln -sf ${prev.rustc}/bin/rustc $rust_toolchain/bin/rustc
    ln -sf ${prev.cargo}/bin/cargo $rust_toolchain/bin/cargo
    ln -sf ${prev.rustfmt}/bin/rustfmt $rust_toolchain/bin/rustfmt

    mkdir -p $rust_toolchain/lib/rustlib/src/rust
    ln -sf ${prev.rustPlatform.rustLibSrc} \
      $rust_toolchain/lib/rustlib/src/rust/library

    mkdir -p $llvm_bin
    ln -sf ${prev.llvmPackages.clang}/bin/clang $llvm_bin/clang
    ln -sf ${prev.llvmPackages.clang}/bin/clang++ $llvm_bin/clang++
    for tool in \
      llvm-ar \
      llvm-cxxfilt \
      llvm-install-name-tool \
      llvm-libtool-darwin \
      llvm-nm \
      llvm-objcopy \
      llvm-objdump \
      llvm-strip
    do
      ln -sf ${prev.llvmPackages.llvm}/bin/$tool $llvm_bin/$tool
    done

    mkdir -p $llvm_bundle/lib/clang
    ln -sf ${prev.llvmPackages.clang}/resource-root \
      $llvm_bundle/lib/clang/${clangResourceVersion}
  '';

  patchedV8Src =
    prev.runCommand "goose-cli-v8-${v8Source.version}-src"
      {
        nativeBuildInputs = [
          prev.patch
          prev.python3
        ];
      }
      ''
        cp -r ${rustyV8Src} $out
        chmod -R u+w $out

        patch -d $out -p1 < ${./rusty-v8-nix.patch}

        python ${./patch_allocator_build.py} \
          $out/build/rust/allocator/BUILD.gn
        python ${./patch_whole_archive.py} \
          $out/build/toolchain/whole_archive.py

        # The Rust host-build-tools toolchain (proc-macros, build scripts,
        # bytecode generators) has its own toolchain_args block, so patch it
        # explicitly to stay off lld and fatal linker warnings as well.
        python ${./patch_apple_toolchain_host_build_tools.py} \
          $out/build/toolchain/apple/toolchain.gni

        mkdir -p $out/third_party
        rm -rf $out/third_party/rust-toolchain $out/third_party/llvm-build
        ln -s ${chromiumToolchainBundle}/third_party/rust-toolchain \
          $out/third_party/rust-toolchain
        ln -s ${chromiumToolchainBundle}/third_party/llvm-build \
          $out/third_party/llvm-build
      '';

  # Hand-maintained source surgery. crate2nix handles the Rust dependency graph,
  # but Goose still needs a custom V8 source tree, lockfile rewrite, and a few
  # build-file tweaks before the workspace is buildable in Nix.
  patchedSrc =
    prev.runCommand "goose-cli-${version}-src"
      {
        nativeBuildInputs = [
          prev.patch
          prev.python3
        ];
      }
      ''
        cp -r ${upstreamSrc} $out
        chmod -R u+w $out

        mkdir -p $out/vendor
        cp -r ${patchedV8Src} $out/vendor/v8-goose-src
        chmod -R u+w $out/vendor/v8-goose-src

        patch -d $out -p1 < ${./goose-workspace-nix.patch}

        # Goose's embedded web UI reaches out to workspace-level documentation
        # assets via include_bytes!(../../../../documentation/...). That path
        # escapes crate2nix/buildRustCrate's per-crate source root, so vendor
        # the two referenced logos into the crate-local static tree and point
        # the source at those local copies.
        mkdir -p $out/crates/goose-cli/static/img
        cp $out/documentation/static/img/logo_dark.png \
          $out/crates/goose-cli/static/img/logo_dark.png
        cp $out/documentation/static/img/logo_light.png \
          $out/crates/goose-cli/static/img/logo_light.png

        python - <<PY
        from pathlib import Path
        import tomllib

        root = Path("$out")
        v8_manifest = tomllib.loads((root / "vendor/v8-goose-src/Cargo.toml").read_text())
        v8_version = v8_manifest["package"]["version"]
        lock_file = root / "Cargo.lock"
        sections = lock_file.read_text().split("[[package]]\n")
        updated = False
        patched = [sections[0]]
        for section in sections[1:]:
            lines = section.splitlines()
            if lines and lines[0] == 'name = "v8-goose"':
                next_lines = []
                for line in lines:
                    if line.startswith("version = "):
                        next_lines.append(f'version = "{v8_version}"')
                    elif line.startswith("source = ") or line.startswith("checksum = "):
                        continue
                    else:
                        next_lines.append(line)
                section = "\n".join(next_lines)
                updated = True
            patched.append("[[package]]\n" + section)
        if not updated:
            raise SystemExit("expected v8-goose Cargo.lock entry not found")
        lock_file.write_text("".join(patched))
        PY

        cp ${./crate-hashes.json} $out/crate-hashes.json
      '';

  # Generated by crate2nix and post-processed so the checked-in file can point
  # at a separate patched source tree via rootSrc. See README.md for regen flow.
  cargoNix = import ./Cargo.nix {
    pkgs = prev;
    rootSrc = patchedSrc;
  };

  # Common inputs shared by the Goose workspace binaries themselves.
  commonGooseOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [
      prev.pkg-config
      prev.protobuf
    ];

    buildInputs =
      (attrs.buildInputs or [ ])
      ++ [
        prev.dbus
        prev.openssl
      ]
      ++ prev.lib.optionals prev.stdenv.hostPlatform.isLinux [ prev.libxcb ];

    LIBCLANG_PATH = "${prev.lib.getLib prev.llvmPackages.libclang}/lib";
  };

  # xcap only needs the extra X11 bits on Linux.
  xcapLinuxOverride =
    attrs:
    prev.lib.optionalAttrs prev.stdenv.hostPlatform.isLinux {
      buildInputs = (attrs.buildInputs or [ ]) ++ [ prev.libxcb ];
    };

  # llama-cpp-sys-2 expects Cargo-style env plus native CMake tooling that
  # buildRustCrate/crate2nix do not provide out of the box.
  llamaCppSysOverride = attrs: {
    nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [
      prev.cmake
      prev.pkg-config
    ];

    # buildRustCrate doesn't currently export CARGO_CFG_TARGET_FEATURE even
    # though Cargo build scripts normally see it. llama-cpp-sys uses that env
    # var only to opt into extra x86 GGML toggles, so an empty string is a safe
    # fallback on our darwin/aarch64 builds.
    CARGO_CFG_TARGET_FEATURE = "";
    LIBCLANG_PATH = "${prev.lib.getLib prev.llvmPackages.libclang}/lib";
  };

  # hipstr 0.6.0's serde path assumes serde_bytes is available; patch the
  # transitive crate instead of carrying a Goose-specific workaround.
  hipstrOverride = attrs: {
    patches = (attrs.patches or [ ]) ++ [ ./hipstr-no-serde-bytes.patch ];
  };

  # rmcp 0.12.0 uses env! for Cargo package vars during compilation, but
  # buildRustCrate does not export those automatically.
  rmcpOverride = attrs: {
    CARGO_CRATE_NAME = attrs.crateName or "rmcp";
    CARGO_PKG_VERSION = attrs.version or "0.12.0";
  };

  # V8 is the most bespoke part of the package: source build via GN/Ninja,
  # darwin SDK knobs, bindgen/libclang wiring, and a suppressed cc-wrapper
  # target-triple warning while we keep using the known-good wrapped clang.
  v8GooseOverride =
    attrs:
    {
      src = patchedV8Src;
    }
    // prev.lib.optionalAttrs prev.stdenv.hostPlatform.isDarwin {

      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [
        prev.cmake
        prev.curl
        prev.gn
        prev.ninja
        prev.python3
        prev.xcodebuild
      ];

      CLANG_BASE_PATH = "${patchedV8Src}/third_party/llvm-build/Release+Asserts";
      GN_ARGS = ''
        mac_sdk_min="14.4"
        mac_deployment_target="14.0"
        mac_min_system_version="14.0"
        rust_bindgen_root="//third_party/rust-toolchain"
        rust_sysroot_absolute="${prev.rustc}"
        rustc_version="${prev.rustc.version}"
        # Apple's linker warns about V8's simulator probe trampolines on arm64;
        # keep the warnings visible but do not fail the build on them.
        fatal_linker_warnings=false
        use_lld=false
      '';
      GN = "${prev.gn}/bin/gn";
      LIBCLANG_PATH = "${prev.lib.getLib prev.llvmPackages.libclang}/lib";
      NINJA = "${prev.ninja}/bin/ninja";
      NIX_CC_WRAPPER_SUPPRESS_TARGET_WARNING = "1";
      PYTHON = "${prev.python3}/bin/python3";
      RUSTC_BOOTSTRAP = "1";
      V8_FROM_SOURCE = "1";
    };

  # Keep all build-system compatibility shims in one place so version bumps can
  # be audited crate-by-crate.
  crateOverrides = prev.defaultCrateOverrides // {
    goose-cli = commonGooseOverride;
    goose-server = commonGooseOverride;
    hipstr = hipstrOverride;
    llama-cpp-sys-2 = llamaCppSysOverride;
    rmcp = rmcpOverride;
    v8-goose = v8GooseOverride;
    xcap = xcapLinuxOverride;
  };

  # Build the two workspace binaries separately, then rejoin them below to keep
  # the historical goose-cli package shape.
  gooseCliDrv = cargoNix.workspaceMembers.goose-cli.build.override {
    inherit crateOverrides;
    runTests = false;
  };

  gooseCliDrvChecked = gooseCliDrv.overrideAttrs (old: {
    doInstallCheck = true;
    nativeInstallCheckInputs = (old.nativeInstallCheckInputs or [ ]) ++ [ prev.coreutils ];
    installCheckPhase = ''
      runHook preInstallCheck

      export HOME="$(${prev.coreutils}/bin/mktemp -d)"
      export XDG_CACHE_HOME="$(${prev.coreutils}/bin/mktemp -d)"
      export XDG_CONFIG_HOME="$(${prev.coreutils}/bin/mktemp -d)"
      export XDG_DATA_HOME="$(${prev.coreutils}/bin/mktemp -d)"
      export XDG_STATE_HOME="$(${prev.coreutils}/bin/mktemp -d)"

      $out/bin/goose --version
      $out/bin/goose info

      runHook postInstallCheck
    '';
  });

  gooseServerDrv = cargoNix.workspaceMembers.goose-server.build.override {
    inherit crateOverrides;
    runTests = false;
  };

  workspaceBins = prev.symlinkJoin {
    name = "goose-cli-workspace-${version}";
    paths = [
      gooseCliDrvChecked
      gooseServerDrv
    ];
  };
in
{
  goose-cli = prev.symlinkJoin {
    name = "goose-cli-${version}";
    paths = [ workspaceBins ];
    nativeBuildInputs = [ prev.installShellFiles ];

    postBuild = ''
      rm -f $out/bin/generate_manpages $out/bin/generate_schema

      installShellCompletion --cmd goose \
        --bash <($out/bin/goose completion bash) \
        --fish <($out/bin/goose completion fish) \
        --zsh <($out/bin/goose completion zsh)
    '';

    passthru = {
      inherit
        cargoNix
        crateOverrides
        gooseServerDrv
        patchedSrc
        patchedV8Src
        chromiumToolchainBundle
        ;
      gooseCliDrv = gooseCliDrvChecked;
    };

    meta = {
      description = "Open-source, extensible AI agent that goes beyond code suggestions - install, execute, edit, and test with any LLM";
      homepage = "https://github.com/block/goose";
      license = prev.lib.licenses.asl20;
      mainProgram = "goose";
      platforms = prev.lib.platforms.linux ++ prev.lib.platforms.darwin;
    };
  };
}
