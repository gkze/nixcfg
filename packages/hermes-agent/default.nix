{
  callPackage,
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
      "daytona"
      "dingtalk"
      "feishu"
      "homeassistant"
      "honcho"
      "mcp"
      "modal"
      "slack"
      "sms"
      "tts-premium"
      "web"
    ];
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

    hermesVenv =
      (callPackage (hermesSource + "/nix/python.nix") {
        inherit (inputs)
          pyproject-build-systems
          pyproject-nix
          uv2nix
          ;
        python312 = python312ForHermes;
        dependency-groups = darwinExtras;
        inherit (hermesNpmLib) pythonSrc;
      }).venv;

    npm-lockfile-fix = inputs.hermes-agent.inputs.npm-lockfile-fix.packages.${system}.default;
    hermesNpmLib = callPackage (hermesSource + "/nix/lib.nix") {
      inherit npm-lockfile-fix;
      # Upstream pins the npm toolchain to nodejs_22 (nix/hermes-agent.nix);
      # keep the build toolchain in lockstep with the wrapper's HERMES_NODE.
      nodejs = nodejs_22;
    };
    hermesTui = callPackage (hermesSource + "/nix/tui.nix") {
      inherit hermesNpmLib;
    };
    hermesWeb = callPackage (hermesSource + "/nix/web.nix") {
      inherit hermesNpmLib;
    };

    bundledSkills = lib.cleanSourceWith {
      src = hermesSource + "/skills";
      filter = path: _type: !(lib.hasInfix "/index-cache/" path) && !(lib.hasInfix "/__pycache__/" path);
    };
    # Skills are excluded from the wheel as of v2026.7.20 (see upstream
    # nix/lib.nix pythonSrc); optional skills only reach the agent through
    # HERMES_OPTIONAL_SKILLS.
    bundledOptionalSkills = lib.cleanSourceWith {
      src = hermesSource + "/optional-skills";
      filter = path: _type: !(lib.hasInfix "/index-cache/" path) && !(lib.hasInfix "/__pycache__/" path);
    };
    bundledPlugins = lib.cleanSourceWith {
      src = hermesSource + "/plugins";
      filter =
        path: _type:
        !(lib.any (needle: lib.hasInfix needle path) [
          "/__pycache__/"
          ".pyc"
          "/.pytest_cache/"
        ]);
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
      cp -r ${bundledOptionalSkills} $out/share/hermes-agent/optional-skills
      cp -r ${bundledPlugins} $out/share/hermes-agent/plugins
      cp -r ${hermesWeb} $out/share/hermes-agent/web_dist

      mkdir -p $out/ui-tui
      cp -r ${hermesTui}/lib/hermes-tui/* $out/ui-tui/

      ${lib.concatMapStringsSep "\n"
        (name: ''
          makeWrapper ${hermesVenv}/bin/${name} $out/bin/${name} \
            --suffix PATH : "${runtimePath}" \
            --set HERMES_BUNDLED_SKILLS $out/share/hermes-agent/skills \
            --set HERMES_OPTIONAL_SKILLS $out/share/hermes-agent/optional-skills \
            --set HERMES_BUNDLED_PLUGINS $out/share/hermes-agent/plugins \
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

    passthru = (builtins.removeAttrs (upstreamPackage.passthru or { }) [ "devShellHook" ]) // {
      inherit hermesVenv upstreamPackage;
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
