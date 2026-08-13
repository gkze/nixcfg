{
  config,
  lib,
  pkgs,
  ...
}:
let
  catalog = import ./mcp-catalog.nix { inherit config lib pkgs; };
in
{
  nixcfg.opencode.mcpServers = catalog.base;
}
