{ lib }:
let
  inherit (lib) recursiveUpdate types;

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
    type = isNullable (value: builtins.elem value [
      "local"
      "remote"
    ]);
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

  sparseMcpServerOverrideType = types.addCheck
    (types.attrsOf types.anything)
    isValidSparseMcpServerOverride;

  sparseMcpServerOverrideMapType = types.attrsOf sparseMcpServerOverrideType;
in
{
  inherit sparseMcpServerOverrideMapType sparseMcpServerOverrideType;

  resolveSparseMcpServerOverrides = baseServers: overrideServers:
    recursiveUpdate baseServers overrideServers;
}
