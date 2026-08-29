{
  backendSrc,
  backendVersion,
  callPackage,
  inputs,
  lib,
  llamaCpp,
  makeWrapper,
  mkResolvedBuildSystemsOverlay,
  nodejs,
  oxcNodeModules,
  python312,
  runCommand,
  stableDiffusionCpp,
  stdenv,
  frontend,
  whisperCpp,
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  workspace = inputs.uv2nix.lib.workspace.loadWorkspace {
    workspaceRoot = lib.fileset.toSource {
      root = ./.;
      fileset = lib.fileset.unions [
        ./pyproject.toml
        ./uv.lock
      ];
    };
  };
  pySet =
    (callPackage inputs.pyproject-nix.build.packages {
      python = python312;
    }).overrideScope
      (
        lib.composeManyExtensions [
          inputs.pyproject-build-systems.overlays.default
          (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
          (mkResolvedBuildSystemsOverlay {
            nixcfg-unsloth-runtime = {
              setuptools = [ ];
            };
            unsloth = {
              setuptools = [ ];
              setuptools-scm = [ ];
            };
          })
          (_pyFinal: pyPrev: {
            unsloth = pyPrev.unsloth.overrideAttrs (old: {
              src = backendSrc;
              postPatch = (old.postPatch or "") + ''
                PYTHONPATH=${
                  lib.fileset.toSource {
                    root = ../..;
                    fileset = lib.fileset.unions [
                      ../../lib/__init__.py
                      ../../lib/exact_text_patch.py
                    ];
                  }
                } ${lib.getExe python312} ${./patch_nix_managed.py} "$PWD" \
                  --backend-root "$PWD"

                rm -rf studio/frontend/dist
                mkdir -p studio/frontend/dist
                cp -R ${frontend}/dist/. studio/frontend/dist/

                validator=studio/backend/core/data_recipe/oxc-validator
                rm -rf "$validator/node_modules"
                cp -R ${oxcNodeModules}/node_modules "$validator/node_modules"
                chmod -R u+w studio/frontend/dist "$validator/node_modules"
              '';
            });
          })
        ]
      );
  venv = pySet.mkVirtualEnv "unsloth-${backendVersion}-venv" workspace.deps.all;
  oxcSmokeAudit = ''
    from collections.abc import Mapping
    import json
    import sys

    def require_single_result(payload, label):
        if not isinstance(payload, list) or len(payload) != 1:
            raise SystemExit(f"{label} OXC result must be a one-element list")
        if not isinstance(payload[0], Mapping):
            raise SystemExit(f"{label} OXC result item must be a mapping")

    with open(sys.argv[1], encoding="utf-8") as stream:
        valid = json.load(stream)
    with open(sys.argv[2], encoding="utf-8") as stream:
        invalid = json.load(stream)
    require_single_result(valid, "valid")
    require_single_result(invalid, "invalid")
    if valid[0]["is_valid"] is not True or invalid[0]["is_valid"] is not False:
        raise SystemExit("OXC validator did not distinguish valid and invalid input")
  '';
  backendVersionAudit = ''
    import importlib.metadata

    if importlib.metadata.version("unsloth") != "${backendVersion}":
        raise SystemExit("unexpected Unsloth backend version")
  '';
  desktopCapabilitiesAudit = ''
    import json
    import sys

    with open(sys.argv[1], encoding="utf-8") as stream:
        payload = json.load(stream)
    expected = {
        "desktop_manageability_version": 2,
        "desktop_protocol_version": 1,
        "studio_install_ok": True,
        "studio_install_reason": None,
        "supports_api_only": True,
        "supports_desktop_backend_ownership": True,
        "supports_provision_desktop_auth": True,
        "version": "${backendVersion}",
    }
    if payload != expected:
        raise SystemExit(
            f"desktop-capabilities contract mismatch: {payload!r}"
        )
  '';
  runtimeEnvironment = {
    DG_VISUAL_BIN = "${llamaCpp}/bin/llama-diffusion-gemma-visual-server";
    LLAMA_SERVER_PATH = "${llamaCpp}/bin/llama-server";
    SD_CLI_PATH = "${stableDiffusionCpp}/bin/sd-cli";
    SD_SERVER_PATH = "${stableDiffusionCpp}/bin/sd-server";
    UNSLOTH_DIFFUSION_ATTENTION_INSTALL = "0";
    UNSLOTH_DIFFUSION_SD_CPP_INSTALL = "0";
    UNSLOTH_DISABLE_LLMCOMPRESSOR_MAIN = "1";
    UNSLOTH_DISABLE_LLM_COMPRESSOR_AUTOINSTALL = "1";
    UNSLOTH_DISABLE_MLX_AUTOREPAIR = "1";
    UNSLOTH_DISABLE_UPDATE_CHECK = "1";
    UNSLOTH_LLAMA_CPP_PATH = "${llamaCpp}";
    UNSLOTH_LLAMA_CPP_SCRIPTS_DIR = "${llamaCpp}";
    UNSLOTH_NIX_MANAGED = "1";
    UNSLOTH_SD_CPP_PATH = "${stableDiffusionCpp}";
    UNSLOTH_SKIP_NODE_INSTALL = "1";
    UNSLOTH_STUDIO_SKIP_FAST_PATH_HOOKS = "1";
    UNSLOTH_STUDIO_SKIP_FLASHATTN_INSTALL = "1";
    UNSLOTH_STUDIO_SKIP_FLA_INSTALL = "1";
    UNSLOTH_STUDIO_SKIP_TILELANG_INSTALL = "1";
    UNSLOTH_WHISPER_CPP_PATH = "${whisperCpp}";
    WHISPER_SERVER_PATH = "${whisperCpp}/bin/whisper-server";
  };
  wrapperFlags = lib.concatLists (
    lib.mapAttrsToList (name: value: [
      "--set"
      name
      value
    ]) runtimeEnvironment
  );
in
runCommand "unsloth-backend-${backendVersion}"
  {
    nativeBuildInputs = [ makeWrapper ];
    passthru = {
      inherit runtimeEnvironment venv;
      pythonSet = pySet;
    };
    meta = {
      description = "Nix-owned Unsloth Studio Python backend";
      license = lib.licenses.asl20;
      mainProgram = "unsloth";
      platforms = [ "aarch64-darwin" ];
    };
  }
  ''
    mkdir -p "$out/bin"
    makeWrapper ${venv}/bin/unsloth "$out/bin/unsloth" \
      --prefix PATH : ${
        lib.makeBinPath [
          nodejs
          llamaCpp
          whisperCpp
          stableDiffusionCpp
        ]
      } \
      ${lib.escapeShellArgs wrapperFlags}

    validator="${venv}/${python312.sitePackages}/studio/backend/core/data_recipe/oxc-validator/validate.mjs"
    test -f "$validator"
    test -d "$(dirname "$validator")/node_modules"

    printf '%s\n' \
      '{"codes":["const value = 1;"],"lang":"js","mode":"syntax+lint","code_shape":"module"}' |
      ${lib.getExe nodejs} "$validator" > "$TMPDIR/oxc-valid.json"
    printf '%s\n' \
      '{"codes":["const = ;"],"lang":"js","mode":"syntax+lint","code_shape":"module"}' |
      ${lib.getExe nodejs} "$validator" > "$TMPDIR/oxc-invalid.json"
    ${venv}/bin/python -c ${lib.escapeShellArg oxcSmokeAudit} \
      "$TMPDIR/oxc-valid.json" "$TMPDIR/oxc-invalid.json"

    ${venv}/bin/python -c ${lib.escapeShellArg backendVersionAudit}

    "$out/bin/unsloth" studio desktop-capabilities --json \
      > "$TMPDIR/desktop-capabilities.json"
    ${venv}/bin/python -c ${lib.escapeShellArg desktopCapabilitiesAudit} \
      "$TMPDIR/desktop-capabilities.json"
    "$out/bin/unsloth" --help >/dev/null
  ''
