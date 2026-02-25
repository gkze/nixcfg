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
}:
mkGoCliPackage {
  inherit pname cmdName;
  input = inputs.${inputName};
  subPackages = [ subPackage ];
  meta = with lib; {
    inherit description homepage;
    license = licenses.mit;
  };
}
