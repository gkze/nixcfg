{ inputs, system, ... }:
{
  inherit (import inputs.nixpkgs-swift { inherit system; })
    swiftPackages
    swift
    ;
}
