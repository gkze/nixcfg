{
  go_1_27,
  mkGoCli,
  ...
}:
mkGoCli {
  pname = "axiom-cli";
  # Intentional compatibility policy; the updater reads this choice through package passthru.
  go = go_1_27;
  cmdName = "axiom";
  description = "The power of Axiom on the command line";
  homepage = "https://github.com/axiomhq/cli";
  # The release module includes developer-only generators, linters, and release
  # tooling. Keep those tools out of the packaged CLI's fixed-output vendor graph.
  postPatch = ''
    go mod edit \
      -droptool=github.com/axiomhq/cli/tools/gen-cli-docs \
      -droptool=github.com/axiomhq/cli/tools/loggen \
      -droptool=github.com/golangci/golangci-lint/v2/cmd/golangci-lint \
      -droptool=github.com/goreleaser/goreleaser/v2 \
      -droptool=golang.org/x/tools/cmd/stringer \
      -droptool=gotest.tools/gotestsum
  '';
}
