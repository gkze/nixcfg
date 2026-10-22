{
  autoPatchelfHook,
  bashInteractive,
  fetchurl,
  installShellFiles,
  lib,
  selfSource,
  stdenv,
  stdenvNoCC,
  zsh,
  ...
}:
stdenvNoCC.mkDerivation {
  pname = "linear-cli";
  inherit (selfSource) version;

  src = fetchurl {
    url = selfSource.urls.${stdenvNoCC.hostPlatform.system};
    hash = selfSource.hashes.${stdenvNoCC.hostPlatform.system};
  };

  nativeBuildInputs = [
    installShellFiles
  ]
  ++ lib.optional stdenvNoCC.hostPlatform.isLinux autoPatchelfHook;
  buildInputs = lib.optional stdenvNoCC.hostPlatform.isLinux stdenv.cc.cc.lib;
  nativeInstallCheckInputs = [
    bashInteractive
    zsh
  ];
  # Preserve the signature on the publisher's Darwin executable.
  dontStrip = true;

  installPhase = ''
    runHook preInstall

    install -Dm755 linear "$out/bin/linear"
    ln -s linear "$out/bin/linear-cli"
    install -Dm644 LICENSE "$out/share/licenses/linear-cli/LICENSE"
    install -Dm644 THIRD_PARTY_LICENSES.md "$out/share/licenses/linear-cli/THIRD_PARTY_LICENSES.md"
    install -Dm644 CHANGELOG.md "$out/share/doc/linear-cli/CHANGELOG.md"

    runHook postInstall
  '';

  # Linux's runtime linker must be patched before executing the publisher binary.
  postFixup = ''
    export HOME="$TMPDIR/linear-home"
    export XDG_CONFIG_HOME="$HOME/config"
    export XDG_CACHE_HOME="$HOME/cache"
    mkdir -p "$HOME"
    ${lib.optionalString stdenvNoCC.hostPlatform.isLinux ''
      # This function runs before `runHook postFixup` reaches `postFixupHooks`,
      # which is where autoPatchelfHook rewrites the ELF interpreter. Patch the
      # output here so the binary can execute; the hook's later pass then finds
      # nothing left to patch.
      autoPatchelf "$out"
    ''}
    for command in linear linear-cli; do
      zsh_completion="$TMPDIR/_$command"
      "$out/bin/$command" completions zsh --name "$command" > "$zsh_completion"
      # Upstream emits a script for sourcing. A site-function must also dispatch
      # on its first autoload invocation, before compdef takes over later calls.
      printf '%s\n' '_clap_dynamic_completer_linear "$@"' >> "$zsh_completion"
      installShellCompletion --cmd "$command" \
        --bash <("$out/bin/$command" completions bash --name "$command") \
        --fish <("$out/bin/$command" completions fish --name "$command") \
        --zsh "$zsh_completion"
    done
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    test "$("$out/bin/linear" --version)" = "linear $version"
    test "$("$out/bin/linear-cli" --version)" = "linear $version"
    "$out/bin/linear-cli" --help >/dev/null
    ${bashInteractive}/bin/bash ${./tests/check-completions.bash} "$out"
    ${zsh}/bin/zsh ${./tests/check-completions.zsh} "$out"

    runHook postInstallCheck
  '';

  meta = with lib; {
    description = "Linear issue tracker CLI";
    homepage = "https://github.com/schpet/linear-cli";
    license = licenses.isc;
    mainProgram = "linear-cli";
    platforms = [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-darwin"
      "x86_64-linux"
    ];
    sourceProvenance = with sourceTypes; [ binaryNativeCode ];
  };
}
