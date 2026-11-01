{
  fetchFromGitHub,
  lib,
  stdenvNoCC,
  zig_0_17,
  ...
}:
let
  pname = "neorex";
  # Upstream publishes no tags and no releases, so this pin is deliberately
  # manual: bump rev, version and hash together. Recompute the hash with
  # `nix store prefetch-file --unpack --json \
  #   https://github.com/alexrlocke/neorex/archive/<rev>.tar.gz`.
  version = "0.0.0-unstable-2026-10-31";
  rev = "e3062d2cd731315dba99faea21672b13e476ab63";

  src = fetchFromGitHub {
    owner = "alexrlocke";
    repo = pname;
    inherit rev;
    hash = "sha256-PIzF80Gpq/5uKPQNxdycYfuiKVnQ3LxuByE1YbXSFtA=";
  };
in
stdenvNoCC.mkDerivation {
  inherit pname version src;

  # build.zig.zon asks for Zig 0.17 (`minimum_zig_version`), and the hook
  # already passes `-Dcpu=baseline --release=safe`.
  nativeBuildInputs = [ zig_0_17.hook ];

  # `zig build test` is fully offline: the only Zig dependency, Ghostty's
  # terminal emulator, is lazy and fetched only for `-Dgolden`.
  doCheck = true;

  postInstall = ''
    install -Dm644 README.md "$out/share/doc/${pname}/README.md"
    install -Dm644 LICENSE "$out/share/doc/${pname}/LICENSE"
  '';

  meta = with lib; {
    description = "Rex-native Neovim frontend, with one Neovim behind every Rex pane";
    homepage = "https://github.com/alexrlocke/neorex";
    license = licenses.mit;
    mainProgram = "neorex";
    # Upstream drives Neovim over kqueue and Rex's macOS-only control socket.
    platforms = [ "aarch64-darwin" ];
  };
}
