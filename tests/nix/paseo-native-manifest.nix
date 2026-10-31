let
  manifestFor = import ../../packages/paseo/native-manifest.nix;
  abseil = version: {
    pname = "abseil-cpp";
    inherit version;
  };
  protobuf = version: dependencies: {
    inherit version;
    propagatedBuildInputs = dependencies;
  };
  current = protobuf "32.1" [ (abseil "20260107.1") ];
  latest = protobuf "32.1" [ (abseil "20260817.0") ];
  selected =
    paseoVersion: value:
    builtins.baseNameOf (manifestFor {
      inherit paseoVersion;
      protobuf = value;
    });
  rejectedFor =
    paseoVersion: value:
    !(builtins.tryEval (
      builtins.toString (manifestFor {
        inherit paseoVersion;
        protobuf = value;
      })
    )).success;
  rejected = rejectedFor "0.9.1";
in
{
  current = selected "0.9.1" current;
  latest = selected "0.9.1" latest;
  releaseCurrent = selected "0.14.0" current;
  releaseLatest = selected "0.14.0" latest;
  unrelatedDependency = selected "0.9.1" (
    protobuf "32.1" [
      { pname = "zlib"; }
      (abseil "20260817.0")
    ]
  );
  unknownRelease = rejectedFor "0.13.2" latest;
  missingRelease = rejectedFor null latest;
  nonStringRelease = rejectedFor 14 latest;
  releaseUnknownAbseil = rejectedFor "0.14.0" (protobuf "32.1" [ (abseil "20260901.0") ]);
  missingDependencies = rejected { version = "32.1"; };
  emptyDependencies = rejected (protobuf "32.1" [ ]);
  duplicateDependencies = rejected (
    protobuf "32.1" [
      (abseil "20260107.1")
      (abseil "20260817.0")
    ]
  );
  unknownAbseil = rejected (protobuf "32.1" [ (abseil "20260901.0") ]);
  unknownProtobuf = rejected (protobuf "33.0" [ (abseil "20260817.0") ]);
  missingAbseilVersion = rejected (protobuf "32.1" [ { pname = "abseil-cpp"; } ]);
  missingProtobufVersion = rejected { propagatedBuildInputs = [ (abseil "20260817.0") ]; };
}
