{
  mkGoCliPackage,
  inputs,
  lib,
}:
{
  pname,
  cmdName,
  inputName ? pname,
  subPackage ? "cmd/${cmdName}",
  description,
  homepage,
  license ? lib.licenses.mit,
  meta ? { },
  ...
}@args:
mkGoCliPackage (
  {
    inherit pname cmdName;
    input = inputs.${inputName};
    subPackages = [ subPackage ];
    meta = {
      inherit
        description
        homepage
        license
        ;
    }
    // meta;
  }
  // (builtins.removeAttrs args [
    "pname"
    "inputName"
    "subPackage"
    "cmdName"
    "description"
    "homepage"
    "license"
    "meta"
  ])
)
