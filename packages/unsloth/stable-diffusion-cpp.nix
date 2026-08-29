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
  pname = "unsloth-stable-diffusion-cpp";
  inherit src version;
  strictDeps = true;

  nativeBuildInputs = [
    cmake
    ninja
  ];

  cmakeBuildType = "Release";
  cmakeFlags = [
    (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
    (lib.cmakeFeature "CMAKE_OSX_DEPLOYMENT_TARGET" "13.3")
    (lib.cmakeBool "GGML_BACKEND_DL" false)
    (lib.cmakeBool "GGML_METAL_EMBED_LIBRARY" true)
    (lib.cmakeBool "GGML_NATIVE" false)
    (lib.cmakeBool "SD_BUILD_EXAMPLES" true)
    (lib.cmakeBool "SD_BUILD_SHARED_LIBS" false)
    (lib.cmakeBool "SD_BUILD_SHARED_GGML_LIB" false)
    (lib.cmakeBool "SD_METAL" true)
    (lib.cmakeBool "SD_SERVER_BUILD_FRONTEND" false)
    (lib.cmakeBool "SD_WEBM" true)
    (lib.cmakeBool "SD_WEBP" true)
    (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
  ];

  buildPhase = ''
    runHook preBuild
    cmake --build . --parallel "$NIX_BUILD_CORES" --target sd-cli sd-server
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin"
    install -m0755 bin/sd-cli "$out/bin/sd-cli"
    install -m0755 bin/sd-server "$out/bin/sd-server"
    ln -s bin/sd-cli "$out/sd-cli"
    ln -s bin/sd-server "$out/sd-server"
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    /usr/bin/lipo "$out/bin/sd-cli" -verify_arch arm64
    /usr/bin/lipo "$out/bin/sd-server" -verify_arch arm64
    "$out/bin/sd-cli" --help >/dev/null
    "$out/bin/sd-server" --help >/dev/null
    runHook postInstallCheck
  '';

  meta = {
    description = "Exact Unsloth stable-diffusion.cpp helper closure";
    license = lib.licenses.mit;
    platforms = [ "aarch64-darwin" ];
  };
}
