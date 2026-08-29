{
  applyPatches,
  cmake,
  cpuinfo,
  darwinMinVersionHook,
  eigen,
  fetchFromGitHub,
  fetchpatch,
  glibcLocales,
  howard-hinnant-date,
  lib,
  libpng,
  microsoft-gsl,
  nlohmann_json,
  pkg-config,
  protobuf_32,
  python3,
  src,
  stdenv,
  zlib,
}:
let
  closureContract = {
    version = "1.23.2";
    commit = "a83fc4d58cb48eb68890dd689f94f28288cf2278";
    sourceHash = "sha256-hZ2L5+0Enkw4rGDKVpRECnKXP87w6Kbiyp6Fdxwt6hk=";
    nixpkgsRecipe = {
      commit = "e1e423f183cde97926ac113d8a4de5a5042a7264";
      path = "pkgs/by-name/on/onnxruntime/package.nix";
    };
    dependencies = {
      abseilCpp = {
        version = "20240722.2";
        hash = "sha256-PuS7MLwi824c4z4Cubh029DEUVYSNPD3MwCDsgzsp3Y=";
      };
      dlpack = {
        commit = "5c210da409e7f1e51ddf445134a4376fdbd70d7d";
        hash = "sha256-YqgzCyNywixebpHGx16tUuczmFS5pjCz5WjR89mv9eI=";
      };
      flatbuffers = {
        version = "23.5.26";
        hash = "sha256-e+dNPNbCHYDXUS/W+hMqf/37fhVgEGzId6rhP3cToTE=";
      };
      mp11 = {
        version = "boost-1.82.0";
        hash = "sha256-cLPvjkf2Au+B19PJNrUkTW/VPxybi1MpPxnIl4oo4/o=";
      };
      onnx = {
        version = "v1.18.0";
        hash = "sha256-UhtF+CWuyv5/Pq/5agLL4Y95YNP63W2BraprhRqJOag=";
      };
      protobuf = {
        version = "32.1";
        hash = "sha256-wfu1MyCycGpxFB++eicA0F41j886/Y52I/4+ciRUg2o=";
        nixpkgsAttribute = "protobuf_32";
      };
      re2 = {
        version = "2024-07-02";
        hash = "sha256-IeANwJlJl45yf8iu/AZNDoiyIvTCZIeK1b74sdCfAIc=";
      };
      safeint = {
        version = "3.0.28";
        hash = "sha256-pjwjrqq6dfiVsXIhbBtbolhiysiFlFTnx5XcX77f+C0=";
      };
    };
    sourceClosureComplete = true;
  };

  abseilCppSrc = fetchFromGitHub {
    owner = "abseil";
    repo = "abseil-cpp";
    tag = closureContract.dependencies.abseilCpp.version;
    hash = closureContract.dependencies.abseilCpp.hash;
  };
  dlpackSrc = fetchFromGitHub {
    owner = "dmlc";
    repo = "dlpack";
    rev = closureContract.dependencies.dlpack.commit;
    hash = closureContract.dependencies.dlpack.hash;
  };
  flatbuffersSrc = fetchFromGitHub {
    owner = "google";
    repo = "flatbuffers";
    rev = "v${closureContract.dependencies.flatbuffers.version}";
    hash = closureContract.dependencies.flatbuffers.hash;
  };
  mp11Src = fetchFromGitHub {
    owner = "boostorg";
    repo = "mp11";
    tag = closureContract.dependencies.mp11.version;
    hash = closureContract.dependencies.mp11.hash;
  };
  onnxSrc = applyPatches {
    name = "paseo-onnx-source-${closureContract.dependencies.onnx.version}";
    src = fetchFromGitHub {
      owner = "onnx";
      repo = "onnx";
      tag = closureContract.dependencies.onnx.version;
      hash = closureContract.dependencies.onnx.hash;
    };
    patches = [
      (fetchpatch {
        url = "https://github.com/onnx/onnx/commit/595a069aaac07586f111681245bc808ee63551f8.patch";
        includes = [ "onnx/defs/schema.h" ];
        hash = "sha256-FFAJuJse4nmNT3ixvEdlqzbr3edY46SqEFv7z/oo6m0=";
      })
      (fetchpatch {
        url = "https://github.com/onnx/onnx/commit/6769c41ad64ebca0358da8c7211d2c6d0e627b2b.patch";
        hash = "sha256-VlTHs0om20kTNvSVQaasSsa5JROliQy4k9BECTsBtbU=";
      })
    ];
  };
  re2Src = fetchFromGitHub {
    owner = "google";
    repo = "re2";
    rev = closureContract.dependencies.re2.version;
    hash = closureContract.dependencies.re2.hash;
  };
  safeintSrc = fetchFromGitHub {
    owner = "dcleblanc";
    repo = "safeint";
    tag = closureContract.dependencies.safeint.version;
    hash = closureContract.dependencies.safeint.hash;
  };
  protobufExact =
    assert lib.assertMsg (
      lib.getVersion protobuf_32 == closureContract.dependencies.protobuf.version
    ) "Paseo ONNX Runtime requires protobuf 32.1";
    protobuf_32;
