{
  buildGoModule,
  fetchFromGitHub,
  gh,
  go-toolchain,
  lib,
  outputs,
  selfSource,
  ...
}:
let
  usesGo127Builder = (lib.functionArgs gh.override) ? buildGo127Module;
in
assert lib.versionAtLeast selfSource.version gh.version;
(gh.override (
  args:
  if usesGo127Builder then
    { buildGo127Module = args.buildGo127Module.override { go = go-toolchain; }; }
  else
    { buildGoModule = buildGoModule.override { go = go-toolchain; }; }
)).overrideAttrs
  (
    old:
    {
      inherit (selfSource) version;
      src = fetchFromGitHub {
        owner = "cli";
        repo = "cli";
        rev = selfSource.commit;
        hash = outputs.lib.sourceHash "gh-current" "srcHash";
      };
      vendorHash = outputs.lib.sourceHash "gh-current" "vendorHash";
      # Keep the maintained recipe's build, telemetry default, manual pages,
      # completions and native version check.
      meta = old.meta // {
        changelog = "https://github.com/cli/cli/releases/tag/v${selfSource.version}";
        platforms = [ "aarch64-darwin" ];
      };
    }
    // lib.optionalAttrs usesGo127Builder {
      # Archive sources omit the epoch file supplied by upstream's Git checkout.
      postPatch =
        assert old.postPatch == "export SOURCE_DATE_EPOCH=$(cat SOURCE_DATE_EPOCH)\n";
        "export SOURCE_DATE_EPOCH=${lib.escapeShellArg selfSource.pins.sourceDateEpoch}\n";
    }
  )
