{ outputs, ... }:
outputs.lib.mkHome {
  system = "aarch64-darwin";
  username = "george";
  modules = [ "${outputs.lib.modulesPath}/home/macbook-pro-m1-16in.nix" ];
}