in
stdenv.mkDerivation (_finalAttrs: {
  pname = "paseo-onnxruntime";
  inherit (closureContract) version;
  inherit src;

  patches = [
    (fetchpatch {
      url = "https://github.com/microsoft/onnxruntime/commit/d6e712c5b7b6260a61e54d1fe40107cf5366ee77.patch";
      hash = "sha256-FSuPybX8f2VoxvLhcYx4rdChaiK8bSUDR32sN3Efwfc=";
    })
    (fetchpatch {
      url = "https://github.com/microsoft/onnxruntime/commit/8ebd0bf1cf02414584d15d7244b07fa97d65ba02.patch";
      hash = "sha256-vX+kaFiNdmqWI91JELcLpoaVIHBb5EPbI7rCAMYAx04=";
    })
    ./protobuf34-nodiscard.patch
    ./onnxruntime-pkgconfig-prefix.patch
  ];

  nativeBuildInputs = [
    cmake
    pkg-config
    protobufExact
    python3
  ];
  buildInputs = [
    eigen
    glibcLocales
    howard-hinnant-date
    libpng
    microsoft-gsl
    nlohmann_json
    protobufExact
    zlib
  ]
  ++ lib.optional (lib.meta.availableOn stdenv.hostPlatform cpuinfo) cpuinfo
  ++ [ (darwinMinVersionHook "13.3") ];

  strictDeps = true;
  separateDebugInfo = true;
  enableParallelBuilding = true;
  cmakeDir = "../cmake";
  outputs = [
    "out"
    "dev"
  ];

  cmakeFlags = [
    "--compile-no-warning-as-error"
    (lib.cmakeBool "ABSL_ENABLE_INSTALL" true)
    (lib.cmakeFeature "CMAKE_CXX_FLAGS" "-Wno-error=unused-variable")
    (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
    (lib.cmakeBool "FETCHCONTENT_QUIET" false)
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_ABSEIL_CPP" "${abseilCppSrc}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_DLPACK" "${dlpackSrc}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_FLATBUFFERS" "${flatbuffersSrc}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_MP11" "${mp11Src}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_ONNX" "${onnxSrc}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_RE2" "${re2Src}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_SAFEINT" "${safeintSrc}")
    (lib.cmakeFeature "FETCHCONTENT_TRY_FIND_PACKAGE_MODE" "ALWAYS")
    (lib.cmakeFeature "ONNX_CUSTOM_PROTOC_EXECUTABLE" (lib.getExe protobufExact))
    (lib.cmakeBool "onnxruntime_BUILD_SHARED_LIB" true)
    (lib.cmakeBool "onnxruntime_BUILD_UNIT_TESTS" false)
    (lib.cmakeBool "onnxruntime_ENABLE_LTO" false)
    (lib.cmakeBool "onnxruntime_ENABLE_PYTHON" false)
    (lib.cmakeBool "onnxruntime_USE_COREML" false)
    (lib.cmakeBool "onnxruntime_USE_CUDA" false)
    (lib.cmakeBool "onnxruntime_USE_FULL_PROTOBUF" false)
    (lib.cmakeBool "onnxruntime_USE_MIGRAPHX" false)
    (lib.cmakeBool "onnxruntime_USE_NCCL" false)
    (lib.cmakeBool "onnxruntime_USE_OPENVINO" false)
    (lib.cmakeBool "onnxruntime_USE_ROCM" false)
    (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
  ];

  doCheck = false;
  checkInputs = [ ];
  nativeCheckInputs = [ ];

  postPatch = ''
    substituteInPlace onnxruntime/core/platform/env.h \
      --replace-fail \
        "GetRuntimePath() const { return PathString(); }" \
        "GetRuntimePath() const { return PathString(\"$out/lib/\"); }"
    substituteInPlace cmake/onnxruntime.cmake \
      --replace-fail "INSTALL_NAME_DIR @rpath" "INSTALL_NAME_DIR $out/lib"
  '';

  postInstall = ''
    install -m644 -Dt "$out/include" \
      ../include/onnxruntime/core/framework/provider_options.h \
      ../include/onnxruntime/core/providers/cpu/cpu_provider_factory.h \
      ../include/onnxruntime/core/session/onnxruntime_*.h
  '';

  passthru = {
    paseoExactSource = closureContract;
    protobuf = protobufExact;
  };

  meta = {
    description = "Exact-source ONNX Runtime foundation for Paseo";
    homepage = "https://github.com/microsoft/onnxruntime";
    license = lib.licenses.mit;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
})
