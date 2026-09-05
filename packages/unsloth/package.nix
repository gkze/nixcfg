{
  callPackage,
  artifactValidation ? builtins.fromJSON (builtins.readFile ./artifact-validation.json),
  closureHashes ? builtins.fromJSON (builtins.readFile ./closure-hashes.json),
  closurePlan ? builtins.fromJSON (builtins.readFile ./closure-plan.json),
  fetchFromGitHub,
  fetchurl,
  gnutar,
  inputs,
  lib,
  makeRustPlatform,
  nodejs_24,
  pkgs,
  pythonWorkspaceRoot ? ./.,
  runCommand,
  selfSource ? builtins.fromJSON (builtins.readFile ./sources.json),
  stdenv,
  stdenvNoCC,
  ...
}:
assert stdenv.hostPlatform.system == "aarch64-darwin";
let
  source = selfSource;
  runtimeSources = builtins.fromJSON (builtins.readFile ./runtime-sources.json);

  inherit (source) version;
  backendVersion = closurePlan.backend.version;
  hashEntryFor =
    hashType: url:
    lib.findFirst (
      entry: entry.hashType == hashType && (url == null || (entry.url or null) == url)
    ) null source.hashes;
  desktopSourceHash = (hashEntryFor "srcHash" null).hash;
  backendSourceHash = (hashEntryFor "sha256" source.urls.backendSdist).hash;
  hashOrFake = value: if value == null then lib.fakeHash else value;

  desktopSource = fetchFromGitHub {
    owner = "unslothai";
    repo = "unsloth";
    rev = source.commit;
    fetchSubmodules = false;
    hash = desktopSourceHash;
  };
  backendSource = fetchurl {
    url = source.urls.backendSdist;
    hash = backendSourceHash;
  };

  nodejs = nodejs_24;
  nodeVersion = lib.getVersion nodejs;
  nodeRuntimeContract =
    runCommand "unsloth-node-${nodeVersion}-runtime-contract"
      {
        nativeBuildInputs = [ nodejs ];
        passthru = {
          expectedVersion = nodeVersion;
          inherit nodejs;
        };
      }
      ''
        test -x ${nodejs}/bin/node
        test -x ${nodejs}/bin/npm
        test -x ${nodejs}/bin/npx

        actualNodeVersion="$(${nodejs}/bin/node --version)"
        test "$actualNodeVersion" = "v${nodeVersion}"
        actualNpmVersion="$(${nodejs}/bin/npm --version)"
        test -n "$actualNpmVersion"
        actualNpxVersion="$(${nodejs}/bin/npx --version)"
        test -n "$actualNpxVersion"

        mkdir -p "$out"
        printf '%s\n' "$actualNodeVersion" > "$out/node-version"
        printf '%s\n' "$actualNpmVersion" > "$out/npm-version"
        printf '%s\n' "$actualNpxVersion" > "$out/npx-version"
      '';
  llamaCppSource = fetchurl {
    url = runtimeSources.llamaCpp.url;
    hash = runtimeSources.llamaCpp.hash;
  };
  whisperCppSource = fetchurl {
    url = runtimeSources.whisperCpp.url;
    hash = runtimeSources.whisperCpp.hash;
  };

  stableDiffusionMainSource = fetchurl {
    url = runtimeSources.stableDiffusionCpp.url;
    hash = runtimeSources.stableDiffusionCpp.hash;
  };
  stableDiffusionGgmlSource = fetchurl {
    url = runtimeSources.stableDiffusionCpp.submodules.ggml.url;
    hash = runtimeSources.stableDiffusionCpp.submodules.ggml.hash;
  };
  stableDiffusionFrontendSource = fetchurl {
    url = runtimeSources.stableDiffusionCpp.submodules."examples/server/frontend".url;
    hash = runtimeSources.stableDiffusionCpp.submodules."examples/server/frontend".hash;
  };
  stableDiffusionLibwebmSource = fetchurl {
    url = runtimeSources.stableDiffusionCpp.submodules."thirdparty/libwebm".url;
    hash = runtimeSources.stableDiffusionCpp.submodules."thirdparty/libwebm".hash;
  };
  stableDiffusionLibwebpSource = fetchurl {
    url = runtimeSources.stableDiffusionCpp.submodules."thirdparty/libwebp".url;
    hash = runtimeSources.stableDiffusionCpp.submodules."thirdparty/libwebp".hash;
  };
  stableDiffusionSource =
    runCommand "unsloth-stable-diffusion-cpp-source-${runtimeSources.stableDiffusionCpp.commit}"
      {
        nativeBuildInputs = [ gnutar ];
      }
      ''
        mkdir -p "$out"
        tar -xzf ${stableDiffusionMainSource} --strip-components=1 -C "$out"

        replaceSubmodule() {
          destination="$1"
          archive="$2"
          rm -rf "$out/$destination"
          mkdir -p "$out/$destination"
          tar -xzf "$archive" --strip-components=1 -C "$out/$destination"
        }

        replaceSubmodule ggml ${stableDiffusionGgmlSource}
        replaceSubmodule examples/server/frontend ${stableDiffusionFrontendSource}
        replaceSubmodule thirdparty/libwebm ${stableDiffusionLibwebmSource}
        replaceSubmodule thirdparty/libwebp ${stableDiffusionLibwebpSource}

        test -f "$out/CMakeLists.txt"
        test -f "$out/ggml/CMakeLists.txt"
        test -f "$out/examples/server/frontend/package.json"
        test -f "$out/thirdparty/libwebm/CMakeLists.txt"
        test -f "$out/thirdparty/libwebp/CMakeLists.txt"
      '';

  frontend = callPackage ./frontend.nix {
    src = desktopSource;
    inherit nodejs version;
    npmDepsHash = hashOrFake closureHashes.frontendNpmDepsHash;
  };
  oxcNodeModules = callPackage ./oxc-node-modules.nix {
    src = desktopSource;
    inherit nodejs version;
    npmDepsHash = hashOrFake closureHashes.oxcNpmDepsHash;
  };
  llamaCpp = callPackage ./llama-cpp.nix {
    src = llamaCppSource;
    version = runtimeSources.llamaCpp.tag;
  };
  whisperCpp = callPackage ./whisper-cpp.nix {
    src = whisperCppSource;
    version = runtimeSources.whisperCpp.tag;
  };
  stableDiffusionCpp = callPackage ./stable-diffusion-cpp.nix {
    src = stableDiffusionSource;
    version = runtimeSources.stableDiffusionCpp.tag;
  };
  backend = callPackage ./backend.nix {
    backendSrc = backendSource;
    inherit
      backendVersion
      frontend
      inputs
      llamaCpp
      nodejs
      oxcNodeModules
      pythonWorkspaceRoot
      stableDiffusionCpp
      whisperCpp
      ;
  };

  cargoManifest = builtins.fromTOML (
    builtins.readFile "${desktopSource}/studio/src-tauri/Cargo.toml"
  );
  rustToolchainVersion = lib.versions.pad 3 cargoManifest.package.rust-version;
  rustToolchain = (inputs.rust-overlay.lib.mkRustBin { } pkgs).stable.${rustToolchainVersion}.default;
  exactRustPlatform = makeRustPlatform {
    cargo = rustToolchain;
    rustc = rustToolchain;
  };
  appCandidate = callPackage ./desktop.nix {
    inherit
      backend
      frontend
      rustToolchain
      version
      ;
    cargoHash = hashOrFake closureHashes.cargoHash;
    rustPlatform = exactRustPlatform;
    src = desktopSource;
  };
  storePathSmokePython = ''
    import http.client
    import json
    import os
    import re
    import subprocess
    import sys
    import time
    from pathlib import Path

    backend_executable = Path(sys.argv[1])
    backend_runtime_entrypoint = Path(sys.argv[2])
    evidence_path = Path(sys.argv[3])
    log_path = Path(sys.argv[4])
    home_path = Path(sys.argv[5])
    home_path.mkdir(parents=True)

    environment = os.environ.copy()
    environment.pop("SSL_CERT_DIR", None)
    environment.pop("SSL_CERT_FILE", None)
    environment["HOME"] = str(home_path)
    environment["UNSLOTH_STUDIO_DISABLE_PUBLIC_CHECK"] = "1"
    environment.pop("UNSLOTH_STUDIO_HOME", None)
    environment.pop("STUDIO_HOME", None)
    required_health = {
        "service": "Unsloth UI Backend",
        "status": "healthy",
    }

    def startup_log() -> str:
        try:
            return log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "<backend log unavailable>"

    with log_path.open("w", encoding="utf-8") as log_stream:
        process = subprocess.Popen(
            [
                str(backend_executable),
                "studio",
                "--api-only",
                "-H",
                "127.0.0.1",
                "-p",
                "0",
            ],
            env=environment,
            stderr=subprocess.STDOUT,
            stdout=log_stream,
            text=True,
        )

    health = None
    port = None
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"packaged backend exited with {return_code} before health\n"
                    f"{startup_log()}"
                )

            matches = re.findall(r"^TAURI_PORT=(\d+)$", startup_log(), re.MULTILINE)
            if matches:
                port = int(matches[-1])
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                try:
                    connection.request("GET", "/api/health")
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                except (OSError, http.client.HTTPException, json.JSONDecodeError):
                    payload = None
                finally:
                    connection.close()
                if (
                    isinstance(payload, dict)
                    and all(payload.get(key) == value for key, value in required_health.items())
                ):
                    health = payload
                    break
            time.sleep(0.25)
        else:
            raise RuntimeError(
                "packaged backend did not become healthy within 180 seconds\n"
                f"{startup_log()}"
            )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    if health is None or port is None:
        raise RuntimeError("packaged backend health evidence was not captured")
    evidence_path.write_text(
        json.dumps(
            {
                "backendExecutable": str(backend_executable),
                "backendRuntimeEntrypoint": str(backend_runtime_entrypoint),
                "health": health,
                "port": port,
                "startup": "passed",
                "teardown": "passed",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
  '';
  storePathAppCandidateSmoke =
    runCommand "unsloth-${version}-store-path-app-candidate-smoke"
      {
        __darwinAllowLocalNetworking = true;
        passthru = {
          artifactCheck = "store-path-app-candidate-backend-smoke";
          inherit appCandidate backend;
        };
      }
      ''
        app="${appCandidate}/Applications/Unsloth.app"
        executable="$app/Contents/MacOS/unsloth-studio"
        backendExecutable="${backend}/bin/unsloth"
        backendRuntimeEntrypoint="${backend.venv}/bin/unsloth"

        test -d "$app"
        test -x "$executable"
        test -x "$backendExecutable"
        test -x "$backendRuntimeEntrypoint"
        test -L "${appCandidate}/bin/unsloth-studio"
        ${stdenv.cc.bintools}/bin/strings -a "$executable" | \
          grep -F "$backendExecutable" >/dev/null

        ${backend.venv}/bin/python -c ${lib.escapeShellArg storePathSmokePython} \
          "$backendExecutable" \
          "$backendRuntimeEntrypoint" \
          "$TMPDIR/backend-health.json" \
          "$TMPDIR/backend-startup.log" \
          "$TMPDIR/backend-home"

        mkdir -p "$out"
        ln -s "${appCandidate}" "$out/app-candidate"
        printf '%s\n' "$backendExecutable" > "$out/backend-path"
        printf '%s\n' "$backendRuntimeEntrypoint" \
          > "$out/backend-runtime-entrypoint-path"
        cp "$TMPDIR/backend-health.json" "$out/backend-health.json"
      '';

  smokeEvidenceComplete =
    (artifactValidation.storePathAppCandidateSmokeOutput or null) == "${storePathAppCandidateSmoke}";
  runtimeEvidence = artifactValidation.runtimeEvidence or null;
  runtimeEvidenceComplete =
    builtins.isAttrs runtimeEvidence
    && (artifactValidation.runtimeEvidenceSchemaVersion or null) == 3
    && (runtimeEvidence.schemaVersion or null) == 3
    && (runtimeEvidence.status or null) == "passed"
    && (runtimeEvidence.teardown or null) == "passed"
    && (runtimeEvidence.sandbox or null) == "passed"
    && (runtimeEvidence.listenerOwnership or null) == "passed"
    && (runtimeEvidence.appCandidate or null) == "${appCandidate}"
    && (runtimeEvidence.backendExecutable or null) == "${backend}/bin/unsloth"
    && (runtimeEvidence.backendRuntimeEntrypoint or null) == "${backend.venv}/bin/unsloth"
    && builtins.isAttrs (runtimeEvidence.health or null)
    &&
      builtins.attrNames runtimeEvidence.health == [
        "service"
        "status"
      ]
    && (runtimeEvidence.health.service or null) == "Unsloth UI Backend"
    && (runtimeEvidence.health.status or null) == "healthy"
    && (runtimeEvidence.studioRootIdentity or null) == "passed";
  closureIdentityComplete =
    (closurePlan.app.version or null) == version
    && (closurePlan.app.tag or null) == "v${version}"
    && (closurePlan.app.commit or null) == source.commit
    && (closurePlan.app.sourceHash or null) == desktopSourceHash
    && (closurePlan.backend.version or null) == backendVersion
    && (closurePlan.backend.sdistHash or null) == backendSourceHash
    && (closurePlan.releaseManifest.version or null) == version
    && (closurePlan.releaseManifest.pypiVersion or null) == backendVersion
    &&
      (closurePlan.releaseManifest.hash or null) == (hashEntryFor "sha256" source.urls.releaseManifest)
      .hash;
  closureStateAllowsExport =
    (closurePlan.status == "ready-for-promotion" && closurePlan.packageExported == false)
    || (closurePlan.status == "exported-and-validated" && closurePlan.packageExported == true);
  unresolvedBuildGates =
    lib.optional (closureHashes.oxcNpmDepsHash == null) "oxcNpmDepsHash"
    ++ lib.optional (closureHashes.frontendNpmDepsHash == null) "frontendNpmDepsHash"
    ++ lib.optional (closureHashes.cargoHash == null) "cargoHash"
    ++ lib.optional (artifactValidation.status != "passed") "artifact-validation"
    ++ lib.optional (!smokeEvidenceComplete) "store-path-smoke-evidence"
    ++ lib.optional (!runtimeEvidenceComplete) "runtime-evidence"
    ++ lib.optional (!closureIdentityComplete) "closure-plan-identity"
    ++ lib.optional (closurePlan.blockers != [ ]) "closure-plan-blockers"
    ++ lib.optional (!closureStateAllowsExport) "closure-plan-status";
  exportReady = unresolvedBuildGates == [ ];
  closurePassthru = {
    inherit
      appCandidate
      artifactValidation
      backend
      desktopSource
      exportReady
      frontend
      llamaCpp
      nodejs
      nodeRuntimeContract
      oxcNodeModules
      stableDiffusionCpp
      stableDiffusionSource
      storePathAppCandidateSmoke
      unresolvedBuildGates
      whisperCpp
      ;
    inherit closureHashes;
  };
  validatedPackage = appCandidate.overrideAttrs (old: {
    passthru = (old.passthru or { }) // closurePassthru;
  });
  blockedPackage = stdenvNoCC.mkDerivation {
    pname = "unsloth-unvalidated";
    inherit version;
    dontUnpack = true;
    buildPhase = ''
      echo "Unsloth remains unexported until these gates close:" >&2
      printf '  %s\n' ${lib.escapeShellArgs unresolvedBuildGates} >&2
      exit 1
    '';
    installPhase = "mkdir -p $out";
    passthru = closurePassthru;
    meta = {
      description = "Build-gated Unsloth Studio source migration";
      platforms = [ "aarch64-darwin" ];
    };
  };
in
if exportReady then validatedPackage else blockedPackage
