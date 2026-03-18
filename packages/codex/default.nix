{
  pkgs,
  inputs,
  outputs,
  lib,
  installShellFiles,
  makeBinaryWrapper,
  symlinkJoin,
  runCommand,
  ripgrep,
  python3,
  crate2nixSourceOnly ? false,
  ...
}:
let
  slib = outputs.lib;
  version = slib.getFlakeVersion "codex";
  src = "${inputs.codex}/codex-rs";
  pythonForSourcePrep = python3.withPackages (ps: [ ps.tomlkit ]);

  patchedSrc =
    runCommand "codex-${version}-src"
      {
        nativeBuildInputs = [ pythonForSourcePrep ];
      }
      ''
        cp -r ${src} "$out"
        chmod -R u+w "$out"
        cp ${src}/node-version.txt "$out/node-version.txt"
        cp ${src}/node-version.txt "$out/core/node-version.txt"
        substituteInPlace "$out/core/src/tools/js_repl/mod.rs" \
          --replace-fail '../../../../node-version.txt' '../../../node-version.txt'

        ${pythonForSourcePrep}/bin/python3 - <<'PY'
        import os
        from pathlib import Path
        import tomlkit

        lock_file = Path(os.environ["out"]) / "Cargo.lock"
        lock_doc = tomlkit.parse(lock_file.read_text())

        for package in lock_doc.get("package", []):
            if package.get("version") == "0.0.0" and "source" not in package:
                package["version"] = "${version}"

        lock_file.write_text(tomlkit.dumps(lock_doc))
        PY
      '';

  cargoNix = import ./Cargo.nix {
    inherit pkgs;
    rootSrc = patchedSrc;
  };
  cargoNixVersion = cargoNix.internal.crates."codex-cli".version;
  cargoNixVersionCheck =
    if cargoNixVersion == version then
      true
    else
      throw ''
        packages/codex/Cargo.nix has codex-cli version ${cargoNixVersion},
        expected ${version}; regenerate Cargo.nix
      '';

  crosstermOverride = attrs: {
    postUnpack = (attrs.postUnpack or "") + ''
      mkdir -p "$sourceRoot/examples/interactive-demo"
      touch "$sourceRoot/examples/interactive-demo/README.md"
    '';
  };

  rmcpOverride = attrs: {
    CARGO_CRATE_NAME = attrs.crateName or "rmcp";
    CARGO_PKG_VERSION = attrs.version or "0.15.0";
  };

  runfilesOverride = attrs: {
    src = "${attrs.src}/rust/runfiles";
  };

  crateOverrides = pkgs.defaultCrateOverrides // {
    crossterm = crosstermOverride;
    rmcp = rmcpOverride;
    runfiles = runfilesOverride;
  };

  codexDrv = cargoNix.workspaceMembers.codex-cli.build.override {
    inherit crateOverrides;
    runTests = false;
  };
  codexDrvChecked = codexDrv.overrideAttrs (old: {
    doInstallCheck = true;
    installCheckPhase = (old.installCheckPhase or "") + ''
      runHook preInstallCheck

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

      $out/bin/codex --version
      $out/bin/codex --help >/dev/null

      runHook postInstallCheck
    '';
  });
  guardedCodexDrv =
    assert cargoNixVersionCheck;
    codexDrvChecked;
in
if crate2nixSourceOnly then
  patchedSrc
else
  symlinkJoin {
    name = "codex-${version}";
    paths = [ guardedCodexDrv ];
    nativeBuildInputs = [
      installShellFiles
      makeBinaryWrapper
    ];

    postBuild = ''
      installShellCompletion --cmd codex \
        --bash <($out/bin/codex completion bash) \
        --fish <($out/bin/codex completion fish) \
        --zsh <($out/bin/codex completion zsh)

      wrapProgram "$out/bin/codex" --prefix PATH : ${lib.makeBinPath [ ripgrep ]}
    '';

    passthru = {
      inherit cargoNix crateOverrides patchedSrc;
      codexDrv = guardedCodexDrv;
    };

    meta = {
      description = "Lightweight coding agent that runs in your terminal";
      homepage = "https://github.com/openai/codex";
      license = lib.licenses.asl20;
      mainProgram = "codex";
      platforms = lib.platforms.unix;
    };
  }
