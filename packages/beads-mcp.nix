{
  mkUv2nixPackage,
  inputs,
  ...
}:
mkUv2nixPackage {
  name = "beads-mcp";
  src = "${inputs.beads}/integrations/beads-mcp";
  mainProgram = "beads-mcp";
}
