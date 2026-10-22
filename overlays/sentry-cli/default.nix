{
  slib,
  selfSource,
  prev,
  ...
}:
{
  sentry-cli =
    let
      filteredSrc = prev.fetchFromGitHub {
        owner = "getsentry";
        repo = "sentry-cli";
        rev = selfSource.commit;
        hash = slib.sourceHash "sentry-cli" "srcHash";
        postFetch = ''
          find $out -name '*.xcarchive' -type d -exec rm -rf {} +
        '';
      };
    in
    prev.sentry-cli.overrideAttrs (old: {
      inherit (selfSource) version;
      src = filteredSrc;
      # CoreUI asset parsing is validated on macOS 27 by the bundled IPA test.
      patches = (old.patches or [ ]) ++ [ ./macos27-assets.patch ];
      buildInputs = (old.buildInputs or [ ]) ++ [ prev.curl ];
      cargoDeps = prev.rustPlatform.fetchCargoVendor {
        src = filteredSrc;
        # CoreUI asset parsing is validated on macOS 27 by the bundled IPA test.
        patches = (old.patches or [ ]) ++ [ ./macos27-assets.patch ];
        hash = slib.sourceHash "sentry-cli" "cargoHash";
      };
      # postFetch strips .xcarchive bundles (macOS code-signed), which
      # breaks this test that expects them present in the source tree.
      checkFlags = (old.checkFlags or [ ]) ++ [
        "--skip=commands::build::upload::tests::test_xcarchive_upload_includes_parsed_assets"
      ];
    });
}
