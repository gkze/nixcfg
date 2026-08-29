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
  pname = "unsloth-llama-cpp";
  inherit src version;
  strictDeps = true;

  nativeBuildInputs = [
    cmake
    ninja
  ];

  cmakeBuildType = "Release";
  cmakeFlags = [
    (lib.cmakeBool "BUILD_SHARED_LIBS" false)
    (lib.cmakeBool "CMAKE_BUILD_WITH_INSTALL_RPATH" true)
    (lib.cmakeFeature "CMAKE_INSTALL_RPATH" "@loader_path")
    (lib.cmakeFeature "CMAKE_OSX_ARCHITECTURES" "arm64")
    (lib.cmakeFeature "CMAKE_OSX_DEPLOYMENT_TARGET" "13.3")
    (lib.cmakeBool "GGML_BACKEND_DL" false)
    (lib.cmakeBool "GGML_METAL" true)
    (lib.cmakeBool "GGML_METAL_EMBED_LIBRARY" true)
    (lib.cmakeBool "GGML_METAL_USE_BF16" true)
    (lib.cmakeBool "GGML_NATIVE" false)
    (lib.cmakeBool "LLAMA_BUILD_EXAMPLES" true)
    (lib.cmakeBool "LLAMA_BUILD_SERVER" true)
    (lib.cmakeBool "LLAMA_BUILD_TESTS" false)
    (lib.cmakeBool "LLAMA_BUILD_UI" false)
    (lib.cmakeBool "LLAMA_OPENSSL" false)
    (lib.cmakeBool "LLAMA_USE_PREBUILT_UI" false)
    (lib.cmakeBool "FETCHCONTENT_FULLY_DISCONNECTED" true)
  ];

  buildPhase = ''
    runHook preBuild
    cmake --build . --parallel "$NIX_BUILD_CORES" --target \
      llama-diffusion-gemma-visual-server \
      llama-quantize \
      llama-server
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/bin"
    for binary in \
      llama-diffusion-gemma-visual-server \
      llama-quantize \
      llama-server
    do
      install -m0755 "bin/$binary" "$out/bin/$binary"
    done
    install -m0644 "$NIX_BUILD_TOP/$sourceRoot/convert_hf_to_gguf.py" \
      "$out/convert_hf_to_gguf.py"
    ln -s bin/llama-quantize "$out/llama-quantize"
    ln -s bin/llama-server "$out/llama-server"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    for binary in "$out"/bin/*; do
      /usr/bin/lipo "$binary" -verify_arch arm64
    done
    set +e
    quantizeHelp="$("$out/bin/llama-quantize" --help 2>&1)"
    quantizeStatus=$?
    set -e
    test "$quantizeStatus" -eq 1
    [[ "$quantizeHelp" == *usage:* ]]
    "$out/bin/llama-server" --help >/dev/null
    test -f "$out/convert_hf_to_gguf.py"
    runHook postInstallCheck
  '';

  meta = {
    description = "Exact Unsloth llama.cpp helper closure";
    license = lib.licenses.mit;
    platforms = [ "aarch64-darwin" ];
  };
}
