{
  slib,
  sources,
  prev,
  ...
}:
{
  sentry-cli =
    let
      filteredSrc = prev.fetchFromGitHub {
        owner = "getsentry";
        repo = "sentry-cli";
        tag = sources.sentry-cli.version;
        hash = slib.sourceHash "sentry-cli" "srcHash";
        postFetch = ''
          find $out -name '*.xcarchive' -type d -exec rm -rf {} +
        '';
      };
    in
    prev.sentry-cli.overrideAttrs (old: {
      inherit (sources.sentry-cli) version;
      src = filteredSrc;
      buildInputs = (old.buildInputs or [ ]) ++ [ prev.curl ];
      cargoDeps = prev.rustPlatform.fetchCargoVendor {
        src = filteredSrc;
        hash = slib.sourceHash "sentry-cli" "cargoHash";
      };
      # postFetch strips .xcarchive bundles (macOS code-signed), which
      # breaks this test that expects them present in the source tree.
      checkFlags = (old.checkFlags or [ ]) ++ [
        "--skip=commands::build::upload::tests::test_xcarchive_upload_includes_parsed_assets"
      ];
    });
}
