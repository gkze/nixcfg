{
  lib,
  mkGoCli,
  bashInteractive,
  python3,
  procps,
  stdenv,
  ...
}:
(mkGoCli {
  pname = "openai-cli";
  cmdName = "openai";
  completionCommand = "@completion";
  description = "The official CLI for the OpenAI API";
  homepage = "https://github.com/openai/openai-cli";
  license = lib.licenses.asl20;
}).overrideAttrs
  (old: {
    # These tools support the native PTY and shell completion tests.
    nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
      python3
    ];
    nativeCheckInputs = (old.nativeCheckInputs or [ ]) ++ [
      python3
      bashInteractive
      procps
    ];
    # `cmd/openai` is one mostly serial test binary that also compiles the
    # CLI for its shell tests. Healthy hosted macos-15 builders needed 552s
    # for 1.51.5 and 636-747s for 1.52.0, so Go's implicit 10m per-binary
    # limit killed 1.52.0 in Update 43110633449. Keep an explicit hang bound.
    checkFlags = (old.checkFlags or [ ]) ++ [ "-timeout=30m" ];
    patches = (old.patches or [ ]) ++ [
      ./darwin-provenance.patch
      ./darwin-native-test-tools.patch
    ];
    # Darwin can deliver child exit in the same turn as the last PTY write.
    # The list waiter must read that write before exit means the marker is
    # missing. fetch_latest rejects a harness layout this rewrite does not own.
    postPatch = (old.postPatch or "") + ''
      PYTHONPATH=${
        import ../../lib/codemods-pythonpath.nix { inherit lib; }
      } ${lib.getExe python3} ${./patch_pty_wait.py} "$PWD"
    '';
    # The Darwin sandbox strips set-id bits from upstream mode fixtures.
    # Keep full CLI tests and the sandbox-safe native metadata/ACL checks here.
    # The remaining TestPicker cases need an ordinary APFS host and no automated
    # gate runs them, so they must be executed by hand before promotion.
    postCheck =
      (old.postCheck or "")
      + (
        if stdenv.hostPlatform.isDarwin then
          ''
            go test -p "$NIX_BUILD_CORES" -v ./internal/autocomplete -run '^TestPicker(Darwin.*|InstallPreservesDarwinFileMetadata|InstallRejectsInheritedScriptACL|InstallRejectsWritableScriptDirectoryACL|InstallAllowsBenignProfileParentACL|InstallRejectsWritableDirectoryACL)$'
          ''
        else
          ''
            go test -p "$NIX_BUILD_CORES" -v ./internal/autocomplete
          ''
      );
  })
