{ lib, pkgs }:
let
  inherit (lib)
    concatMapStringsSep
    escapeShellArg
    ;

  bunxExe = pkgs.lib.getExe' pkgs.bun "bunx";

  mkTokenCommand =
    args:
    let
      tokenCommand = args.tokenCommand or null;
      tokenEnv = args.tokenEnv or null;
    in
    if tokenCommand != null then
      tokenCommand
    else if tokenEnv != null then
      "printf '%s' \"\${" + tokenEnv + ":-}\""
    else
      "false";

  mkMcpRemoteWrapper =
    args:
    let
      inherit (args) name;
      tokenCommand = args.tokenCommand or null;
      tokenEnv = args.tokenEnv or null;
      inherit (args) url;
      extraHeaders = args.extraHeaders or [ ];
      resolvedTokenCommand = mkTokenCommand { inherit tokenCommand tokenEnv; };
    in
    pkgs.writeShellScript name ''
      set -euo pipefail

      token="$(${resolvedTokenCommand})"
      if [ -z "$token" ]; then
        echo ${escapeShellArg "${name}: token lookup returned empty output"} >&2
        exit 1
      fi

      args=(
        "${url}"
        --header "Authorization: Bearer $token"
      )
      ${concatMapStringsSep "\n      " (header: "args+=(--header ${escapeShellArg header})") extraHeaders}
      exec ${bunxExe} --bun mcp-remote@latest "''${args[@]}"
    '';
in
{
  inherit bunxExe mkMcpRemoteWrapper;
}
