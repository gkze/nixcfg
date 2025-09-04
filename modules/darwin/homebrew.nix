{ inputs, primaryUser, ... }:
{
  nix-homebrew = {
    enable = true;
    enableRosetta = true;
    user = primaryUser;
    taps =
      builtins.listToAttrs (
        map
          (tap: {
            name = "homebrew/homebrew-${tap}";
            value = inputs."homebrew-${tap}";
          })
          [
            "core"
            "cask"
          ]
      )
      // {
        "pantsbuild/homebrew-tap" = inputs.pantsbuild-tap;
      };
    mutableTaps = false;
  };
}
