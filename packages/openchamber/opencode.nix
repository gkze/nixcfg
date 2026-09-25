{
  bun,
  lib,
  makeBinaryWrapper,
  models-dev,
  nodejs_24,
  nodeModules,
  python3,
  ripgrep,
  src,
  stdenvNoCC,
  sysctl,
  version,
}:
stdenvNoCC.mkDerivation {
  pname = "openchamber-opencode";
  inherit src version;

  nativeBuildInputs = [
    bun
    makeBinaryWrapper
    models-dev
    nodejs_24
    python3
  ];

  strictDeps = true;

  env = {
    MODELS_DEV_API_JSON = "${models-dev}/dist/_api.json";
    OPENCODE_CHANNEL = "prod";
    OPENCODE_DISABLE_MODELS_FETCH = "true";
    OPENCODE_VERSION = version;
  };

  postPatch = ''
    PYTHONPATH=${
      lib.fileset.toSource {
        root = ../..;
        fileset = lib.fileset.unions [
          ../../lib/__init__.py
          ../../lib/exact_text_patch.py
        ];
      }
    } ${lib.getExe python3} ${./patch_nix_managed.py} "$PWD" --component opencode
  '';

  configurePhase = ''
    runHook preConfigure

    cp -R ${nodeModules}/. .
    patchShebangs node_modules packages/*/node_modules

    runHook postConfigure
  '';

  buildPhase = ''
    runHook preBuild

    buildTmp="$TMPDIR/opencode-build"
    export TMPDIR="$buildTmp/tmp"
    export HOME="$buildTmp/home"
    mkdir -p "$TMPDIR" "$HOME"

    cd packages/opencode
    bun --bun ./script/build.ts --single --skip-install

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    install -Dm755 dist/opencode-*/bin/opencode "$out/bin/opencode"
    wrapProgram "$out/bin/opencode" \
      --set OPENCODE_DISABLE_AUTOUPDATE true \
      --set OPENCODE_NIX_MANAGED 1 \
      --prefix PATH : ${
        lib.makeBinPath [
          ripgrep
          sysctl
        ]
      }

    runHook postInstall
  '';

  postFixup = ''
    wrappedExecutable="$out/bin/.opencode-wrapped"
    /usr/bin/codesign --force --sign - "$wrappedExecutable"
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    wrappedExecutable="$out/bin/.opencode-wrapped"
    test -x "$wrappedExecutable"
    /usr/bin/codesign --verify --strict --verbose=2 "$wrappedExecutable"
    test "$(/usr/bin/lipo -archs "$wrappedExecutable")" = arm64
    test "$(HOME="$TMPDIR" "$out/bin/opencode" --version)" = "${version}"

    runHook postInstallCheck
  '';

  meta = {
    description = "Exact OpenCode companion runtime for OpenChamber";
    homepage = "https://github.com/anomalyco/opencode";
    license = lib.licenses.mit;
    mainProgram = "opencode";
    platforms = [ "aarch64-darwin" ];
  };
}
