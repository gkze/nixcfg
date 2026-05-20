{
  selfSource,
  slib,
  prev,
  ...
}:
let
  inherit (selfSource) version;
  src = prev.fetchFromGitHub {
    owner = "oxc-project";
    repo = "tsgolint";
    tag = "v${version}";
    fetchSubmodules = false;
    hash = slib.sourceHash "tsgolint" "srcHash";
  };
  tsgolint = prev.tsgolint.overrideAttrs (_oldAttrs: {
    inherit version src;
    patches = [ ];
    prePatch = "";

    vendorHash = slib.sourceHash "tsgolint" "vendorHash";
    env = (_oldAttrs.env or { }) // {
      GOWORK = "off";
    };

    postPatch = ''
      if grep -Fq 'github.com/dlclark/regexp2/v2 v2.0.3' go.mod \
        && ! grep -Fq 'github.com/dlclark/regexp2/v2 v2.0.3 ' go.sum; then
        printf '%s\n' \
          'github.com/dlclark/regexp2/v2 v2.0.3 h1:Q/g6akmsBvqlGs/EQo+BvclgjZA3lCYwAayJl/4p3/4=' \
          'github.com/dlclark/regexp2/v2 v2.0.3/go.mod h1:Bz5TMy5d8fPK0ximH0Yi9KvsRHNnvXqUx9XG6a4wB+I=' \
          >> go.sum
      fi
    '';

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
    '';
  });
in
{
  inherit tsgolint;
}
