{
  mkGoCliPackage,
  inputs,
  lib,
}:
{
  pname,
  inputName,
  subPackage,
  cmdName,
  description,
  homepage,
  ...
}@args:
mkGoCliPackage (
  {
    inherit pname cmdName;
    input = inputs.${inputName};
    subPackages = [ subPackage ];
    meta = with lib; {
      inherit description homepage;
      license = licenses.mit;
    };
  }
  // (builtins.removeAttrs args [
    "pname"
    "inputName"
    "subPackage"
    "cmdName"
    "description"
    "homepage"
  ])
)
