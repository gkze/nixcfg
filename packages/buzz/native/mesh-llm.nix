{
  fetchFromGitHub,
  lib,
  python3,
  srcHash,
  stdenvNoCC,
}:
let
  version = "0.75.1";
  commit = "3295c902d4c4f859aaadf9240042ffdaf06dd07e";
  sourceSubdir = "share/mesh-llm/source";
  provenanceSubpath = "share/mesh-llm/provenance.json";
  inventoryScript = ''
    import hashlib
    import json
    import sys
    from pathlib import Path

    source_root = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    version = sys.argv[3]
    commit = sys.argv[4]
    patch_directory = source_root / "third_party/llama.cpp/patches"
    patch_paths = sorted(patch_directory.glob("*.patch"), key=lambda path: path.name)
    if not patch_paths:
        raise SystemExit("Mesh source contains no llama.cpp patches")
    packaging_paths = (
        "scripts/build-llama.sh",
        "scripts/package-native-runtime.sh",
        "scripts/prepare-llama.sh",
        "third_party/llama.cpp/upstream.txt",
    )

    def sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    upstream_path = source_root / "third_party/llama.cpp/upstream.txt"
    inventory = {
        "schemaVersion": 1,
        "meshLlm": {
            "version": version,
            "commit": commit,
        },
        "llamaCpp": {
            "upstreamPin": upstream_path.read_text(encoding="utf-8").strip(),
            "patches": [
                {"name": path.name, "sha256": sha256(path)} for path in patch_paths
            ],
        },
        "packagingInputs": [
            {"path": relative_path, "sha256": sha256(source_root / relative_path)}
            for relative_path in packaging_paths
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
  '';
  src = fetchFromGitHub {
    owner = "Mesh-LLM";
    repo = "mesh-llm";
    rev = commit;
    hash = srcHash;
    fetchSubmodules = false;
  };
in
stdenvNoCC.mkDerivation {
  pname = "mesh-llm-source";
  inherit version src;
  strictDeps = true;
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  dontFixup = true;
  nativeBuildInputs = [ python3 ];
  installPhase = ''
    sourceOutput="$out/${sourceSubdir}"
    provenanceOutput="$out/${provenanceSubpath}"
    mkdir -p "$sourceOutput"
    cp -R "$src"/. "$sourceOutput"
    ${lib.getExe python3} -c ${lib.escapeShellArg inventoryScript} "$sourceOutput" "$provenanceOutput" ${lib.escapeShellArg version} ${lib.escapeShellArg commit}
  '';
  passthru = {
    inherit sourceSubdir;
    inherit provenanceSubpath;
    buzzNativeContract = {
      kind = "mesh-llm";
      inherit version commit;
      sdkFeatures = [
        "client"
        "serving"
      ];
      hostRuntimeFeatures = [ "dynamic-native-runtime" ];
    };
  };
}
