{
  final,
  outputs,
  selfSource,
  sources,
  ...
}:
# The updater hashes vendorHash through the shared overlay probe, so the
# package and the Go toolchain it builds with must resolve without the host's
# package set (Update run 39760093470).
{
  gh-current = final.callPackage ../../packages/gh-current {
    inherit outputs selfSource;
    go-toolchain = final.callPackage ../../packages/go-toolchain {
      inherit outputs;
      selfSource = sources.go-toolchain;
    };
  };
}
