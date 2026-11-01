{
  fetchFromGitHub,
  lib,
  outputs,
  selfSource,
  stdenvNoCC,
  zig_0_17,
  ...
}:
let
  pname = "neorex";
  # Upstream tags no releases, so the pin comes from the update pipeline
  # (`nixcfg update neorex`): it resolves the default-branch head into a
  # `0.0.0-unstable-<date>` version and hashes that immutable commit.
  inherit (selfSource) version;
  rev = selfSource.commit;

  src = fetchFromGitHub {
    owner = "alexrlocke";
    repo = pname;
    inherit rev;
    hash = outputs.lib.sourceHash pname "srcHash";
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
