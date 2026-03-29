{
  fetchurl,
  lib,
  stdenvNoCC,
  outputs,
  ...
}:
let
  slib = outputs.lib;
  info = slib.sourceEntry "homebrew-zsh-completion";
  inherit (info) version;
  source = lib.findFirst (
    entry: entry.hashType == "sha256" && lib.hasInfix "/${version}/" entry.url
  ) null info.hashes;
in
assert source != null;
stdenvNoCC.mkDerivation {
  pname = "homebrew-zsh-completion";
  inherit version;

  src = fetchurl {
    inherit (source) url;
    inherit (source) hash;
  };

  dontUnpack = true;

  installPhase = ''
    mkdir $out/
    cp -r $src $out/_brew
    chmod +x $out/_brew
  '';

  meta = with lib; {
    description = "Homebrew zsh completion script";
    homepage = "https://github.com/Homebrew/brew";
    license = licenses.bsd2;
    platforms = platforms.unix;
  };
}
