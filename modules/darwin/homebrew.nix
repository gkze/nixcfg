{ inputs, ... }:
{
  nix-homebrew = {
    enable = true;
    enableRosetta = true;
    user = "george";
    taps = builtins.listToAttrs (
      map
        (comp: {
          name = "homebrew/homebrew-${comp}";
          value = inputs."homebrew-${comp}";
        })
        [
          "core"
          "cask"
          "bundle"
        ]
    );
    mutableTaps = false;
  };
}
