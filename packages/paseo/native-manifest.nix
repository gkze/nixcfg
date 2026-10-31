{ protobuf, paseoVersion }:
let
  # ONNX's pinned Abseil is static. Its external protobuf runtime brings a
  # separate dynamic Abseil closure, which the addon copies into the app.
  abseilDependencies = builtins.filter (dependency: (dependency.pname or "") == "abseil-cpp") (
    protobuf.propagatedBuildInputs or [ ]
  );
  abseil =
    if builtins.length abseilDependencies == 1 then
      builtins.head abseilDependencies
    else
      throw "Paseo native inventory requires exactly one propagated protobuf Abseil dependency";
  tuple = "${protobuf.version or ""}/${abseil.version or ""}";
  # Each complete manifest is reviewed against the realized native bundle.
  # A new release or dependency tuple must add its own exact inventory and
  # native proof. 0.14.0 adds the prebuilt msgpackr-extract N-API addon that
  # @opencode/client reaches through effect and msgpackr. Drop 0.9.1 once
  # Update promotes the 0.14.0 pin.
  releases = {
    "0.9.1" = {
      "32.1/20260107.1" = ./native-manifest.txt;
      "32.1/20260817.0" = ./native-manifest-abseil-20260817.txt;
    };
    "0.14.0" = {
      "32.1/20260107.1" = ./native-manifest-0.14.0.txt;
      "32.1/20260817.0" = ./native-manifest-0.14.0-abseil-20260817.txt;
    };
  };
  manifests =
    if builtins.isString paseoVersion && releases ? ${paseoVersion} then
      releases.${paseoVersion}
    else
      throw "Paseo native inventory has no reviewed release ${builtins.toJSON paseoVersion}";
in
manifests.${tuple}
  or (throw "Paseo native inventory has no reviewed protobuf/Abseil tuple ${tuple}")
