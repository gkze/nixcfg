{ inputs, ... }:
{
  nix-homebrew = {
    enable = true;
    enableRosetta = true;
    user = "george";
    taps = with inputs; {
      "homebrew/homebrew-core" = homebrew-core;
      "homebrew/homebrew-cask" = homebrew-cask;
      "homebrew/homebrew-bundle" = homebrew-bundle;
    };
    mutableTaps = false;
  };
}
