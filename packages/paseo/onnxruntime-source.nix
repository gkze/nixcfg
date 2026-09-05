{
  applyPatches,
  cmake,
  closureContract ? (builtins.fromJSON (builtins.readFile ./native-lock.json)).onnxruntime,
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
  sourceHash ? null,
  src,
  stdenv,
  zlib,
}:
let
  effectiveClosureContract = closureContract // {
    inherit sourceHash;
  };
  fetchGitHubDependency =
    dependency:
    fetchFromGitHub {
      inherit (dependency) owner repo hash;
      rev = dependency.commit;
    };
  fetchReviewedPatch =
    patch:
    fetchpatch (
      {
        inherit (patch) url hash;
      }
      // lib.optionalAttrs (patch ? includes) { inherit (patch) includes; }
    );
  onnxPatches = builtins.filter (patch: patch.target == "onnx") closureContract.patches;
  onnxruntimePatches = builtins.filter (patch: patch.target == "onnxruntime") closureContract.patches;

  abseilCppSrc = fetchGitHubDependency closureContract.dependencies.abseilCpp;
  dlpackSrc = fetchGitHubDependency closureContract.dependencies.dlpack;
  flatbuffersSrc = fetchGitHubDependency closureContract.dependencies.flatbuffers;
  mp11Src = fetchGitHubDependency closureContract.dependencies.mp11;
  onnxSrc = applyPatches {
    name = "paseo-onnx-source-${closureContract.dependencies.onnx.version}";
    src = fetchGitHubDependency closureContract.dependencies.onnx;
    patches = map fetchReviewedPatch onnxPatches;
  };
  re2Src = fetchGitHubDependency closureContract.dependencies.re2;
  safeintSrc = fetchGitHubDependency closureContract.dependencies.safeint;
  protobuf32 =
    assert lib.assertMsg (
      lib.versions.major (lib.getVersion protobuf_32) == "32"
    ) "Paseo ONNX Runtime requires the protobuf_32 major version lane";
    protobuf_32;
in
stdenv.mkDerivation (_finalAttrs: {
  pname = "paseo-onnxruntime";
  inherit (closureContract) version;
  inherit src;

  patches = (map fetchReviewedPatch onnxruntimePatches) ++ [
    ./protobuf34-nodiscard.patch
    ./onnxruntime-pkgconfig-prefix.patch
  ];

  nativeBuildInputs = [
    cmake
    pkg-config
    protobuf32
    python3
  ];
  buildInputs = [
    eigen
    glibcLocales
    howard-hinnant-date
    libpng
    microsoft-gsl
    nlohmann_json
    protobuf32
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
    (lib.cmakeFeature "ONNX_CUSTOM_PROTOC_EXECUTABLE" (lib.getExe protobuf32))
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
    paseoExactSource = effectiveClosureContract;
    protobuf = protobuf32;
  };

  meta = {
    description = "Exact-source ONNX Runtime foundation for Paseo";
    homepage = "https://github.com/microsoft/onnxruntime";
    license = lib.licenses.mit;
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
})
