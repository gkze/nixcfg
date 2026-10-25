# Regenerable tool output on argus lives on the Build volume: an APFS sparse
# bundle (~/Build.sparsebundle) that the user LaunchAgent
# com.gkze.build-volume attaches at login, kept out of Spotlight by the host's
# `darwinDefaults.spotlight.unindexedVolumes`. Caches and build intermediates
# go there, plus bun's whole install root (see build-volume-env.nix). Other
# installed tools (mise installs, uv tools and Pythons, npm globals, cargo and
# go binaries) stay in the home directory, so a detached volume costs them
# rebuild time, never an installation; bun's global CLIs are unavailable until
# the volume is attached.
#
# Redirects owned elsewhere, by files these tools read directly:
# - npm cache: `cache=` in ~/.npmrc (also holds registry auth, so unmanaged)
# - pnpm store: `store-dir` in ~/Library/Preferences/pnpm/config.yaml
# - cargo intermediates: `build.build-dir` in ~/.cargo/config.toml
# - bun package cache: `[install.cache] dir` in ~/.bunfig.toml (bun otherwise
#   follows ~/.npmrc's cache=)
# - Xcode: IDECustomDerivedDataLocation in the com.apple.dt.Xcode defaults
#
# Cargo has no setting for its registry cache apart from moving all of
# CARGO_HOME, so it stays in ~/.cargo.
{ config, lib, ... }:
let
  env = import ./build-volume-env.nix;
in
{
  # HM modules that derive paths from xdg.cacheHome agree with the shell.
  xdg.cacheHome = env.XDG_CACHE_HOME;
  # Home Manager otherwise materializes its cache placeholder relative to $HOME,
  # which creates ~/Volumes/Build/cache for this absolute external cache path.
  home.file."${env.XDG_CACHE_HOME}/.keep".enable = false;
  # LaunchAgents, PATH entries and tool configs written by bun-installed CLIs
  # hold absolute ~/.bun paths; keep them resolving to the relocated root.
  home.file.".bun".source = config.lib.file.mkOutOfStoreSymlink env.BUN_INSTALL;
  # Home Manager otherwise moves an unmanaged ~/.bun to .bun.backup before
  # linking. Stop before writeBoundary if that would strand populated data
  # behind an empty target; the guard prints a non-overwriting recovery command.
  home.activation.buildVolumeBunMigration = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
    buildVolumeBunInstall=${lib.escapeShellArg env.BUN_INSTALL}
    ${builtins.readFile ./build-volume-bun-migration.sh}
  '';

  home.sessionVariables = env;
}
