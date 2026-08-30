{
  cctools,
  closureContract ? (builtins.fromJSON (builtins.readFile ../native-lock.json)).sherpaOnnx,
  eigen_5,
  fetchFromGitHub,
  fetchurl,
  lib,
  libarchive,
  nlohmann_json,
  onnxRuntime,
  sherpa-onnx,
  srcHash,
  stdenv,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  inherit (closureContract) version commit;

  # Keep this in the locked sherpa-onnx-sys release's emitted static-link order. Besides
  # defining the delivered artifact contract, the order is exercised by the
  # link-only smoke check below.
  expectedArchiveNames = [
    "libsherpa-onnx-c-api.a"
    "libsherpa-onnx-core.a"
    "libkaldi-decoder-core.a"
    "libsherpa-onnx-kaldifst-core.a"
    "libsherpa-onnx-fstfar.a"
    "libsherpa-onnx-fst.a"
    "libkaldi-native-fbank-core.a"
    "libkissfft-float.a"
    "libonnxruntime.a"
    "libssentencepiece_core.a"
  ];

  activeCache = builtins.map (
    name:
    let
      dependency = closureContract.dependencies.${name};
    in
    {
      inherit (dependency) cmakeVariable;
      name = dependency.file;
      src = fetchurl {
        inherit (dependency) url hash;
      };
    }
  ) closureContract.dependencyOrder;

  # Override the pinned nixpkgs package while replacing its dependency cache
  # with the active, updater-reviewed closure below.
  base = sherpa-onnx.override {
    cudaSupport = false;
    onnxruntime = onnxRuntime;
    pythonSupport = false;
    websocketSupport = false;
  };
in
base.overrideAttrs (old: {
  pname = "buzz-sherpa-onnx";
  inherit version;

  src = fetchFromGitHub {
    owner = "k2-fsa";
    repo = "sherpa-onnx";
    rev = commit;
    fetchSubmodules = false;
    hash = srcHash;
  };

  outputs = [ "out" ];
  patches = [ ];
  nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ libarchive ];
  __darwinAllowLocalNetworking = false;
  separateDebugInfo = false;
  dontStrip = true;

  cmakeBuildType = "Release";
  preConfigure = ''
    ${lib.concatMapStringsSep "\n" (entry: "cp ${entry.src} ./${entry.name}") activeCache}

    offlineSources="$PWD/.buzz-offline-sources"
    rm -rf "$offlineSources"
    mkdir -p "$offlineSources"
    for archiveName in ${
      lib.concatMapStringsSep " " (entry: lib.escapeShellArg entry.name) activeCache
    }; do
      if [ ! -s "$PWD/$archiveName" ]; then
        echo "missing reviewed local Sherpa dependency archive: $archiveName" >&2
        exit 1
      fi
    done
    ${lib.concatMapStringsSep "\n" (entry: ''
      sourceDirectory="$offlineSources/${entry.cmakeVariable}"
      sourceInventory="$TMPDIR/buzz-sherpa-${entry.cmakeVariable}.inventory"
      mkdir -p "$sourceDirectory"
      ${lib.getExe' libarchive "bsdtar"} \
        --extract \
        --file "$PWD/${entry.name}" \
        --strip-components 1 \
        --directory "$sourceDirectory"
      if ! find "$sourceDirectory" -mindepth 1 -print -quit > "$sourceInventory"; then
        echo "failed to inspect reviewed Sherpa dependency source: ${entry.cmakeVariable}" >&2
        exit 1
      fi
      if [ ! -s "$sourceInventory" ]; then
        echo "reviewed Sherpa dependency source is empty: ${entry.cmakeVariable}" >&2
        exit 1
      fi
      cmakeFlagsArray+=(
        "-DFETCHCONTENT_SOURCE_DIR_${entry.cmakeVariable}=$sourceDirectory"
      )
    '') activeCache}
    for sourceDir in \
      ${lib.escapeShellArg nlohmann_json.src} \
      ${lib.escapeShellArg eigen_5.src}; do
      if [ ! -d "$sourceDir" ]; then
        echo "missing reviewed local Sherpa dependency source: $sourceDir" >&2
        exit 1
      fi
    done
    if [ ! -f "${lib.getDev onnxRuntime}/include/onnxruntime_c_api.h" ] \
      || [ ! -f "${lib.getLib onnxRuntime}/lib/libonnxruntime.a" ]; then
      echo "missing package-local ONNX Runtime development inputs" >&2
      exit 1
    fi
  '';

  cmakeFlags = [
    (lib.cmakeBool "FETCHCONTENT_QUIET" false)
    (lib.cmakeBool "FETCHCONTENT_UPDATES_DISCONNECTED" true)
    (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
    (lib.cmakeBool "BUILD_SHARED_LIBS" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_BINARY" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_C_API" true)
    (lib.cmakeBool "SHERPA_ONNX_BUILD_C_API_EXAMPLES" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_TESTS" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_CHECK" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_PYTHON" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_JNI" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WEBSOCKET" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_PORTAUDIO" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_GPU" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_DIRECTML" false)
    (lib.cmakeBool "SHERPA_ONNX_LINK_D3D" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_RKNN" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_AXERA" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_AXCL" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_ASCEND_NPU" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_QNN" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_SPACEMIT" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_SPEAKER_DIARIZATION" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_TTS" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_ASR" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_KWS" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_VAD" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_VAD_ASR" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_NODEJS" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_WASM_SPEECH_ENHANCEMENT" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_SPEAKER_DIARIZATION" false)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_TTS" false)
    (lib.cmakeBool "SHERPA_ONNX_USE_PRE_INSTALLED_ONNXRUNTIME_IF_AVAILABLE" true)
    (lib.cmakeBool "SHERPA_ONNX_ENABLE_SANITIZER" false)
    (lib.cmakeFeature "onnxruntime_SOURCE_DIR" "${lib.getDev onnxRuntime}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_JSON" "${nlohmann_json.src}")
    (lib.cmakeFeature "FETCHCONTENT_SOURCE_DIR_EIGEN" "${eigen_5.src}")
    (lib.cmakeFeature "CMAKE_CXX_FLAGS" "-DSHERPA_ONNX_DISABLE_COREML")
    (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
    "-Wno-dev"
  ];

  env = (old.env or { }) // {
    SHERPA_ONNXRUNTIME_INCLUDE_DIR = "${lib.getDev onnxRuntime}/include";
    SHERPA_ONNXRUNTIME_LIB_DIR = "${lib.getLib onnxRuntime}/lib";
  };

  doCheck = false;
  doInstallCheck = false;
  checkInputs = [ ];
  nativeCheckInputs = [ ];

  postInstall = (old.postInstall or "") + ''
    localOnnxArchive="${lib.getLib onnxRuntime}/lib/libonnxruntime.a"
    if [ ! -f "$localOnnxArchive" ]; then
      echo "missing package-local ONNX Runtime archive: $localOnnxArchive" >&2
      exit 1
    fi

    rm -f "$out/lib/libonnxruntime.a"
    ln -s "$localOnnxArchive" "$out/lib/libonnxruntime.a"
    rm -f \
      "$out/lib/libsherpa-onnx-cxx-api.a" \
      "$out/include/sherpa-onnx/c-api/cxx-api.h" \
      "$out/sherpa-onnx.pc"
  '';

  postFixup = (old.postFixup or "") + ''
    expectedInventory="$(
      printf '%s\n' ${lib.concatMapStringsSep " " lib.escapeShellArg expectedArchiveNames} |
        LC_ALL=C sort
    )"
    actualInventory="$(
      find "$out/lib" -maxdepth 1 \( -type f -o -type l \) -name '*.a' \
        -exec basename {} \; |
        LC_ALL=C sort
    )"
    if [ "$actualInventory" != "$expectedInventory" ]; then
      echo "unexpected sherpa-onnx static archive inventory" >&2
      printf 'expected:\n%s\nactual:\n%s\n' "$expectedInventory" "$actualInventory" >&2
      exit 1
    fi

    if [ ! -f "$out/include/sherpa-onnx/c-api/c-api.h" ]; then
      echo "missing sherpa-onnx C API header" >&2
      exit 1
    fi
    if [ -e "$out/lib/libsherpa-onnx-cxx-api.a" ] \
      || [ -e "$out/include/sherpa-onnx/c-api/cxx-api.h" ] \
      || [ -e "$out/sherpa-onnx.pc" ]; then
      echo "sherpa-onnx output retains a pruned C++ API or root pkg-config file" >&2
      exit 1
    fi
    if [ "$(readlink "$out/lib/libonnxruntime.a")" != \
      "${lib.getLib onnxRuntime}/lib/libonnxruntime.a" ]; then
      echo "sherpa-onnx does not reuse the package-local ONNX Runtime archive" >&2
      exit 1
    fi

    for archiveName in $expectedInventory; do
      archive="$out/lib/$archiveName"
      archiveArchitectures="$(${cctools}/bin/lipo -archs "$archive")"
      if [ "$archiveArchitectures" != "arm64" ]; then
        echo "expected arm64-only $archiveName, found: $archiveArchitectures" >&2
        exit 1
      fi
    done
    if ! ${cctools}/bin/nm -gU "$out/lib/libsherpa-onnx-c-api.a" \
      | grep -E '(^|[[:space:]])_SherpaOnnxCreateOfflineRecognizer$' >/dev/null; then
      echo "sherpa-onnx C API archive does not export _SherpaOnnxCreateOfflineRecognizer" >&2
      exit 1
    fi
    if ! ${cctools}/bin/nm -gU "$out/lib/libonnxruntime.a" \
      | grep -E '(^|[[:space:]])_OrtGetApiBase$' >/dev/null; then
      echo "package-local ONNX Runtime archive does not export _OrtGetApiBase" >&2
      exit 1
    fi

    if find "$out" \
      \( \
        -name '*.framework' -o \
        -name '*.dylib' -o -name '*.dylib.*' -o \
        -name '*.so' -o -name '*.so.*' \
      \) \
      -print -quit | grep -q .; then
      echo "sherpa-onnx output contains a framework or dynamic library" >&2
      exit 1
    fi
    if [ -d "$out/bin" ] && find "$out/bin" -type f -print -quit | grep -q .; then
      echo "sherpa-onnx output contains an executable" >&2
      exit 1
    fi

    smokeSource="$TMPDIR/buzz-sherpa-link-smoke.cc"
    printf '%s\n' \
      '#include <sherpa-onnx/c-api/c-api.h>' \
      'int main() { return SherpaOnnxCreateOfflineRecognizer(nullptr) == nullptr; }' \
      > "$smokeSource"
    "$CXX" -std=c++17 -I"$out/include" "$smokeSource" -L"$out/lib" \
      -lsherpa-onnx-c-api \
      -lsherpa-onnx-core \
      -lkaldi-decoder-core \
      -lsherpa-onnx-kaldifst-core \
      -lsherpa-onnx-fstfar \
      -lsherpa-onnx-fst \
      -lkaldi-native-fbank-core \
      -lkissfft-float \
      -lonnxruntime \
      -lssentencepiece_core \
      -lc++ \
      -framework Foundation \
      -o "$TMPDIR/buzz-sherpa-link-smoke"
  '';

  passthru = (old.passthru or { }) // {
    buzzNativeContract = {
      kind = "sherpa-onnx";
      inherit version commit;
      target = "aarch64-apple-darwin";
      linkMode = "static";
      usePreinstalledOnnxRuntime = true;
      precompiledReleaseArchivesAllowed = false;
      cmakeOptions = {
        BUILD_SHARED_LIBS = false;
        SHERPA_ONNX_ENABLE_BINARY = false;
        SHERPA_ONNX_ENABLE_C_API = true;
        SHERPA_ONNX_ENABLE_GPU = false;
        SHERPA_ONNX_ENABLE_TESTS = false;
        SHERPA_ONNX_ENABLE_TTS = false;
      };
    };
    buzzStaticArchiveLinkOrder = expectedArchiveNames;
  };

  meta = (old.meta or { }) // {
    description = "Static sherpa-onnx foundation for Buzz";
    changelog = "https://github.com/k2-fsa/sherpa-onnx/releases/tag/v${version}";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
})
