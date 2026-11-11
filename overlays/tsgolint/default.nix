{
  selfSource,
  slib,
  prev,
  ...
}:
let
  inherit (selfSource) commit version;
  src = prev.fetchFromGitHub {
    owner = "oxc-project";
    repo = "tsgolint";
    rev = commit;
    fetchSubmodules = false;
    hash = slib.sourceHash "tsgolint" "srcHash";
  };
  # tsgolint 7.0.2006's go.mod requires go >= 1.26.10, newer than nixpkgs'
  # default Go 1.26.9, and the build pins GOTOOLCHAIN=local. Select the newer
  # Go 1.27 builder rather than inheriting nixpkgs' default.
  tsgolintBase = prev.tsgolint.override {
    buildGoModule = prev.buildGoModule.override { go = prev.go_1_27; };
  };
  tsgolint = tsgolintBase.overrideAttrs (_oldAttrs: {
    inherit version src;
    patches = [ ];
    prePatch = "";

    vendorHash = slib.sourceHash "tsgolint" "vendorHash";
    env = (_oldAttrs.env or { }) // {
      GOWORK = "off";
    };

    postPatch = "";

    preBuild = ''
      if [ ! -d internal/collections ]; then
        env GOFLAGS=-mod=mod go mod download github.com/microsoft/typescript-go
        typescript_go_dir="$(env GOFLAGS=-mod=mod go list -m -f '{{.Dir}}' github.com/microsoft/typescript-go)"
        chmod -R u+w "$typescript_go_dir"
        for patch in patches/*.patch; do
          patch -d "$typescript_go_dir" -p1 < "$patch"
        done

        mkdir -p internal/collections
        find "$typescript_go_dir/internal/collections" -type f ! -name '*_test.go' -exec cp {} internal/collections/ \;
      fi

      go mod tidy
    '';
  });
in
{
  inherit tsgolint;
}
