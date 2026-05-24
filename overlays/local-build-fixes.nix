{ prev, ... }:
{
  czkawka =
    if prev.stdenv.hostPlatform.isDarwin then
      prev.czkawka.overrideAttrs (old: {
        buildInputs = builtins.filter (dep: (dep.pname or "") != "wayland") (old.buildInputs or [ ]);
        dontWrapGApps = false;
        postFixup = "";
      })
    else
      prev.czkawka;

  nushell = prev.nushell.overrideAttrs (old: {
    checkPhase =
      let
        extraSkip = "--skip=shell::environment::env::path_is_a_list_in_repl";
      in
      prev.lib.replaceStrings
        [ "--skip=repl::test_config_path::test_default_config_path" ]
        [ "${extraSkip} --skip=repl::test_config_path::test_default_config_path" ]
        old.checkPhase;
  });

  ast-grep = prev.ast-grep.overrideAttrs (old: {
    checkFlags =
      (old.checkFlags or [ ])
      ++ prev.lib.optionals prev.stdenv.hostPlatform.isDarwin [
        "--skip=test_scan_invalid_rule_id"
      ];
  });

  mountpoint-s3 = prev.mountpoint-s3.overrideAttrs (old: {
    buildInputs =
      (old.buildInputs or [ ])
      ++ prev.lib.optionals prev.stdenv.hostPlatform.isDarwin [ prev.macfuse-stubs ]
      ++ prev.lib.optionals prev.stdenv.hostPlatform.isLinux [ prev.fuse3 ];
    doCheck = !prev.stdenv.hostPlatform.isDarwin;
    postPatch = (old.postPatch or "") + ''
      declare -a metrics_dirs=()
      shopt -s nullglob
      shopt -s globstar

      for metrics_dir in "$cargoDepsCopy"/metrics-* "$cargoDepsCopy"/**/metrics-*; do
        if [[ -f "$metrics_dir/src/recorder/mod.rs" ]]; then
          case " ''${metrics_dirs[*]} " in
            *" $metrics_dir "*) ;;
            *) metrics_dirs+=("$metrics_dir") ;;
          esac
        fi
      done

      if [[ "''${#metrics_dirs[@]}" -eq 0 ]]; then
        echo >&2 "ERROR: no vendored metrics crate found under '$cargoDepsCopy'"
        exit 1
      fi

      for metrics_dir in "''${metrics_dirs[@]}"; do
        if grep -Fq "fn new(recorder: &'a (dyn Recorder + 'a)) -> Self" "$metrics_dir/src/recorder/mod.rs"; then
          echo "metrics crate in $metrics_dir already includes recorder lifetime fix"
        elif patch --batch --dry-run -d "$metrics_dir" -p1 < ${./mountpoint-s3/metrics-recorder-lifetime.patch} >/dev/null 2>&1; then
          patch --batch -d "$metrics_dir" -p1 < ${./mountpoint-s3/metrics-recorder-lifetime.patch}
        else
          echo >&2 "ERROR: metrics recorder lifetime patch does not apply to '$metrics_dir'"
          exit 1
        fi
      done
    '';
    meta = old.meta // {
      platforms = prev.lib.platforms.unix;
    };
  });

  # nixpkgs' sequoia-sop currently vendors a duplicate copy of sq's
  # manpages, which creates noisy buildEnv collisions when both are present.
  # TODO: remove once nixpkgs stops installing the duplicated sq docs/man assets
  # from sequoia-sop.
  sequoia-sop = prev.sequoia-sop.overrideAttrs (old: {
    postFixup = (old.postFixup or "") + ''
      if [[ -d "$out/share/man" ]]; then
        find "$out/share/man" -type f \
          ! -path "$out/share/man/man1/sqop*.1.gz" \
          -delete
        find "$out/share/man" -type d -empty -delete
      fi
    '';
  });

  sequoia-wot = prev.sequoia-wot.overrideAttrs (_: {
    doCheck = !prev.stdenv.hostPlatform.isDarwin;
  });
}
