{
  fetchurl,
  lib,
  stdenvNoCC,
  selfSource,
  ...
}:
let
  inherit (selfSource) version;
  source = lib.findFirst (
    entry: entry.hashType == "sha256" && lib.hasInfix "/${version}/" entry.url
  ) null selfSource.hashes;
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
