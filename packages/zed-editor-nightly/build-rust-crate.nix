{ lib }:
{
  # crate2nix calls `(buildRustCrateForPkgs pkgs).override { defaultCrateOverrides
  # = ...; }`. `pkgs.buildRustCrate` is a callPackage functor (a set). A bare
  # `attrs:` lambda is not, so evaluation fails with "expected a set but found a
  # function" on every platform (Update #1235 / #208 wrap). Keep .override and
  # apply per-crate policy to each produced derivation.
  wrapBuildRustCrate =
    builder: isDarwin:
    let
      applyDarwinRlibMetadata =
        drv:
        drv.overrideAttrs (
          _old:
          lib.optionalAttrs isDarwin {
            dontStrip = true;
            stripExclude = [ "*.rlib" ];
          }
        );
      linuxZedUnsplit =
        args:
        args
        // lib.optionalAttrs (!isDarwin && (args.crateName or "") == "zed") {
          # crate2nix splits lib+bin crates into outputs out+lib. Linux rustc
          # then records $out inside $lib (rlib dest / rpath / the custom
          # installPhase that only fills $out), and Nix rejects that
          # multi-output cycle (Update validate-x86, rust_zed-1.25.0).
          # extraDerivationAttrs wins over the hardcoded outputs, but
          # outputDev stays [ "lib" ] unless we override it too — otherwise
          # multiple-outputs still assigns outputInclude from empty $lib.
          # Keep Darwin split so already-warmed rust_zed hashes stay put.
          outputs = [ "out" ];
          outputDev = [ "out" ];
        };
      # Update #1258/#1259: Darwin rustc 1.98.1 reads the Cachix
      # language_models rlib (extra-filename f75b2474e2; h3crq11a then
      # x49g8qy1) and logs "register newly loaded library", then
      # intern_stable_crate_id returns CrateError::NotFound — bare E0463
      # at agent_ui buffer_codegen.rs:24. No register-crate cnum line.
      # "resolving crate core" is resolve_crate's missing_core probe.
      # Cargo.nix has no language_models cycle (dependents are agent_ui,
      # eval_cli, edit_prediction_cli, zed). rustc StableCrateId hashes
      # crate name + every -C metadata. Extra metadata forces a Darwin
      # rebuild and a new intern id so the cached rlib cannot be reused.
      # Linux is identity. Do not evict h3crq11a.
      darwinLanguageModelsIntern =
        args:
        args
        // lib.optionalAttrs (isDarwin && (args.crateName or "") == "language_models") {
          extraRustcOpts = (args.extraRustcOpts or [ ]) ++ [
            "-C metadata=nixcfg-221-e0463"
          ];
        };
      wrap = inner: {
        __functor =
          _self: args:
          applyDarwinRlibMetadata (inner (darwinLanguageModelsIntern (linuxZedUnsplit args)));
        override = f: wrap (inner.override f);
      };
    in
    wrap builder;
}
