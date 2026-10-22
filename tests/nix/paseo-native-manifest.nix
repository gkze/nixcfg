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
  rejected =
    value:
    !(builtins.tryEval (
      builtins.toString (manifestFor {
        protobuf = value;
      })
    )).success;
in
{
  current = builtins.baseNameOf (manifestFor {
    protobuf = protobuf "32.1" [ (abseil "20260107.1") ];
  });
  latest = builtins.baseNameOf (manifestFor {
    protobuf = protobuf "32.1" [ (abseil "20260817.0") ];
  });
  unrelatedDependency = builtins.baseNameOf (manifestFor {
    protobuf = protobuf "32.1" [
      { pname = "zlib"; }
      (abseil "20260817.0")
    ];
  });
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
