{
  bash,
  direnv,
  nixDirenv,
  runCommand,
}:
runCommand "nix-direnv-batched-flake-input-gcroots-test"
  {
    nativeBuildInputs = [
      bash
      direnv
    ];
  }
  ''
    bash ${./test.sh} \
      ${nixDirenv}/share/nix-direnv/direnvrc \
      ${direnv}/bin/direnv \
      ${./retry-integration.envrc}
    touch "$out"
  ''
