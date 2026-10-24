# Evaluate the exported Darwin `mkSystem` without the extra modules that
# `mkDarwinHost` adds. Only a real nix-darwin and Home Manager evaluation shows
# that the exported modules read no option those extra modules declare (such
# as nix-homebrew's) and need no argument the constructor does not supply.
{
  darwinSystem,
  mkSystem,
}:
let
  inherit
    (darwinSystem (mkSystem {
      system = "aarch64-darwin";
      users = [ "alice" ];
    }))
    config
    ;
in
assert config.homebrew.taps == [ ];
assert config.system.defaults.NSGlobalDomain.AppleInterfaceStyle == null;
assert config.system.defaults.NSGlobalDomain.AppleInterfaceStyleSwitchesAutomatically;
assert config.home-manager.users.alice.home.homeDirectory == "/Users/alice";
true
