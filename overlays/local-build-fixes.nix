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
      if [[ -d "$cargoDepsCopy/metrics-0.24.1" ]]; then
        metrics_dirs=("$cargoDepsCopy/metrics-0.24.1")
      else
        shopt -s nullglob
        shopt -s globstar
        metrics_dirs=("$cargoDepsCopy"/**/metrics-0.24.1)
      fi

      if [[ "''${#metrics_dirs[@]}" -eq 0 ]]; then
        echo >&2 "ERROR: '$cargoDepsCopy/**/metrics-0.24.1' not found"
        false
      elif [[ "''${#metrics_dirs[@]}" -gt 1 ]]; then
        echo >&2 "ERROR: multiple metrics-0.24.1 directories found under '$cargoDepsCopy':"
        printf '  %s\n' "''${metrics_dirs[@]}" >&2
        false
      fi

      patch -d "''${metrics_dirs[0]}" -p1 < ${./mountpoint-s3/metrics-recorder-lifetime.patch}
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
