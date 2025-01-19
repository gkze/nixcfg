{ outputs, ... }:
outputs.lib.mkHome {
  system = "aarch64-darwin";
  username = "george";
  modules = [ "${outputs.lib.modulesPath}/home/macbook-pro-16in.nix" ];
}
