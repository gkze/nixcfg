{
  cmake,
  lib,
  ninja,
  src,
  stdenv,
  version,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
stdenv.mkDerivation {
  pname = "unsloth-whisper-cpp";
  inherit src version;
  strictDeps = true;

  nativeBuildInputs = [
    cmake
    ninja
  ];

  cmakeBuildType = "Release";
  cmakeFlags = [
    (lib.cmakeBool "BUILD_SHARED_LIBS" false)
    (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
    (lib.cmakeFeature "CMAKE_OSX_DEPLOYMENT_TARGET" "13.3")
    (lib.cmakeBool "GGML_BACKEND_DL" false)
    (lib.cmakeBool "GGML_METAL" true)
    (lib.cmakeBool "GGML_METAL_EMBED_LIBRARY" true)
    (lib.cmakeBool "GGML_NATIVE" false)
    (lib.cmakeBool "WHISPER_BUILD_EXAMPLES" true)
    (lib.cmakeBool "WHISPER_BUILD_SERVER" true)
    (lib.cmakeBool "WHISPER_BUILD_TESTS" false)
    (lib.cmakeBool "WHISPER_CURL" false)
    (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
  ];

  buildPhase = ''
    runHook preBuild
    cmake --build . --parallel "$NIX_BUILD_CORES" --target whisper-server
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin"
    install -m0755 bin/whisper-server "$out/bin/whisper-server"
    ln -s bin/whisper-server "$out/whisper-server"
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    /usr/bin/lipo "$out/bin/whisper-server" -verify_arch arm64
    "$out/bin/whisper-server" --help >/dev/null
    runHook postInstallCheck
  '';

  meta = {
    description = "Exact Unsloth whisper.cpp helper closure";
    license = lib.licenses.mit;
    platforms = [ "aarch64-darwin" ];
  };
}
