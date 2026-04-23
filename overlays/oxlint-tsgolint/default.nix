{
  selfSource,
  slib,
  prev,
  ...
}:
let
  buildGoModule = prev.buildGoModule.override { go = prev.go_latest or prev.go; };
  inherit (selfSource) version;
  src = prev.fetchgit {
    url = "https://github.com/oxc-project/tsgolint.git";
    rev = "v${version}";
    fetchSubmodules = true;
    hash = slib.sourceHash "oxlint-tsgolint" "srcHash";
  };
in
{
  oxlint-tsgolint = buildGoModule {
    pname = "oxlint-tsgolint";
    inherit version src;
    subPackages = [ "cmd/tsgolint" ];
    vendorHash = slib.sourceHash "oxlint-tsgolint" "vendorHash";
    doCheck = false;
    env = {
      GOWORK = "off";
    };

    postPatch = ''
      for patch in patches/*.patch; do
        patch -d typescript-go -p1 < "$patch"
      done

      substituteInPlace go.mod \
        --replace-fail $'replace (\n' \
          $'replace (\n\tgithub.com/microsoft/typescript-go => ./typescript-go\n' \
        --replace-fail $'\tgithub.com/klauspost/cpuid/v2 v2.2.10 // indirect' \
          $'\tgithub.com/klauspost/cpuid/v2 v2.2.10 // indirect\n\tgithub.com/mackerelio/go-osstat v0.2.7 // indirect' \
        --replace-fail $'\tgolang.org/x/mod v0.33.0 // indirect' \
          $'\tgolang.org/x/mod v0.35.0 // indirect' \
        --replace-fail $'\tgolang.org/x/sync v0.19.0 // indirect' \
          $'\tgolang.org/x/sync v0.20.0 // indirect'
      patch -p1 < ${./go-sum.patch}

      mkdir -p internal/collections
      find ./typescript-go/internal/collections -type f ! -name '*_test.go' -exec cp {} internal/collections/ \;
    '';

    meta = with prev.lib; {
      description = "High-performance type-aware TypeScript linting backend for Oxlint";
      homepage = "https://github.com/oxc-project/tsgolint";
      license = licenses.mit;
      platforms = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-darwin"
        "x86_64-linux"
      ];
      mainProgram = "tsgolint";
    };
  };
}
