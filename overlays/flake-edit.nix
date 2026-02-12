{
  inputs,
  system,
  ...
}:
{
  flake-edit = inputs.flake-edit.packages.${system}.default;
}
