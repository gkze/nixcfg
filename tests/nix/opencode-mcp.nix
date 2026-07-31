{
  lib,
  src ? ../..,
}:
let
  mcp = import (src + "/lib/opencode-mcp.nix") { inherit lib; };

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  local = mcp.mkLocalServer [
    "demo"
    "--stdio"
  ];
  remote = mcp.mkRemoteServer "https://example.test/mcp";

  checks = [
    (assertEq "local MCP constructor" {
      type = "local";
      command = [
        "demo"
        "--stdio"
      ];
    } local)
    (assertEq "remote MCP constructor" {
      type = "remote";
      url = "https://example.test/mcp";
    } remote)
    (assertEq "constructors compose with OpenCode-specific fields"
      {
        enabled = true;
        type = "remote";
        url = "https://example.test/mcp";
        oauth = { };
      }
      (mcp.renderMcpServers {
        demo = remote // {
          enable = true;
          oauth = { };
        };
      }).demo
    )
  ];
in
builtins.deepSeq checks true
