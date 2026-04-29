# Shared builder for rusty_v8 (V8 JavaScript engine static library).
#
# Produces a GN/Ninja source build of librusty_v8.a and a crate override
# function that wires the built library into crate2nix builds.
#
# Usage:
#   v8Build = mkRustyV8Build {
#     pkgs = prev;
#     name = "goose-cli-v8";
#     version = "145.0.0";
#     rustyV8Src = fetchgit { ... };
#     extraPatches = [ ./rusty-v8-goose-rename.patch ];
#   };
#
# Returns: { nativeDrv, patchedSrc, chromiumToolchainBundle, mkCrateOverride }
{ lib }:
{
  mkRustyV8PrebuiltArtifacts =
    {
      pkgs,
      name,
      releaseVersion,
      archiveHash,
      bindingHash,
      releaseBaseUrl ? "https://github.com/denoland/rusty_v8/releases/download",
    }:
    let
      version = lib.removePrefix "v" releaseVersion;
      rustTarget = pkgs.stdenv.hostPlatform.rust.rustcTarget;
      archiveName = "librusty_v8_release_${rustTarget}.a.gz";
      bindingName = "src_binding_release_${rustTarget}.rs";
    in
    {
      archive = pkgs.fetchurl {
        name = "${name}-${version}-${rustTarget}-${archiveName}";
        url = "${releaseBaseUrl}/v${version}/${archiveName}";
        hash = archiveHash;
      };
      binding = pkgs.fetchurl {
        name = "${name}-${version}-${rustTarget}-${bindingName}";
        url = "${releaseBaseUrl}/v${version}/${bindingName}";
        hash = bindingHash;
      };
    };

  mkRustyV8Build =
    {
      pkgs,
      name,
      version,
      rustyV8Src,
      extraPatches ? [ ],
      extraPatchCommands ? "",
      gnArgsOverrides ? { },
      clangResourceVersion ? "22",
      prebuiltArtifacts ? null,
    }:
    let
      patchScriptsDir = ./rusty-v8;

      chromiumToolchainBundle = pkgs.runCommand "${name}-toolchain-${version}" { } ''
        rust_toolchain=$out/third_party/rust-toolchain
        llvm_bundle=$out/third_party/llvm-build/Release+Asserts
        llvm_bin=$llvm_bundle/bin

        mkdir -p $rust_toolchain
        printf '%s\n' '${pkgs.rustc.version}' > $rust_toolchain/VERSION

        mkdir -p $rust_toolchain/bin
        ln -sf ${pkgs.rust-bindgen}/bin/bindgen $rust_toolchain/bin/bindgen
        ln -sf ${pkgs.rustc}/bin/rustc $rust_toolchain/bin/rustc
        ln -sf ${pkgs.cargo}/bin/cargo $rust_toolchain/bin/cargo
        ln -sf ${pkgs.rustfmt}/bin/rustfmt $rust_toolchain/bin/rustfmt

        mkdir -p $rust_toolchain/lib/rustlib/src/rust
        ln -sf ${pkgs.rustPlatform.rustLibSrc} \
          $rust_toolchain/lib/rustlib/src/rust/library

        mkdir -p $llvm_bin
        ln -sf ${pkgs.llvmPackages.clang}/bin/clang $llvm_bin/clang
        ln -sf ${pkgs.llvmPackages.clang}/bin/clang++ $llvm_bin/clang++
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
          ln -sf ${pkgs.llvmPackages.llvm}/bin/$tool $llvm_bin/$tool
        done

        mkdir -p $llvm_bundle/lib/clang
        ln -sf ${pkgs.llvmPackages.clang}/resource-root \
          $llvm_bundle/lib/clang/${clangResourceVersion}
      '';

      patchedSrc =
        pkgs.runCommand "${name}-${version}-src"
          {
            nativeBuildInputs = [
              pkgs.patch
              pkgs.python3
            ];
          }
          ''
            cp -r ${rustyV8Src} $out
            chmod -R u+w $out

            patch -d $out -p1 < ${patchScriptsDir}/rusty-v8-nix.patch

            ${lib.concatMapStringsSep "\n" (p: "patch -d $out -p1 < ${p}") extraPatches}

            python ${patchScriptsDir}/patch_allocator_build.py \
              $out/build/rust/allocator/BUILD.gn
            python ${patchScriptsDir}/patch_whole_archive.py \
              $out/build/toolchain/whole_archive.py

            # The Rust host-build-tools toolchain (proc-macros, build scripts,
            # bytecode generators) has its own toolchain_args block, so patch it
            # explicitly to stay off lld and fatal linker warnings as well.
            python ${patchScriptsDir}/patch_apple_toolchain_host_build_tools.py \
              $out/build/toolchain/apple/toolchain.gni

            python ${patchScriptsDir}/patch_build_rs_prebuilt.py \
              $out/build.rs

            ${extraPatchCommands}

            mkdir -p $out/third_party
            rm -rf $out/third_party/rust-toolchain $out/third_party/llvm-build
            ln -s ${chromiumToolchainBundle}/third_party/rust-toolchain \
              $out/third_party/rust-toolchain
            ln -s ${chromiumToolchainBundle}/third_party/llvm-build \
              $out/third_party/llvm-build
          '';

      hostCpu =
        if pkgs.stdenv.hostPlatform.isAarch64 then
          "arm64"
        else if pkgs.stdenv.hostPlatform.isx86_64 then
          "x64"
        else
          throw "rusty-v8: unsupported host architecture";

      defaultGnArgs = {
        is_debug = "false";
        use_custom_libcxx = "true";
        v8_enable_sandbox = "false";
        v8_enable_pointer_compression = "false";
        v8_enable_v8_checks = "false";
        host_cpu = ''"${hostCpu}"'';
        target_cpu = ''"${hostCpu}"'';
        rust_bindgen_root = ''"//third_party/rust-toolchain"'';
        rust_sysroot_absolute = ''"${pkgs.rustc}"'';
        rustc_version = ''"${pkgs.rustc.version}"'';
        treat_warnings_as_errors = "false";
        fatal_linker_warnings = "false";
        use_lld = "false";
        clang_base_path = ''"${patchedSrc}/third_party/llvm-build/Release+Asserts"'';
      };

      darwinGnArgs = {
        mac_sdk_min = ''"14.4"'';
        mac_deployment_target = ''"14.0"'';
        mac_min_system_version = ''"14.0"'';
      };

      mergedGnArgs =
        defaultGnArgs
        // (lib.optionalAttrs pkgs.stdenv.hostPlatform.isDarwin darwinGnArgs)
        // gnArgsOverrides;

      v8GnArgs = lib.concatStringsSep "\n" (lib.mapAttrsToList (k: v: "${k}=${v}") mergedGnArgs);

      nativeDrv =
        if pkgs.stdenv.hostPlatform.isDarwin || pkgs.stdenv.hostPlatform.isLinux then
          pkgs.stdenv.mkDerivation {
            name = "${name}-native-${version}";
            src = patchedSrc;
            nativeBuildInputs = [
              pkgs.gn
              pkgs.ninja
              pkgs.python3
            ]
            ++ lib.optionals pkgs.stdenv.hostPlatform.isDarwin [ pkgs.xcodebuild ];

            dontConfigure = true;
            dontFixup = true;

            env = {
              CLANG_BASE_PATH = "${patchedSrc}/third_party/llvm-build/Release+Asserts";
              GN = "${pkgs.gn}/bin/gn";
              GN_ARGS = v8GnArgs;
              NINJA = "${pkgs.ninja}/bin/ninja";
              NIX_CC_WRAPPER_SUPPRESS_TARGET_WARNING = "1";
              PYTHONDONTWRITEBYTECODE = "1";
              PYTHON = "${pkgs.python3}/bin/python3";
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

      prebuiltCrateOverride =
        if nativeDrv != null then
          { }
        else if prebuiltArtifacts == null then
          throw "rusty-v8: native build unavailable and no prebuiltArtifacts were provided"
        else
          {
            RUSTY_V8_ARCHIVE = "${prebuiltArtifacts.archive}";
            RUSTY_V8_SRC_BINDING_PATH = "${prebuiltArtifacts.binding}";
          };

      mkCrateOverride =
        attrs:
        {
          src = patchedSrc;
        }
        // lib.optionalAttrs (nativeDrv != null) {
          nativeBuildInputs =
            (attrs.nativeBuildInputs or [ ])
            ++ [
              pkgs.python3
            ]
            ++ lib.optionals pkgs.stdenv.hostPlatform.isDarwin [ pkgs.xcodebuild ];

          LIBCLANG_PATH = "${lib.getLib pkgs.llvmPackages.libclang}/lib";
          PYTHON = "${pkgs.python3}/bin/python3";
          RUSTY_V8_ARCHIVE = "${nativeDrv}/lib/librusty_v8.a";
          RUSTY_V8_PREBUILT_GN_OUT = "${nativeDrv}/share/gn_out";
        }
        // prebuiltCrateOverride;
    in
    {
      inherit
        nativeDrv
        patchedSrc
        chromiumToolchainBundle
        mkCrateOverride
        ;
    };
}
