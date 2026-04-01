{
  prev,
  slib,
  sources,
  selfSource,
  ...
}:
let
  inherit (selfSource) version;

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
  v8ManifestVersion =
    (builtins.fromTOML (builtins.readFile "${rustyV8Src}/Cargo.toml")).package.version;
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

        python - <<PY
        from pathlib import Path

        path = Path("$out/build.rs")
        text = path.read_text()

        old_envs = '    "RUSTY_V8_SRC_BINDING_PATH",\n'
        new_envs = '    "RUSTY_V8_SRC_BINDING_PATH",\n    "RUSTY_V8_PREBUILT_GN_OUT",\n'
        if old_envs not in text:
            raise SystemExit("expected RUSTY_V8_SRC_BINDING_PATH env list entry not found")
        text = text.replace(old_envs, new_envs, 1)

        old_prebuilt = '  print_prebuilt_src_binding_path();\n\n  download_static_lib_binaries();\n'
        new_prebuilt = """  if let Ok(prebuilt_gn_out) = env::var(\"RUSTY_V8_PREBUILT_GN_OUT\") {\n    let prebuilt_gn_out = PathBuf::from(prebuilt_gn_out);\n    let local_gn_out = build_dir().join(\"gn_out\");\n    fs::create_dir_all(&local_gn_out).unwrap();\n    fs::copy(prebuilt_gn_out.join(\"project.json\"), local_gn_out.join(\"project.json\")).unwrap();\n    if let Ok(args_gn) = fs::read(prebuilt_gn_out.join(\"args.gn\")) {\n      fs::write(local_gn_out.join(\"args.gn\"), args_gn).unwrap();\n    }\n    build_binding();\n  } else {\n    print_prebuilt_src_binding_path();\n  }\n\n  download_static_lib_binaries();\n"""
        if old_prebuilt not in text:
            raise SystemExit("expected prebuilt V8 branch not found")
        text = text.replace(old_prebuilt, new_prebuilt, 1)

        path.write_text(text)
        PY

        mkdir -p $out/third_party
        rm -rf $out/third_party/rust-toolchain $out/third_party/llvm-build
        ln -s ${chromiumToolchainBundle}/third_party/rust-toolchain \
          $out/third_party/rust-toolchain
        ln -s ${chromiumToolchainBundle}/third_party/llvm-build \
          $out/third_party/llvm-build
      '';

  v8GnArgs = ''
    is_debug=false
    use_custom_libcxx=true
    v8_enable_sandbox=false
    v8_enable_pointer_compression=false
    v8_enable_v8_checks=false
    host_cpu="arm64"
    target_cpu="arm64"
    mac_sdk_min="14.4"
    mac_deployment_target="14.0"
    mac_min_system_version="14.0"
    rust_bindgen_root="//third_party/rust-toolchain"
    rust_sysroot_absolute="${prev.rustc}"
    rustc_version="${prev.rustc.version}"
    treat_warnings_as_errors=false
    fatal_linker_warnings=false
    use_lld=false
    clang_base_path="${patchedV8Src}/third_party/llvm-build/Release+Asserts"
  '';

  v8NativeDrv =
    if prev.stdenv.hostPlatform.isDarwin then
      prev.stdenv.mkDerivation {
        name = "goose-cli-v8-native-${v8Source.version}";
        src = patchedV8Src;
        nativeBuildInputs = [
          prev.gn
          prev.ninja
          prev.python3
          prev.xcodebuild
        ];

        dontConfigure = true;
        dontFixup = true;

        env = {
          CLANG_BASE_PATH = "${patchedV8Src}/third_party/llvm-build/Release+Asserts";
          GN = "${prev.gn}/bin/gn";
          GN_ARGS = v8GnArgs;
          NINJA = "${prev.ninja}/bin/ninja";
          NIX_CC_WRAPPER_SUPPRESS_TARGET_WARNING = "1";
          PYTHONDONTWRITEBYTECODE = "1";
          PYTHON = "${prev.python3}/bin/python3";
          RUSTC_BOOTSTRAP = "1";
        };

        buildPhase = ''
          runHook preBuild

          export HOME="$TMPDIR/home"
          mkdir -p "$HOME"
          export DEPOT_TOOLS_WIN_TOOLCHAIN=0

          gn_out="$TMPDIR/gn-out"

          "$GN" --root="$PWD" --script-executable="$PYTHON" gen "$gn_out" \
            --ide=json --args="$GN_ARGS"

          "$NINJA" -C "$gn_out" rusty_v8

          mkdir -p "$out/lib" "$out/share/gn_out"
          cp "$gn_out/obj/librusty_v8.a" "$out/lib/"
          cp "$gn_out/project.json" "$out/share/gn_out/project.json"
          cp "$gn_out/args.gn" "$out/share/gn_out/args.gn"

          runHook postBuild
        '';

        installPhase = ''
          runHook preInstall
          runHook postInstall
        '';
      }
    else
      null;

  cargoNixFn = import ./Cargo.nix;
  cargoNixGooseVersion = (cargoNixFn { pkgs = prev; }).internal.crates."goose-cli".version;
  cargoNixV8Version = (cargoNixFn { pkgs = prev; }).internal.crates."v8-goose".version;
  cargoNixVersionCheck =
    if cargoNixGooseVersion == version then
      true
    else
      throw ''
        overlays/goose-cli/Cargo.nix has goose-cli version ${cargoNixGooseVersion},
        expected ${version}; regenerate Cargo.nix
      '';
  cargoNixV8VersionCheck =
    if cargoNixV8Version == v8ManifestVersion then
      true
    else
      throw ''
        overlays/goose-cli/Cargo.nix has v8-goose version ${cargoNixV8Version},
        expected ${v8ManifestVersion}; regenerate Cargo.nix
      '';

  # Hand-maintained source surgery. crate2nix handles the Rust dependency graph,
  # but Goose still needs a custom V8 source tree, lockfile rewrite, and a few
  # build-file tweaks before the workspace is buildable in Nix.
  patchedSrc =
    prev.runCommand "goose-cli-${version}-src"
      {
        nativeBuildInputs = [
          prev.python3
        ];
      }
      ''
        cp -r ${upstreamSrc} $out
        chmod -R u+w $out

        mkdir -p $out/vendor
        cp -r ${patchedV8Src} $out/vendor/v8-goose-src
        chmod -R u+w $out/vendor/v8-goose-src

        python - <<PY
        import re
        import shutil
        from pathlib import Path
        import tomllib

        def drop_top_level_sections(text: str, headers: set[str]) -> str:
            prefixes = tuple(
                header[:-1] + "."
                for header in headers
                if header.startswith("[")
                and header.endswith("]")
                and not header.startswith("[[")
            )

            def should_remove(header: str) -> bool:
                return header in headers or any(header.startswith(prefix) for prefix in prefixes)

            lines = text.splitlines(keepends=True)
            kept = []
            removing = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    removing = should_remove(stripped)
                    if removing:
                        continue
                if not removing:
                    kept.append(line)
            return "".join(kept)

        root = Path("$out")
        goose_cli_src = root / "crates/goose-cli/src"
        logo_rewrites = {
            "../../../../documentation/static/img/logo_dark.png": "../../static/img/logo_dark.png",
            "../../../../documentation/static/img/logo_light.png": "../../static/img/logo_light.png",
        }
        rewrote_logo_paths = False
        if goose_cli_src.exists():
            for path in goose_cli_src.rglob("*.rs"):
                text = path.read_text()
                updated = text
                for old, new in logo_rewrites.items():
                    updated = updated.replace(old, new)
                if updated != text:
                    path.write_text(updated)
                    rewrote_logo_paths = True

        if rewrote_logo_paths:
            static_img_dir = root / "crates/goose-cli/static/img"
            static_img_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / "documentation/static/img/logo_dark.png", static_img_dir / "logo_dark.png")
            shutil.copy2(root / "documentation/static/img/logo_light.png", static_img_dir / "logo_light.png")

        v8_cargo_toml = root / "vendor/v8/Cargo.toml"
        v8_cargo_text = v8_cargo_toml.read_text()
        v8_cargo_text, replacements = re.subn(
            r'^v8-goose\s*=\s*.*$',
            'v8-goose = { path = "../v8-goose-src" }',
            v8_cargo_text,
            count=1,
            flags=re.MULTILINE,
        )
        if replacements != 1:
            raise SystemExit("expected one v8-goose dependency line in vendor/v8/Cargo.toml")
        v8_cargo_toml.write_text(v8_cargo_text)

        v8_goose_cargo_toml = root / "vendor/v8-goose-src/Cargo.toml"
        v8_goose_cargo_text = drop_top_level_sections(
            v8_goose_cargo_toml.read_text(),
            {
                "[workspace]",
                "[profile.dev]",
                "[dev-dependencies]",
                "[[example]]",
                "[[test]]",
                "[[bench]]",
            },
        )
        v8_goose_cargo_toml.write_text(v8_goose_cargo_text)

        v8_manifest = tomllib.loads(v8_goose_cargo_text)
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
  cargoNix = cargoNixFn {
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

  # rmcp uses env! for Cargo package vars during compilation, but
  # buildRustCrate does not export those automatically.
  rmcpOverride =
    attrs:
    assert attrs ? crateName;
    assert attrs ? version;
    {
      CARGO_CRATE_NAME = attrs.crateName;
      CARGO_PKG_VERSION = attrs.version;
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
        prev.python3
        prev.xcodebuild
      ];

      LIBCLANG_PATH = "${prev.lib.getLib prev.llvmPackages.libclang}/lib";
      PYTHON = "${prev.python3}/bin/python3";
      RUSTY_V8_ARCHIVE = "${v8NativeDrv}/lib/librusty_v8.a";
      RUSTY_V8_PREBUILT_GN_OUT = "${v8NativeDrv}/share/gn_out";
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
  goose-cli-crate2nix-src = patchedSrc;
  goose-cli =
    assert cargoNixVersionCheck;
    assert cargoNixV8VersionCheck;
    prev.symlinkJoin {
      name = "goose-cli-${version}";
      paths = [ workspaceBins ];
      nativeBuildInputs = [ prev.installShellFiles ];

      postBuild = ''
        rm -f $out/bin/generate_manpages $out/bin/generate_schema

        export HOME="$TMPDIR/home"
        export XDG_CACHE_HOME="$TMPDIR/xdg-cache"
        export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
        export XDG_DATA_HOME="$TMPDIR/xdg-data"
        export XDG_STATE_HOME="$TMPDIR/xdg-state"
        mkdir -p \
          "$HOME" \
          "$XDG_CACHE_HOME" \
          "$XDG_CONFIG_HOME" \
          "$XDG_DATA_HOME" \
          "$XDG_STATE_HOME"

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
        inherit v8NativeDrv;
      };

      meta = {
        description = "Open-source, extensible AI agent that goes beyond code suggestions - install, execute, edit, and test with any LLM";
        homepage = "https://github.com/block/goose";
        license = prev.lib.licenses.asl20;
        mainProgram = "goose";
        platforms = [ "aarch64-darwin" ];
      };
    };
}
