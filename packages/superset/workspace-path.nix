# Copy one generated Superset workspace into the store when the pinned
# upstream tree still contains it.
#
# `bun.nix` is generated for one upstream commit. A later pin can delete a
# workspace (Update 42032332040: `apps/admin` at `624219c`) while the checked-in
# `bun.nix` still names it. `builtins.path` throws during flake evaluation and
# aborts every Darwin closure, which is not a package-scoped failure.
# `bunDeps` keeps only store paths, so a missing workspace is omitted and
# evaluation continues. The update hold refuses to promote that mismatch.
{ lib }:
{
  generatedRoot,
  upstreamSrc,
}:
path:
let
  pathString = toString path;
  relativePath = lib.removePrefix generatedRoot pathString;
  upstreamPath = upstreamSrc + "/${relativePath}";
in
if !lib.hasPrefix generatedRoot pathString then
  throw "Superset bun.nix referenced a path outside its generated workspace: ${pathString}"
else if builtins.pathExists upstreamPath then
  # `builtins.path` without a filter yields the same store path as
  # `copyPathToStore` (an accept-all `filterSource`) but does not call a Nix
  # lambda for every file in the workspace tree.
  builtins.path {
    path = upstreamPath;
    name = baseNameOf relativePath;
  }
else
  null
