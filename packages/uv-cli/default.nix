{
  fetchurl,
  installShellFiles,
  lib,
  python3,
  selfSource,
  stdenvNoCC,
  ...
}:
stdenvNoCC.mkDerivation {
  pname = "uv-cli";
  inherit (selfSource) version;
  src = fetchurl {
    url = selfSource.urls.${stdenvNoCC.hostPlatform.system};
    hash = selfSource.hashes.${stdenvNoCC.hostPlatform.system};
  };
  nativeBuildInputs = [ installShellFiles ];
  nativeInstallCheckInputs = [ python3 ];
  # The Darwin archives are publisher-signed; the Linux archives are static musl.
  dontFixup = true;
  installPhase = ''
    runHook preInstall
    install -Dm755 uv "$out/bin/uv"
    install -Dm755 uvx "$out/bin/uvx"
    export HOME="$TMPDIR/uv-home" UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never
    mkdir -p "$HOME"
    installShellCompletion --cmd uv \
      --bash <("$out/bin/uv" generate-shell-completion bash) \
      --fish <("$out/bin/uv" generate-shell-completion fish) \
      --zsh <("$out/bin/uv" generate-shell-completion zsh)
    installShellCompletion --cmd uvx \
      --bash <("$out/bin/uvx" --generate-shell-completion bash) \
      --fish <("$out/bin/uvx" --generate-shell-completion fish) \
      --zsh <("$out/bin/uvx" --generate-shell-completion zsh)
    runHook postInstall
  '';
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    test "$("$out/bin/uv" --version | cut -d' ' -f2)" = "$version"
    test "$("$out/bin/uvx" --version | cut -d' ' -f2)" = "$version"
    export HOME="$TMPDIR/uv-check-home" UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never
    mkdir -p "$HOME"
    "$out/bin/uv" venv --no-project --no-config --python ${python3}/bin/python3 "$TMPDIR/uv-venv"
    "$TMPDIR/uv-venv/bin/python" -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)'
    runHook postInstallCheck
  '';
  meta = {
    description = "Current stable standalone uv and uvx command-line tools";
    homepage = "https://github.com/astral-sh/uv";
    license = with lib.licenses; [
      asl20
      mit
    ];
    mainProgram = "uv";
    platforms = [
      "aarch64-darwin"
      "x86_64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
    sourceProvenance = with lib.sourceTypes; [ binaryNativeCode ];
  };
}
