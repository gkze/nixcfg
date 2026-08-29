{
  cmake,
  fetchurl,
  lib,
  onnxruntimeExact,
  pkg-config,
  src,
  stdenv,
}:
let
  # sherpa-onnx's CMake graph uses FetchContent for both direct and nested
  # dependencies. Keep this contract in the Paseo package so the npm addon
  # equivalent is reproducible without network access in the build sandbox.
  closureContract = {
    version = "1.12.28";
    commit = "86d3d00e28c22c102fb7d01c7b62fdc4e7a69f1b";
    onnxruntime = {
      version = "1.23.2";
      source = "paseo-exact-source-build";
    };
    npmAddonBuild = {
      workflow = ".github/workflows/npm-addon-macos.yaml";
      portaudio = false;
      websocket = false;
      tts = true;
      speakerDiarization = true;
    };
    dependencies = {
      eigen = {
        file = "eigen-3.4.1.tar.gz";
        url = "https://gitlab.com/libeigen/eigen/-/archive/3.4.1/eigen-3.4.1.tar.gz";
        hash = "sha256-uTxmfRtpJlzbTZ8w7CH4+su+izB880wLmUKDTG1P2+I=";
      };
      espeakNg = {
        file = "espeak-ng-f6fed6c58b5e0998b8e68c6610125e2d07d595a7.zip";
        url = "https://github.com/csukuangfj/espeak-ng/archive/f6fed6c58b5e0998b8e68c6610125e2d07d595a7.zip";
        hash = "sha256-cMv0BQ56AUquGRQLBeVySdpHIPVhKEWfvjqTvq+XGuY=";
      };
      hclustCpp = {
        file = "hclust-cpp-2026-02-25.tar.gz";
        url = "https://github.com/csukuangfj/hclust-cpp/archive/refs/tags/2026-02-25.tar.gz";
        hash = "sha256-jxTgJMcJ1zr7QK5pyyLeS3Pbpny85A8uUYgT2oE5q1Y=";
      };
      json = {
        file = "json-3.12.0.tar.gz";
        url = "https://github.com/nlohmann/json/archive/refs/tags/v3.12.0.tar.gz";
        hash = "sha256-S5LrDAbRBoP3RHzpQGy5fNS0U74Y1yeTIPey8CXBAYc=";
      };
      kaldiDecoder = {
        file = "kaldi-decoder-0.2.11.tar.gz";
        url = "https://github.com/k2-fsa/kaldi-decoder/archive/refs/tags/v0.2.11.tar.gz";
        hash = "sha256-hcpGJTVZJUHrW6bSGEMAnPNHOPUbKLcfhIgqNpS1KL8=";
      };
      kaldiNativeFbank = {
        file = "kaldi-native-fbank-1.22.3.tar.gz";
        url = "https://github.com/csukuangfj/kaldi-native-fbank/archive/refs/tags/v1.22.3.tar.gz";
        hash = "sha256-kXbMZvx84e34XPNVsG4yDFfbYpffdCd/V1GDRoiTz2E=";
      };
      kaldifst = {
        file = "kaldifst-1.7.17.tar.gz";
        url = "https://github.com/k2-fsa/kaldifst/archive/refs/tags/v1.7.17.tar.gz";
        hash = "sha256-xLcBojpAC9qAMlhrAsfg1egTp2WDLfYMI+bfnmKwEPQ=";
      };
      kissfft = {
        file = "kissfft-febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip";
        url = "https://github.com/mborgerding/kissfft/archive/febd4caeed32e33ad8b2e0bb5ea77542c40f18ec.zip";
        hash = "sha256-SXED5mQWjr45WAt1etvmFvbPhaFlcq9YHKe8QtCrE/0=";
      };
      openfst = {
        file = "openfst-sherpa-onnx-2024-06-19.tar.gz";
        url = "https://github.com/csukuangfj/openfst/archive/refs/tags/sherpa-onnx-2024-06-19.tar.gz";
        hash = "sha256-XJjoLMUJxWGFAt3khguOoE2EOFDtV+bWtZC2RLJohT0=";
      };
      piperPhonemize = {
        file = "piper-phonemize-78a788e0b719013401572d70fef372e77bff8e43.zip";
        url = "https://github.com/csukuangfj/piper-phonemize/archive/78a788e0b719013401572d70fef372e77bff8e43.zip";
        hash = "sha256-iWQaRkiaSJh1RkPOV72pybVLTKRkhf3AK/DchLhmZF0=";
      };
      simpleSentencepiece = {
        file = "simple-sentencepiece-0.7.tar.gz";
        url = "https://github.com/pkufool/simple-sentencepiece/archive/refs/tags/v0.7.tar.gz";
        hash = "sha256-F0ioIgYKNbqp9mCfhO/I61TcDnS57OPYI2e3EZ/cda8=";
      };
    };
    sourceClosureComplete = true;
  };

  fetchContentCache = lib.mapAttrsToList (_: dependency: {
    name = dependency.file;
    src = fetchurl {
      inherit (dependency) url hash;
    };
  }) closureContract.dependencies;
in
stdenv.mkDerivation {
  pname = "paseo-sherpa-onnx";
  version = "1.12.28";
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
