{ lib, pkgs }:
let
  inherit (lib)
    assertMsg
    concatMapStringsSep
    escapeShellArg
    ;

  bunxExe = pkgs.lib.getExe' pkgs.bun "bunx";
  tokenEnvPattern = "[A-Za-z_][A-Za-z0-9_]*";
  checkedTokenEnv =
    tokenEnv:
    assert assertMsg (
      builtins.isString tokenEnv && builtins.match tokenEnvPattern tokenEnv != null
    ) "mcp remote wrapper tokenEnv must be a shell variable name";
    tokenEnv;

  mkTokenCommand =
    args:
    let
      tokenCommand = args.tokenCommand or null;
      tokenEnv = args.tokenEnv or null;
      safeTokenEnv = if tokenEnv != null then checkedTokenEnv tokenEnv else null;
    in
    if tokenCommand != null then
      tokenCommand
    else if safeTokenEnv != null then
      "printf '%s' \"\${" + safeTokenEnv + ":-}\""
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
        ${escapeShellArg url}
        --header "Authorization: Bearer $token"
      )
      ${concatMapStringsSep "\n      " (header: "args+=(--header ${escapeShellArg header})") extraHeaders}
      exec ${bunxExe} --bun mcp-remote@latest "''${args[@]}"
    '';
in
{
  inherit bunxExe mkMcpRemoteWrapper;
}
