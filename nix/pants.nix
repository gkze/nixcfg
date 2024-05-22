{ rustPlatform, fetchFromGitHub }:
let version = "2.20.1"; in rustPlatform.buildRustCrate {
  pname = "pants-engine";
  inherit version;
  src = fetchFromGitHub { owner = "pantsbuild"; repo = "pants"; };
}
