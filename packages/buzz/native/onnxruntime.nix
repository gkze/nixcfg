{
  abseil-cpp_202601,
  cctools,
  fetchFromGitHub,
  ld64,
  lib,
  onnxruntime,
  protobuf,
  python3,
  srcHash,
  stdenv,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  version = "1.27.0";
  commit = "8f0278c77bf44b0cc83c098c6c722b92a36ac4b5";

  # The upstream Apple static-framework assembler deliberately skips shared
  # external targets. Keep protobuf and its Abseil closure static so they are
  # folded into the one delivered archive instead of surviving as unresolved
  # dylib references. Their __FILE__ strings need the same reproducible path
  # normalization as ONNX Runtime's own objects.
  normalizeStaticDependency =
    dependency:
    dependency.overrideAttrs (old: {
      preConfigure = (old.preConfigure or "") + ''
        export NIX_CFLAGS_COMPILE="''${NIX_CFLAGS_COMPILE-} -ffile-prefix-map=$NIX_BUILD_TOP=/build -ffile-prefix-map=/nix/store=/source-store"
        export NIX_CXXFLAGS_COMPILE="''${NIX_CXXFLAGS_COMPILE-} -ffile-prefix-map=$NIX_BUILD_TOP=/build -ffile-prefix-map=/nix/store=/source-store"
      '';
    });
  staticAbseil = normalizeStaticDependency (abseil-cpp_202601.override { static = true; });
  staticProtobuf = normalizeStaticDependency (
    protobuf.override {
      abseil-cpp = staticAbseil;
      enableShared = false;
    }
  );

  base = onnxruntime.override {
    abseil-cpp = staticAbseil;
    coremlSupport = false;
    cudaSupport = false;
    ncclSupport = false;
    openvinoSupport = false;
    protobuf = staticProtobuf;
    pythonSupport = false;
    rocmSupport = false;
  };

  cmakeSourcePath =
    name:
    let
      prefix = "-DFETCHCONTENT_SOURCE_DIR_${name}:STRING=";
    in
    lib.removePrefix prefix (
      lib.findSingle (lib.hasPrefix prefix) (throw "ONNX Runtime is missing ${name} source metadata")
        (throw "ONNX Runtime has duplicate ${name} source metadata")
        base.cmakeFlags
    );
in
base.overrideAttrs (old: {
  pname = "buzz-onnxruntime";
  inherit version;

  src = fetchFromGitHub {
    owner = "microsoft";
    repo = "onnxruntime";
    rev = commit;
    fetchSubmodules = false;
    hash = srcHash;
  };

  patches = (old.patches or [ ]) ++ [
    ./onnxruntime-macos-static-archive.patch
  ];

  outputChecks = lib.recursiveUpdate (old.outputChecks or { }) {
    out.allowedReferences = [ ];
    dev.allowedReferences = [ "out" ];
  };

  cmakeBuildType = "Release";
  cmakeFlags = (old.cmakeFlags or [ ]) ++ [
    # Shared-library mode is only the upstream trigger for its Apple static
    # framework assembler. No shared artifact survives installation.
    (lib.cmakeBool "onnxruntime_BUILD_SHARED_LIB" true)
    (lib.cmakeBool "onnxruntime_BUILD_APPLE_FRAMEWORK" true)
    (lib.cmakeBool "onnxruntime_BUILD_UNIT_TESTS" false)
    (lib.cmakeBool "onnxruntime_ENABLE_PYTHON" false)
    (lib.cmakeBool "onnxruntime_USE_COREML" false)
    (lib.cmakeBool "onnxruntime_USE_CUDA" false)
    (lib.cmakeBool "onnxruntime_USE_NCCL" false)
    (lib.cmakeBool "onnxruntime_USE_MIGRAPHX" false)
    (lib.cmakeBool "onnxruntime_USE_OPENVINO" false)
    (lib.cmakeBool "onnxruntime_USE_ROCM" false)
    (lib.cmakeBool "onnxruntime_ENABLE_LTO" false)
    (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
    (lib.cmakeFeature "PLATFORM_NAME" "macosx")
  ];

  doCheck = false;
  doInstallCheck = false;
  checkInputs = [ ];
  nativeCheckInputs = [ ];
  separateDebugInfo = false;

  # __FILE__ is retained in the final relocatable object. Normalize both the
  # randomized build root and fetched-source store roots before compilation.
  preConfigure = (old.preConfigure or "") + ''
    sourcePrefixMaps="-ffile-prefix-map=$NIX_BUILD_TOP=/build -ffile-prefix-map=${cmakeSourcePath "ONNX"}=/source/onnx -ffile-prefix-map=${cmakeSourcePath "ABSEIL_CPP"}=/source/abseil -ffile-prefix-map=${cmakeSourcePath "RE2"}=/source/re2 -ffile-prefix-map=${staticProtobuf}=/source/protobuf"
    export NIX_CFLAGS_COMPILE="$sourcePrefixMaps ''${NIX_CFLAGS_COMPILE-}"
    export NIX_CXXFLAGS_COMPILE="$sourcePrefixMaps ''${NIX_CXXFLAGS_COMPILE-}"
  '';

  # Keep the version-aligned nixpkgs patch hooks, then make the Apple archive
  # assembly independent of host /usr/bin tools. cctools' libtool is a split
  # output; `${cctools}/bin/libtool` is not the owning store path. nixpkgs also
  # pins the dynamic package to its store output; restore upstream's relative
  # fallback and install name before assembling this static-only archive.
  postPatch = (old.postPatch or "") + ''
    substituteInPlace cmake/onnxruntime.cmake \
      --replace-fail "/usr/bin/ar" "${cctools}/bin/ar" \
      --replace-fail "/usr/bin/ld" "${ld64}/bin/ld" \
      --replace-fail "/usr/bin/libtool" "${cctools.libtool}/bin/libtool" \
      --replace-fail \
        'set(STATIC_FRAMEWORK_OUTPUT_DIR ''${CMAKE_CURRENT_BINARY_DIR}/''${CMAKE_BUILD_TYPE}-''${CMAKE_OSX_SYSROOT})' \
        'set(STATIC_FRAMEWORK_OUTPUT_DIR ''${CMAKE_CURRENT_BINARY_DIR}/buzz-static-framework-output)'
    substituteInPlace onnxruntime/core/platform/posix/env.cc \
      --replace-fail "$out/lib/" ""
    substituteInPlace cmake/onnxruntime.cmake \
      --replace-fail "INSTALL_NAME_DIR $out/lib" "INSTALL_NAME_DIR @rpath"
  '';

  postInstall = (old.postInstall or "") + ''
    staticFrameworkBinary="buzz-static-framework-output/static_framework/onnxruntime.framework/onnxruntime"
    if [ ! -f "$staticFrameworkBinary" ]; then
      echo "missing assembled static ONNX Runtime framework binary: $staticFrameworkBinary" >&2
      exit 1
    fi

    mkdir -p "$out/lib"
    install -m 444 "$staticFrameworkBinary" "$out/lib/libonnxruntime.a"
    rm -f "$out/include/coreml_provider_factory.h"
    find "$out" -name '*.framework' -prune -exec rm -rf {} +
    find "$out" \( \
      -name '*.dylib' -o -name '*.dylib.*' -o -name '*.so' -o -name '*.so.*' \
    \) -exec rm -f {} +
    rm -rf "$out/lib/cmake" "$out/lib/pkgconfig"
    if [ -d "$out/bin" ]; then
      rmdir "$out/bin"
    fi
  '';

  # Validate after the multiple-output hooks have moved headers into $dev.
  postFixup = (old.postFixup or "") + ''
    archive="$out/lib/libonnxruntime.a"
    ${python3}/bin/python3 ${./normalize_ar.py} "$archive"
    for requiredPath in \
      "$archive" \
      "$dev/include/onnxruntime_c_api.h" \
      "$dev/include/cpu_provider_factory.h" \
      "$dev/include/provider_options.h"; do
      if [ ! -f "$requiredPath" ]; then
        echo "missing required ONNX Runtime output: $requiredPath" >&2
        exit 1
      fi
    done

    staticArchiveCount="$(
      find "$out" "$dev" -type f -name 'libonnxruntime.a' -print |
        wc -l | tr -d '[:space:]'
    )"
    if [ "$staticArchiveCount" -ne 1 ]; then
      echo "expected exactly one delivered libonnxruntime.a, found $staticArchiveCount" >&2
      exit 1
    fi

    archiveMembers="$(${cctools}/bin/ar -t "$archive")"
    expectedArchiveMembers='__.SYMDEF SORTED
    prelinked_objects.o'
    if [ "$archiveMembers" != "$expectedArchiveMembers" ]; then
      echo "unexpected libonnxruntime.a member inventory:" >&2
      printf '%s\n' "$archiveMembers" >&2
      exit 1
    fi
    archiveOwners="$(
      ${cctools}/bin/ar -tv "$archive" |
        awk '{ print $2 }' |
        LC_ALL=C sort -u
    )"
    if [ "$archiveOwners" != "0/0" ]; then
      echo "libonnxruntime.a contains nondeterministic member owners: $archiveOwners" >&2
      exit 1
    fi

    archiveArchitectures="$(${cctools}/bin/lipo -archs "$archive")"
    if [ "$archiveArchitectures" != "arm64" ]; then
      echo "expected arm64-only libonnxruntime.a, found: $archiveArchitectures" >&2
      exit 1
    fi
    if ! ${cctools}/bin/nm -gU "$archive" | grep -E '(^|[[:space:]])_OrtGetApiBase$' >/dev/null; then
      echo "libonnxruntime.a does not export _OrtGetApiBase" >&2
      exit 1
    fi

    archiveStrings="$TMPDIR/buzz-onnxruntime-archive.strings"
    ${stdenv.cc.bintools}/bin/strings -a "$archive" > "$archiveStrings"
    if grep -F "$NIX_BUILD_TOP/" "$archiveStrings" >/dev/null \
      || grep -F '/nix/var/nix/builds/' "$archiveStrings" >/dev/null; then
      echo "libonnxruntime.a contains an ephemeral Nix build path" >&2
      exit 1
    fi
    if grep -F '/source-store/' "$archiveStrings" >/dev/null; then
      echo "libonnxruntime.a contains a hash-preserving normalized store path" >&2
      exit 1
    fi
    storeRoots="$(
      grep -Eo '/nix/store/[^/[:space:]]+' \
        "$archiveStrings" |
        LC_ALL=C sort -u || true
    )"
    if [ -n "$storeRoots" ]; then
      echo "libonnxruntime.a contains Nix store references:" >&2
      printf '%s\n' "$storeRoots" >&2
      exit 1
    fi

    projectUndefined="$(
      ${cctools}/bin/nm -u "$archive" |
        ${stdenv.cc.bintools}/bin/c++filt |
        grep -E '(^|[[:space:]])(onnxruntime::|onnx::|google::protobuf::|absl::|re2::|flatbuffers::|_?utf8_range_)' || true
    )"
    if [ -n "$projectUndefined" ]; then
      echo "libonnxruntime.a retains undefined project symbols:" >&2
      printf '%s\n' "$projectUndefined" >&2
      exit 1
    fi

    smokeSource="$TMPDIR/ort-link-smoke.cc"
    smokeBinary="$TMPDIR/ort-link-smoke"
    printf '%s\n' '#include <onnxruntime_c_api.h>' \
      'int main() {' \
      '  const OrtApiBase* base = OrtGetApiBase();' \
      '  return base && base->GetApi(ORT_API_VERSION) ? 0 : 1;' \
      '}' > "$smokeSource"
    "$CXX" -std=c++17 -I"$dev/include" "$smokeSource" \
      -Wl,-force_load,"$archive" -Wl,-undefined,error \
      -framework CoreFoundation -framework Foundation \
      -o "$smokeBinary"

    if [ -e "$dev/lib/cmake" ] || [ -e "$dev/lib/pkgconfig" ]; then
      echo "ONNX Runtime dev output contains misleading dynamic-library metadata" >&2
      exit 1
    fi

    if find "$out" "$dev" \
      \( \
        -name '*.framework' -o \
        -name '*.dylib' -o -name '*.dylib.*' -o -name '*.so' -o -name '*.so.*' \
      \) \
      -print -quit | grep -q .; then
      echo "ONNX Runtime output contains a framework or dynamic library" >&2
      exit 1
    fi
  '';

  passthru = (old.passthru or { }) // {
    buzzNativeContract = {
      kind = "onnxruntime";
      version = "1.27.0";
      commit = "8f0278c77bf44b0cc83c098c6c722b92a36ac4b5";
      target = "aarch64-apple-darwin";
      configuration = "Release";
      assemblyBuildSharedLib = true;
      assemblyBuildAppleFramework = true;
      deliveredSharedLib = false;
      deliveredAppleFramework = false;
      skipTests = true;
      monolithicStaticArchive = true;
    };
  };

  meta = (old.meta or { }) // {
    description = "Static ONNX Runtime foundation for Buzz";
    changelog = "https://github.com/microsoft/onnxruntime/releases/tag/v${version}";
    platforms = [ "aarch64-darwin" ];
    sourceProvenance = with lib.sourceTypes; [ fromSource ];
  };
})
