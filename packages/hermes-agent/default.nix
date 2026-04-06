{
  inputs,
  stdenv,
  ...
}:
inputs.hermes-agent.packages.${stdenv.hostPlatform.system}.default
