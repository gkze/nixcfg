{ outputs, ... }:
let
  profiles = import ./george/profiles.nix { slib = outputs.lib; };
in
profiles.mkHost [
  profiles.georgeBase
  profiles.townWorkstation
  {
    extraSystemModules = [
      { networking.computerName = "Zeus"; }
    ];
  }
]
