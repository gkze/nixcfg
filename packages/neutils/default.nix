{
  fetchFromGitHub,
  lib,
  outputs,
  pkgs,
  selfSource,
  stdenvNoCC,
  zig_0_15,
  ...
}:
let
  pname = "neutils";
  inherit (selfSource) version;
  buildFlags = [
    "--release=small"
    "-Dcpu=baseline"
  ];

  src = fetchFromGitHub {
    owner = "deevus";
    repo = pname;
    tag = "v${version}";
    hash = outputs.lib.sourceHash pname "srcHash";
  };

  zigDeps = pkgs.callPackage ./build.zig.zon.nix { };
in
stdenvNoCC.mkDerivation {
  inherit pname version src;

  nativeBuildInputs = [ zig_0_15.hook ];

  # nixpkgs' zig hook appends its own optimization flags unless disabled.
  dontSetZigDefaultFlags = true;

  # zigInstallPhase already runs `zig build install`, so skip a duplicate build.
  dontUseZigBuild = true;

  zigBuildFlags = buildFlags;

  zigCheckFlags = buildFlags;

  postConfigure = ''
    ln -s ${zigDeps} "$ZIG_GLOBAL_CACHE_DIR/p"
  '';

  doCheck = true;

  postInstall = ''
    install -Dm644 README.md "$out/share/doc/${pname}/README.md"
    install -Dm644 LICENSE "$out/share/doc/${pname}/LICENSE"
  '';

  meta = with lib; {
    description = "Modern CLI utilities for everyday developer tasks";
    homepage = "https://github.com/deevus/neutils";
    license = licenses.mit;
    mainProgram = "urlparse";
    platforms = [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
  };
}
