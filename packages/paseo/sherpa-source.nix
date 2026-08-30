{
  closureContract ? (builtins.fromJSON (builtins.readFile ./native-lock.json)).sherpaOnnx,
  cmake,
  fetchurl,
  lib,
  onnxruntimeExact,
  pkg-config,
  src,
  stdenv,
}:
let
  # The updater owns the exact FetchContent closure; this derivation only
  # materializes it into the disconnected CMake cache.
  fetchContentCache = lib.mapAttrsToList (_: dependency: {
    name = dependency.file;
    src = fetchurl {
      inherit (dependency) url hash;
    };
  }) closureContract.dependencies;
in
stdenv.mkDerivation {
  pname = "paseo-sherpa-onnx";
  inherit (closureContract) version;
  inherit src;

  patches = [ ./sherpa-use-external-onnxruntime.patch ];

  nativeBuildInputs = [
    cmake
    pkg-config
  ];
  buildInputs = [ onnxruntimeExact ];

  strictDeps = true;
  dontStrip = true;

  env = {
    SHERPA_ONNXRUNTIME_INCLUDE_DIR = "${lib.getDev onnxruntimeExact}/include";
    SHERPA_ONNXRUNTIME_LIB_DIR = "${lib.getLib onnxruntimeExact}/lib";
  };

  preConfigure = ''
    ${lib.concatMapStringsSep "\n" (
      dependency: "cp ${dependency.src} ./${dependency.name}"
    ) fetchContentCache}
  '';

  cmakeFlags = [
    (lib.cmakeBool "FETCHCONTENT_QUIET" false)
    (lib.cmakeBool "BUILD_SHARED_LIBS" true)
    (lib.cmakeFeature "CMAKE_CXX_FLAGS" "-DSHERPA_ONNX_DISABLE_COREML")
    (lib.cmakeBool "SHERPA_ONNX_USE_PRE_INSTALLED_ONNXRUNTIME_IF_AVAILABLE" true)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_BINARY" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_C_API" true)
    (lib.cmakeBool "SHERPA_ONNX_BUILD_C_API_EXAMPLES" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_EXAMPLES" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_GPU" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_JNI" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_PORTAUDIO" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_PYTHON" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_SPEAKER_DIARIZATION" true)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_TESTS" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_TTS" true)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WEBSOCKET" false)
  ];

  passthru.paseoExactSource = closureContract;

  meta = {
    description = "Exact-source sherpa-onnx runtime for Paseo";
    homepage = "https://github.com/k2-fsa/sherpa-onnx";
    license = lib.licenses.asl20;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
}
