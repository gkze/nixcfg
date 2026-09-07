{
  cctools,
  cmake,
  fetchFromGitHub,
  gitMinimal,
  lib,
  llamaCppSrcHash,
  meshLlmSrcHash,
  nativeLock ? builtins.fromJSON (builtins.readFile ../native-lock.json),
  ninja,
  python3,
  stdenv,
  stdenvNoCC,
}:
assert stdenvNoCC.hostPlatform.system == "aarch64-darwin";
let
  inspectionPython = python3.withPackages (ps: [ ps.macholib ]);
  inspectionSource = lib.fileset.toSource {
    root = ../../..;
    fileset = lib.fileset.unions [
      ../../../lib/__init__.py
      ../../../lib/macho.py
    ];
  };
  meshLlmLock = nativeLock.meshLlm or { };
  llamaCppLock = nativeLock.llamaCpp or { };
  meshLlmVersion = meshLlmLock.version or null;
  meshLlmCommit = meshLlmLock.commit or null;
  skippyAbi = meshLlmLock.skippyAbi or null;
  llamaCppCommit = llamaCppLock.commit or null;
  meshLlm = import ./mesh-llm.nix {
    inherit
      fetchFromGitHub
      lib
      nativeLock
      python3
      stdenvNoCC
      ;
    srcHash = meshLlmSrcHash;
  };
  llamaCpp = import ./llama-cpp.nix {
    inherit
      cctools
      cmake
      fetchFromGitHub
      gitMinimal
      lib
      nativeLock
      ninja
      stdenv
      ;
    meshSrcHash = meshLlmSrcHash;
    srcHash = llamaCppSrcHash;
  };
  expectedMeshContract = {
    kind = "mesh-llm";
    version = meshLlmVersion;
    commit = meshLlmCommit;
    sdkFeatures = [
      "client"
      "serving"
    ];
    hostRuntimeFeatures = [ "dynamic-native-runtime" ];
  };
  expectedLlamaContract = {
    kind = "llama.cpp";
    commit = llamaCppCommit;
    target = "aarch64-apple-darwin";
    backend = "metal";
    linkMode = "dynamic";
    buildType = "Release";
    ggmlNative = false;
    cmakeOptions = {
      BUILD_SHARED_LIBS = true;
      GGML_METAL = true;
      LLAMA_BUILD_APP = false;
      LLAMA_BUILD_EXAMPLES = false;
      LLAMA_BUILD_SERVER = false;
      LLAMA_BUILD_TESTS = false;
      LLAMA_CURL = false;
      LLAMA_OPENSSL = false;
    };
  };
  implementedBundleContract = {
    kind = "mesh-native-runtime-bundle";
    meshVersion = meshLlmVersion;
    inherit skippyAbi;
    target = "aarch64-apple-darwin";
    platform = {
      os = "macos";
      arch = "aarch64";
    };
    backend = "metal";
    sourceInputs = [
      "meshLlm"
      "llamaCpp"
    ];
    manifestHasFileDigests = true;
    releaseArchiveAllowed = false;
  };
  runtimeId = "meshllm-native-runtime-darwin-aarch64-metal";
in
assert builtins.isString meshLlmVersion;
assert builtins.isString meshLlmCommit && builtins.match "[0-9a-f]{40}" meshLlmCommit != null;
assert builtins.isString skippyAbi && builtins.match "[0-9]+\\.[0-9]+\\.[0-9]+" skippyAbi != null;
assert builtins.isString llamaCppCommit && builtins.match "[0-9a-f]{40}" llamaCppCommit != null;
assert (meshLlm.passthru.buzzNativeContract or null) == expectedMeshContract;
assert (meshLlm.sourceSubdir or null) == "share/mesh-llm/source";
assert (meshLlm.provenanceSubpath or null) == "share/mesh-llm/provenance.json";
assert (llamaCpp.passthru.buzzNativeContract or null) == expectedLlamaContract;
assert (llamaCpp.libSubdir or null) == "lib";
assert builtins.isList (llamaCpp.resourceSubpaths or null);
stdenvNoCC.mkDerivation {
  pname = "buzz-mesh-native-runtime";
  version = meshLlmVersion;
  strictDeps = true;
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  nativeBuildInputs = [
    cctools
    inspectionPython
  ];
  installPhase = ''
    runHook preInstall
    export PYTHONPATH=${inspectionSource}
    ${inspectionPython}/bin/python3 ${./mesh_runtime_bundle.py} ${lib.escapeShellArg "${meshLlm}/${meshLlm.sourceSubdir}"} ${lib.escapeShellArg "${meshLlm}/${meshLlm.provenanceSubpath}"} ${lib.escapeShellArg "${llamaCpp}"} "$out" lib ${lib.escapeShellArg (builtins.toJSON llamaCpp.resourceSubpaths)} ${cctools}/bin/nm /usr/bin/codesign ${lib.escapeShellArg meshLlmVersion} ${lib.escapeShellArg meshLlmCommit} ${lib.escapeShellArg llamaCppCommit} ${lib.escapeShellArg skippyAbi}
    runHook postInstall
  '';
  passthru = {
    buzzNativeContract = implementedBundleContract;
    manifestSubpath = "manifest.json";
    inherit runtimeId;
  };
  meta = {
    description = "Repo-owned Mesh ${meshLlmVersion} Metal native runtime for Buzz";
    license = lib.licenses.asl20;
    platforms = [ "aarch64-darwin" ];
  };
}
