{ config, lib, ... }:
{
  optionPath,
  enableDescription,
  pathOptionName,
  pathDefault,
  pathDescription,
  extraConfig ? { },
  # Additional static PATH entries appended after the primary path option.
  extraPaths ? [ ],
}:
let
  cfg = lib.getAttrFromPath optionPath config;
in
{
  options = lib.setAttrByPath optionPath {
    enable = lib.mkEnableOption enableDescription;
    ${pathOptionName} = lib.mkOption {
      type = lib.types.str;
      default = pathDefault;
      description = pathDescription;
    };
  };

  config = lib.mkIf cfg.enable (
    extraConfig // { home.sessionPath = [ cfg.${pathOptionName} ] ++ extraPaths; }
  );
}
