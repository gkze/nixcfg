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
  symlinkJoin,
  tirith,
  ...
}:
let
  inherit (builtins)
    fromTOML
    readFile
    ;

  inherit (stdenv.hostPlatform) system;
  platformCompat = import ../../lib/pinned-input-platform-compat;
  pyprojectBuildSystemsCompat = inputs.pyproject-build-systems // {
    overlays = inputs.pyproject-build-systems.overlays // {
      default = lib.composeManyExtensions [
        platformCompat.overlay
        inputs.pyproject-build-systems.overlays.default
      ];
    };
  };
  upstreamPackage = inputs.hermes-agent.packages.${system}.default;
in
if !stdenv.hostPlatform.isDarwin then
  upstreamPackage
else
  let
    darwinExtras = [
      "acp"
      "bedrock"
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

    hermesVenvBase =
      (callPackage (hermesSource + "/nix/python.nix") {
        inherit (inputs)
          pyproject-nix
          uv2nix
          ;
        pyproject-build-systems = pyprojectBuildSystemsCompat;
        python312 = python312ForHermes;
        dependency-groups = darwinExtras;
        inherit (hermesNpmLib) pythonSrc;
      }).venv;

    hermesPythonPackage =
      lib.findFirst
        (dependency: lib.hasSuffix "-hermes-agent-${upstreamPackage.version}" (baseNameOf dependency))
        (throw "Hermes Python package missing from the generated virtualenv dependency set")
        (lib.splitString ":" hermesVenvBase.NIX_PYPROJECT_DEPS);

    # The code-scoped marker is authoritative in upstream's install-method
    # detector. Stamp the Python package that owns hermes_cli, rather than the
    # virtualenv whose hermes_cli entry is only a symlink to that package.
    nixManagedHermesPythonPackage = symlinkJoin {
      name = "hermes-agent-nix-managed-python";
      paths = [ hermesPythonPackage ];
      postBuild = ''
        printf '%s\n' nix > "$out/${python312ForHermes.sitePackages}/.install_method"
        for entrypoint in "$out"/bin/*; do
          if test -L "$entrypoint"; then
            entrypointTarget=$(readlink "$entrypoint")
            unlink "$entrypoint"
            cp "$entrypointTarget" "$entrypoint"
          fi
        done
      '';
    };

    hermesVenv = hermesVenvBase.overrideAttrs (old: {
      NIX_PYPROJECT_DEPS = lib.concatStringsSep ":" (
        map (
          dependency:
          if dependency == hermesPythonPackage then toString nixManagedHermesPythonPackage else dependency
        ) (lib.splitString ":" old.NIX_PYPROJECT_DEPS)
      );
    });

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

    doInstallCheck = true;
    installCheckPhase = ''
      runHook preInstallCheck

      legacyHome=$(mktemp -d)
      printf '%s\n' git > "$legacyHome/.install_method"
      HERMES_HOME="$legacyHome" $out/bin/hermes --version | grep -Fqx 'Install method: nix'
      HERMES_HOME="$legacyHome" ${hermesVenv}/bin/python3 -c \
        'from hermes_cli import web_server; assert web_server.detect_install_method(web_server.PROJECT_ROOT) == "nix"'

      if updateOutput=$(HERMES_HOME="$legacyHome" $out/bin/hermes update --check); then
        echo "Hermes unexpectedly admitted a Nix-managed self-update" >&2
        exit 1
      else
        updateStatus=$?
      fi
      test "$updateStatus" -eq 2
      test "$updateOutput" = \
        'Update Hermes through the Nix source that installed it (e.g. nix profile upgrade, or update your flake input and rebuild with nixos-rebuild or home-manager switch)'

      runHook postInstallCheck
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
