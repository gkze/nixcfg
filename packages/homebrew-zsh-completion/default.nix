{
  lib,
  stdenvNoCC,
  outputs,
  ...
}:
let
  slib = outputs.lib;
  source = slib.sourceHashEntry "homebrew-zsh-completion" "sha256";
in
stdenvNoCC.mkDerivation {
  name = "brew-zsh-completion";
  src = builtins.fetchurl {
    inherit (source) url;
    sha256 = source.hash;
  };
  dontUnpack = true;
  installPhase = ''
    mkdir $out/
    cp -r $src $out/_brew
    chmod +x $out/_brew
  '';
  meta.platforms = lib.platforms.darwin;
}
