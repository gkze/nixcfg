{
  lib,
  src ? ../..,
}:
let
  mcp = import (src + "/lib/opencode-mcp.nix") { inherit lib; };

  inherit (import ./lib.nix) assertEq;

  local = mcp.mkLocalServer [
    "demo"
    "--stdio"
  ];
  remote = mcp.mkRemoteServer "https://example.test/mcp";
  assertionsFor = server: mcp.mkServerAssertions "test.mcp" { demo = server; };
  transportAssertionFor = server: builtins.head (assertionsFor server);
  requirementAssertionFor = server: builtins.elemAt (assertionsFor server) 1;
  failedAssertionMessagesFor =
    server:
    builtins.map (assertion: assertion.message) (
      builtins.filter (assertion: !assertion.assertion) (assertionsFor server)
    );
  mergedRemote =
    mcp.resolveSparseMcpServerOverrides
      {
        demo = local // {
          enable = true;
        };
      }
      {
        demo = {
          command = null;
          type = "remote";
          inherit (remote) url;
        };
      };

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
    (assertEq "explicit local transport passes" true (transportAssertionFor local).assertion)
    (assertEq "explicit remote transport passes" true (transportAssertionFor remote).assertion)
    (assertEq "missing resolved type fails" false
      (transportAssertionFor {
        command = [ "demo" ];
      }).assertion
    )
    (assertEq "nullable resolved type fails despite remote url" false
      (transportAssertionFor {
        command = null;
        type = null;
        inherit (remote) url;
      }).assertion
    )
    (assertEq "other resolved types fail" [ false false false ] (
      builtins.map
        (
          serverType:
          (transportAssertionFor {
            inherit serverType;
            inherit (remote) url;
          }).assertion
        )
        [
          "stdio"
          false
          42
        ]
    ))
    (assertEq "invalid transport is independent of command and url"
      [
        "test.mcp.demo: type must be either \"local\" or \"remote\"."
      ]
      (failedAssertionMessagesFor {
        command = null;
        type = null;
        url = null;
      })
    )
    (assertEq "null local command fails" false
      (requirementAssertionFor (
        local
        // {
          command = null;
        }
      )).assertion
    )
    (assertEq "empty local command fails" false
      (requirementAssertionFor (
        local
        // {
          command = [ ];
        }
      )).assertion
    )
    (assertEq "non-list local command fails" false
      (requirementAssertionFor (
        local
        // {
          command = "demo";
        }
      )).assertion
    )
    (assertEq "null remote url fails" false
      (requirementAssertionFor (
        remote
        // {
          url = null;
        }
      )).assertion
    )
    (assertEq "remote transport permits nullable local command" true
      (requirementAssertionFor {
        command = null;
        type = "remote";
        inherit (remote) url;
      }).assertion
    )
    (assertEq "profile transport switch inherits enabled state" {
      command = null;
      enabled = true;
      type = "remote";
      inherit (remote) url;
    } (mcp.renderMcpServers mergedRemote).demo)
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
