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
    nativeCheckInputs = (old.nativeCheckInputs or [ ]) ++ [
      python3
      bashInteractive
      procps
    ];
    patches = (old.patches or [ ]) ++ [
      ./darwin-provenance.patch
      ./darwin-native-test-tools.patch
    ];
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
