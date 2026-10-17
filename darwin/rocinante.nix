{ outputs, ... }:
let
  profiles = import ./george/profiles.nix { slib = outputs.lib; };
in
profiles.mkHost [
  profiles.georgeBase
  {
    extraHomeModules = [ ./george/dock-apps.nix ];
  }
]
