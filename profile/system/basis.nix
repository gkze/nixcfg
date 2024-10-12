{ hostPlatform, ... }:
let kernel = builtins.elemAt (builtins.split "-" hostPlatform) 2; in
{ imports = [ { darwin = { }; linux = { }; }.${kernel} ]; }
