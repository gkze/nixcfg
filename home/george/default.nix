{ outputs, ... }:
outputs.lib.mkHome {
  system = "aarch64-darwin";
  username = "george";
}
