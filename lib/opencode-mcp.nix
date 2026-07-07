{ lib }:
let
  inherit (lib)
    mapAttrs
    mapAttrsToList
    mkEnableOption
    mkOption
    optionalAttrs
    recursiveUpdate
    types
    ;

  isNullable = check: value: value == null || check value;

  isStringList = value: builtins.isList value && builtins.all builtins.isString value;

  isNullableStringAttrs =
    value:
    builtins.isAttrs value
    && builtins.all (item: item == null || builtins.isString item) (builtins.attrValues value);

  sparseMcpServerFieldChecks = {
    command = isNullable isStringList;
    enable = isNullable builtins.isBool;
    enabled = isNullable builtins.isBool;
    environment = isNullable isNullableStringAttrs;
    type = isNullable (
      value:
      builtins.elem value [
        "local"
        "remote"
      ]
    );
    url = isNullable builtins.isString;
  };

  isValidSparseMcpServerOverride =
    server:
    builtins.isAttrs server
    && builtins.all (
      field:
      let
        check = sparseMcpServerFieldChecks.${field} or (_: true);
      in
      check server.${field}
    ) (builtins.attrNames server);

  sparseMcpServerOverrideType = types.addCheck (types.attrsOf types.anything) isValidSparseMcpServerOverride;

  sparseMcpServerOverrideMapType = types.attrsOf sparseMcpServerOverrideType;

  mcpServerType = types.submodule {
    freeformType = types.attrsOf types.anything;

    options = {
      enable = mkEnableOption "MCP server" // {
        default = false;
      };

      type = mkOption {
        type = types.enum [
          "local"
          "remote"
        ];
        default = "local";
        description = "MCP server transport type.";
      };

      command = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Command argv for local MCP servers.";
      };

      url = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Endpoint URL for remote MCP servers.";
      };

      environment = mkOption {
        type = types.attrsOf types.str;
        default = { };
        description = "Environment variables for local MCP server processes.";
      };
    };
  };

  mcpServerMapType = types.attrsOf mcpServerType;

  mkSparseServerOverride =
    baseServers: name: server:
    let
      extras = removeAttrs server [
        "enable"
        "enabled"
      ];
      enabled =
        server.enabled or (server.enable or (if builtins.hasAttr name baseServers then null else false));
    in
    extras // optionalAttrs (enabled != null) { inherit enabled; };

  renderSparseMcpServerOverrides =
    baseServers: servers: mapAttrs (mkSparseServerOverride baseServers) servers;

  mkServerConfig =
    server:
    let
      enabled = server.enabled or (server.enable or false);
      serverType = server.type or "local";
      command = server.command or [ ];
      url = server.url or null;
      environment = server.environment or { };
      extras = removeAttrs server [
        "command"
        "enable"
        "enabled"
        "environment"
        "type"
        "url"
      ];
    in
    extras
    // {
      inherit enabled;
      type = serverType;
    }
    // optionalAttrs (command != [ ]) { inherit command; }
    // optionalAttrs (url != null) { inherit url; }
    // optionalAttrs (environment != { }) { inherit environment; };

  renderMcpServers = servers: mapAttrs (_: mkServerConfig) servers;

  mkServerAssertions =
    optionPath: servers:
    mapAttrsToList (
      name: server:
      let
        isLocal = (server.type or "local") == "local";
        command = server.command or [ ];
        url = server.url or null;
        isValid = if isLocal then command != [ ] else url != null;
      in
      {
        assertion = isValid;
        message =
          if isLocal then
            "${optionPath}.${name}: local servers require a non-empty command."
          else
            "${optionPath}.${name}: remote servers require a non-null url.";
      }
    ) servers;

  mkProfileOverlayConfig =
    baseServers: profile:
    profile.settings
    // {
      "$schema" = profile.settings."$schema" or "https://opencode.ai/config.json";
    }
    // optionalAttrs (profile.mcpServers != { }) {
      mcp = renderSparseMcpServerOverrides baseServers profile.mcpServers;
    };
in
{
  inherit
    mkProfileOverlayConfig
    mkServerAssertions
    mcpServerMapType
    mcpServerType
    renderMcpServers
    renderSparseMcpServerOverrides
    sparseMcpServerOverrideMapType
    sparseMcpServerOverrideType
    ;

  resolveSparseMcpServerOverrides =
    baseServers: overrideServers: recursiveUpdate baseServers overrideServers;
}
