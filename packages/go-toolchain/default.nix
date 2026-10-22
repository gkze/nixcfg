{
  fetchurl,
  go_1_27,
  lib,
  outputs,
  selfSource,
  ...
}:
assert lib.versions.majorMinor selfSource.version == lib.versions.majorMinor go_1_27.version;
go_1_27.overrideAttrs (old: {
  inherit (selfSource) version;
  src = fetchurl {
    url = outputs.lib.sourceUrl "go-toolchain" "sha256";
    hash = outputs.lib.sourceHash "go-toolchain" "sha256";
  };

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    export GOTOOLCHAIN=local GOENV=off GOCACHE="$TMPDIR/go-check-cache" GOPATH="$TMPDIR/go-check-path"
    test "$("$out/bin/go" env GOVERSION)" = "go$version"
    test -x "$out/bin/gofmt"
    mkdir "$TMPDIR/go-check"
    cd "$TMPDIR/go-check"
    printf 'module nixcfg-toolchain-check\n\ngo %s\n' "$version" > go.mod
    cat > main.go <<'EOF'
    package main
    import "fmt"
    func main() { fmt.Println("go toolchain check passed") }
    EOF
    "$out/bin/go" build -o check .
    test "$(./check)" = "go toolchain check passed"
    runHook postInstallCheck
  '';

  meta = old.meta // {
    description = "Current stable Go toolchain for interactive development";
  };
})
