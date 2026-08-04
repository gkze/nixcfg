{
  callPackage,
  ffmpeg,
  git,
  inputs,
  lib,
  makeWrapper,
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
      "google"
      "homeassistant"
      "honcho"
      "mcp"
      "modal"
      "slack"
      "sms"
      "tts-premium"
      "web"
      "youtube"
    ];
    hermesSource = inputs.hermes-agent;
    hermesRevision = hermesSource.rev or null;

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
    bundledLocales = lib.cleanSource (hermesSource + "/locales");
    bundledOptionalMcps = lib.cleanSourceWith {
      src = hermesSource + "/optional-mcps";
      filter = path: _type: !(lib.hasInfix "/__pycache__/" path);
    };

    runtimePath = lib.makeBinPath [
      hermesNpmLib.nodejs
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
    nativeBuildInputs = [ makeWrapper ];

    installPhase = ''
      runHook preInstall

      mkdir -p $out/share/hermes-agent $out/bin
      cp -r ${bundledSkills} $out/share/hermes-agent/skills
      cp -r ${bundledOptionalSkills} $out/share/hermes-agent/optional-skills
      cp -r ${bundledPlugins} $out/share/hermes-agent/plugins
      cp -r ${bundledLocales} $out/share/hermes-agent/locales
      cp -r ${bundledOptionalMcps} $out/share/hermes-agent/optional-mcps
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
            --set HERMES_BUNDLED_LOCALES $out/share/hermes-agent/locales \
            --set HERMES_OPTIONAL_MCPS $out/share/hermes-agent/optional-mcps \
            --set HERMES_WEB_DIST $out/share/hermes-agent/web_dist \
            --set HERMES_TUI_DIR $out/ui-tui \
            --set HERMES_PYTHON ${hermesVenv}/bin/python3 \
            --set HERMES_NODE ${lib.getExe hermesNpmLib.nodejs}${
              lib.optionalString (
                hermesRevision != null
              ) " \\\n            --set HERMES_REVISION ${hermesRevision}"
            }
        '')
        [
          "hermes"
          "hermes-agent"
          "hermes-acp"
        ]
      }

      runHook postInstall
    '';

    # Upstream desktop/dev passthru closes over the unadapted package, so do
    # not expose those outputs as if they used this Darwin-specific venv.
    passthru = {
      inherit
        hermesNpmLib
        hermesTui
        hermesVenv
        hermesWeb
        upstreamPackage
        ;
    };

    meta = upstreamPackage.meta // {
      # The upstream default/full package includes local voice transcription,
      # and "messaging" currently reactivates voice-capable Discord dependencies.
      # On Darwin that pulls in faster-whisper and av, whose import check is
      # killed during local builds. Keep the CLI package usable and leave those
      # integrations to upstream.
      longDescription = ''
        Hermes Agent packaged from the upstream flake with Darwin extras that
        avoid the faster-whisper/av build path.
      '';
    };
  }
