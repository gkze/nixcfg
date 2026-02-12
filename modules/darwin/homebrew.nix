{ inputs, primaryUser, ... }:
let
  mkHomebrewTaps =
    tapNames:
    builtins.listToAttrs (
      map (tap: {
        name = "homebrew/homebrew-${tap}";
        value = inputs."homebrew-${tap}";
      }) tapNames
    );
in
{
  nix-homebrew = {
    enable = true;
    enableRosetta = true;
    user = primaryUser;
    taps =
      mkHomebrewTaps [
        "core"
        "cask"
      ]
      // {
        "pantsbuild/homebrew-tap" = inputs.pantsbuild-tap;
      };
    mutableTaps = false;
  };
}
