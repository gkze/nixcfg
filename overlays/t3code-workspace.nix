{ inputs, prev, ... }:
{
  t3codeWorkspaceSource = import ../packages/t3code/_source.nix {
    src = inputs.t3code;
    inherit (prev) lib;
  };
}
