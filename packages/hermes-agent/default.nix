{
  callPackage,
  coreutils,
  ffmpeg,
  git,
  inputs,
  lib,
  makeWrapper,
  nodejs_22,
  openssh,
  python312,
  ripgrep,
  stdenv,
  tirith,
  ...
}:
let
  inherit (builtins)
    fromTOML
    pathExists
    readFile
    ;

  inherit (stdenv.hostPlatform) system;
  upstreamPackage = inputs.hermes-agent.packages.${system}.default;
in
if !stdenv.hostPlatform.isDarwin then
  upstreamPackage
else
  let
    darwinExtras = [
      "acp"
      "bedrock"
      "cli"
      "cron"
      "daytona"
      "dingtalk"
      "feishu"
      "homeassistant"
      "honcho"
      "mcp"
      "mistral"
      "modal"
      "pty"
      "slack"
      "sms"
      "tts-premium"
      "web"
    ];
    darwinExtrasSpec = lib.concatStringsSep "," darwinExtras;
    hermesSource = inputs.hermes-agent;

    python312ForHermes = python312.override {
      packageOverrides = _pyFinal: pyPrev: {
        fsspec = pyPrev.fsspec.overridePythonAttrs (old: {
          disabledTests = (old.disabledTests or [ ]) ++ [
            # Fails on the current nixpkgs revision with:
            # TypeError: cannot unpack non-iterable bool object.
            "test_expiry"
          ];
        });
      };
    };

    hermesVenv = callPackage (hermesSource + "/nix/python.nix") {
      inherit (inputs)
        pyproject-build-systems
        pyproject-nix
        uv2nix
        ;
      python312 = python312ForHermes;
      dependency-groups = darwinExtras;
    };

    npm-lockfile-fix = inputs.hermes-agent.inputs.npm-lockfile-fix.packages.${system}.default;
    hermesNpmLib = callPackage (hermesSource + "/nix/lib.nix") {
      inherit npm-lockfile-fix;
    };
    hermesTui = callPackage (hermesSource + "/nix/tui.nix") {
      inherit hermesNpmLib;
    };
    hermesWeb = callPackage (hermesSource + "/nix/web.nix") {
      inherit hermesNpmLib;
    };

    bundledSkills = lib.cleanSourceWith {
      src = hermesSource + "/skills";
      filter = path: _type: !(lib.hasInfix "/index-cache/" path);
    };

    runtimePath = lib.makeBinPath [
      nodejs_22
      ripgrep
      git
      openssh
      ffmpeg
      tirith
    ];

    pyproject = fromTOML (readFile (hermesSource + "/pyproject.toml"));
    pyprojectHash = builtins.hashString "sha256" (readFile (hermesSource + "/pyproject.toml"));
    uvLockHash =
      if pathExists (hermesSource + "/uv.lock") then
        builtins.hashString "sha256" (readFile (hermesSource + "/uv.lock"))
      else
        "none";
  in
  stdenv.mkDerivation {
    pname = "hermes-agent";
    inherit (pyproject.project) version;

    dontUnpack = true;
    dontBuild = true;
    nativeBuildInputs = [
      makeWrapper
      nodejs_22
    ];

    installPhase = ''
      runHook preInstall

      mkdir -p $out/share/hermes-agent $out/bin
      cp -r ${bundledSkills} $out/share/hermes-agent/skills
      cp -r ${hermesWeb} $out/share/hermes-agent/web_dist

      mkdir -p $out/ui-tui
      cp -r ${hermesTui}/lib/hermes-tui/* $out/ui-tui/

      ${lib.concatMapStringsSep "\n"
        (name: ''
          makeWrapper ${hermesVenv}/bin/${name} $out/bin/${name} \
            --suffix PATH : "${runtimePath}" \
            --set HERMES_BUNDLED_SKILLS $out/share/hermes-agent/skills \
            --set HERMES_WEB_DIST $out/share/hermes-agent/web_dist \
            --set HERMES_TUI_DIR $out/ui-tui \
            --set HERMES_PYTHON ${hermesVenv}/bin/python3 \
            --set HERMES_NODE ${nodejs_22}/bin/node
        '')
        [
          "hermes"
          "hermes-agent"
          "hermes-acp"
        ]
      }

      runHook postInstall
    '';

    postFixup = ''
      substituteInPlace "$out/ui-tui/dist/entry.js" \
        --replace-fail '#!${coreutils}/bin/env -S  --max-old-space-size=8192 --expose-gc' \
        '#!${coreutils}/bin/env -S ${nodejs_22}/bin/node --max-old-space-size=8192 --expose-gc'
    '';

    passthru = (builtins.removeAttrs (upstreamPackage.passthru or { }) [ "devShellHook" ]) // {
      inherit hermesVenv upstreamPackage;
      devShellHook = ''
        STAMP=".nix-stamps/hermes-agent"
        STAMP_VALUE="${pyprojectHash}:${uvLockHash}"
        if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$STAMP_VALUE" ]; then
          echo "hermes-agent: installing Python dependencies..."
          uv venv .venv --python ${hermesVenv}/bin/python3 2>/dev/null || true
          source .venv/bin/activate
          uv pip install -e ".[${darwinExtrasSpec}]"
          [ -d mini-swe-agent ] && uv pip install -e ./mini-swe-agent 2>/dev/null || true
          [ -d tinker-atropos ] && uv pip install -e ./tinker-atropos 2>/dev/null || true
          mkdir -p .nix-stamps
          echo "$STAMP_VALUE" > "$STAMP"
        else
          source .venv/bin/activate
          export HERMES_PYTHON=${hermesVenv}/bin/python3
        fi
      '';
    };

    meta = upstreamPackage.meta // {
      # The upstream "all" extra includes local voice transcription, and
      # "messaging" currently reactivates voice-capable Discord dependencies.
      # On Darwin that pulls in faster-whisper and av, whose import check is
      # killed during local builds. Keep the CLI package usable and leave those
      # integrations to upstream.
      longDescription = ''
        Hermes Agent packaged from the upstream flake with Darwin extras that
        avoid the faster-whisper/av build path.
      '';
    };
  }
