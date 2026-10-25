# Environment that points regenerable tool output, and bun's whole install
# root, at the Build volume. build-volume.nix exports it to login shells and
# build-volume-launchd.nix to launchd, so GUI-launched tools agree with the
# shell. Values must be absolute: launchd does not expand variables.
let
  volume = import ./build-volume-identity.nix;
  cache = "${volume.path}/cache";
in
{
  XDG_CACHE_HOME = cache;
  # Bun's global installs link into its package cache, so the cache can only
  # move together with them. Pinning BUN_INSTALL also stops bun from deriving
  # its global directories from XDG_CACHE_HOME.
  BUN_INSTALL = "${volume.path}/bun";
  DENO_DIR = "${volume.path}/deno";
  GOCACHE = "${volume.path}/go/build";
  GOMODCACHE = "${volume.path}/go/mod";
  HOMEBREW_CACHE = "${cache}/Homebrew";
  MISE_CACHE_DIR = "${volume.path}/mise";
  PIP_CACHE_DIR = "${cache}/pip";
  PYTHONPYCACHEPREFIX = "${volume.path}/pycache";
  TURBO_CACHE_DIR = "${volume.path}/turbo";
  UV_CACHE_DIR = "${volume.path}/uv";
  # The uv cache and the venvs it fills sit on different APFS volumes, so
  # clones and hard links cannot cross; copy without the per-install warning.
  UV_LINK_MODE = "copy";
}
