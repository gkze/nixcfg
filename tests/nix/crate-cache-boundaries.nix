{
  lib,
  system,
}:
let
  gitbutlerPolicy = import ../../packages/gitbutler/crate-cache-policy.nix { inherit lib; };
  zedPolicy = import ../../packages/zed-editor-nightly/crate-cache-policy.nix { inherit lib; };

  mkProbe =
    name: attrs:
    builtins.derivation (
      {
        inherit name system;
        builder = "/bin/false";
        args = [ ];
      }
      // attrs
    );
  sorted = builtins.sort builtins.lessThan;
  changedCrates =
    names: baselineFor: changedFor:
    sorted (builtins.filter (name: (baselineFor name).drvPath != (changedFor name).drvPath) names);

  expectedGitbutlerVersionConsumers = [
    "but"
    "but-update"
  ];
  expectedGitbutlerChannelConsumers = [
    "but"
    "but-api"
    "but-path"
    "but-update"
    "gitbutler-tauri"
  ];
  gitbutlerCrates = lib.unique (
    expectedGitbutlerChannelConsumers ++ expectedGitbutlerVersionConsumers ++ [ "but-error" ]
  );
  gitbutlerProbe =
    metadata: crateName:
    mkProbe "gitbutler-${crateName}" (
      gitbutlerPolicy.forCrate {
        inherit crateName;
        inherit (metadata) channel version;
      }
    );
  gitbutlerBaseline = {
    channel = "release";
    version = "1.2.3-cache-probe";
  };
  gitbutlerChangedVersion = gitbutlerBaseline // {
    version = "9.8.7-cache-probe";
  };
  gitbutlerChangedChannel = gitbutlerBaseline // {
    channel = "nightly";
  };

  expectedZedCommitShaConsumers = [
    "cli"
    "edit_prediction_cli"
    "eval_cli"
    "remote_server"
    "zed"
  ];
  expectedZedReleaseVersionConsumers = [ "cli" ];
  expectedZedUpdateExplanationConsumers = [
    "auto_update"
    "cli"
  ];
  expectedZedFontConfigConsumers = [ "zed" ];
  expectedZedLivekitWebrtcConsumers = [ "webrtc-sys" ];
  expectedZedProtocConsumers = [
    "livekit_api"
    "proto"
  ];
  zedCrates = lib.unique (
    expectedZedCommitShaConsumers
    ++ expectedZedReleaseVersionConsumers
    ++ expectedZedUpdateExplanationConsumers
    ++ expectedZedFontConfigConsumers
    ++ expectedZedLivekitWebrtcConsumers
    ++ expectedZedProtocConsumers
    ++ [ "client" ]
  );
  zedProbe =
    metadata: crateName:
    mkProbe "zed-${crateName}" (
      zedPolicy.forCrate {
        inherit crateName;
        inherit (metadata)
          commitSha
          fontConfig
          livekitWebrtc
          protoc
          releaseVersion
          updateExplanation
          ;
      }
    );
  zedBaseline = {
    commitSha = "1111111111111111111111111111111111111111";
    fontConfig = "font-config-a";
    livekitWebrtc = "livekit-webrtc-a";
    protoc = "protoc-a";
    releaseVersion = "unstable-11111111";
    updateExplanation = "managed by Nix";
  };
  zedChangedCommit = zedBaseline // {
    commitSha = "2222222222222222222222222222222222222222";
  };
  zedChangedReleaseVersion = zedBaseline // {
    releaseVersion = "unstable-22222222";
  };
  zedChangedFontConfig = zedBaseline // {
    fontConfig = "font-config-b";
  };
  zedChangedLivekitWebrtc = zedBaseline // {
    livekitWebrtc = "livekit-webrtc-b";
  };
  zedChangedProtoc = zedBaseline // {
    protoc = "protoc-b";
  };
  zedChangedUpdateExplanation = zedBaseline // {
    updateExplanation = "managed elsewhere";
  };

  checks = [
    (
      assert sorted gitbutlerPolicy.versionConsumers == sorted expectedGitbutlerVersionConsumers;
      true
    )
    (
      assert sorted gitbutlerPolicy.channelConsumers == sorted expectedGitbutlerChannelConsumers;
      true
    )
    (
      assert
        changedCrates gitbutlerCrates (gitbutlerProbe gitbutlerBaseline) (
          gitbutlerProbe gitbutlerChangedVersion
        ) == sorted expectedGitbutlerVersionConsumers;
      true
    )
    (
      assert
        changedCrates gitbutlerCrates (gitbutlerProbe gitbutlerBaseline) (
          gitbutlerProbe gitbutlerChangedChannel
        ) == sorted expectedGitbutlerChannelConsumers;
      true
    )
    (
      assert gitbutlerPolicy.patchedSourceName "but" == "but";
      assert gitbutlerPolicy.patchedSourceName "gitbutler-tauri" == "gitbutler-tauri";
      true
    )
    (
      assert sorted zedPolicy.commitShaConsumers == sorted expectedZedCommitShaConsumers;
      true
    )
    (
      assert sorted zedPolicy.releaseVersionConsumers == sorted expectedZedReleaseVersionConsumers;
      true
    )
    (
      assert sorted zedPolicy.updateExplanationConsumers == sorted expectedZedUpdateExplanationConsumers;
      true
    )
    (
      assert sorted zedPolicy.fontConfigConsumers == sorted expectedZedFontConfigConsumers;
      true
    )
    (
      assert sorted zedPolicy.livekitWebrtcConsumers == sorted expectedZedLivekitWebrtcConsumers;
      true
    )
    (
      assert sorted zedPolicy.protocConsumers == sorted expectedZedProtocConsumers;
      true
    )
    (
      assert
        changedCrates zedCrates (zedProbe zedBaseline) (zedProbe zedChangedCommit)
        == sorted expectedZedCommitShaConsumers;
      true
    )
    (
      assert
        changedCrates zedCrates (zedProbe zedBaseline) (zedProbe zedChangedReleaseVersion)
        == sorted expectedZedReleaseVersionConsumers;
      true
    )
    (
      assert
        changedCrates zedCrates (zedProbe zedBaseline) (zedProbe zedChangedFontConfig)
        == sorted expectedZedFontConfigConsumers;
      true
    )
    (
      assert
        changedCrates zedCrates (zedProbe zedBaseline) (zedProbe zedChangedLivekitWebrtc)
        == sorted expectedZedLivekitWebrtcConsumers;
      true
    )
    (
      assert
        changedCrates zedCrates (zedProbe zedBaseline) (zedProbe zedChangedProtoc)
        == sorted expectedZedProtocConsumers;
      true
    )
    (
      assert
        changedCrates zedCrates (zedProbe zedBaseline) (zedProbe zedChangedUpdateExplanation)
        == sorted expectedZedUpdateExplanationConsumers;
      true
    )
    (
      assert zedPolicy.preparedSourceName "client" == "zed-editor-nightly-client-src";
      true
    )
  ];
in
# The real derivation paths are the contract: AST inspection cannot prove that
# metadata changes leave non-consuming crate cache keys untouched.
builtins.deepSeq checks true
