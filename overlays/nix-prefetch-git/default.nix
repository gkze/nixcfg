{ final, prev, ... }:
let
  # Materialize only the patch contents so unrelated repository changes do not
  # invalidate the expensive Bash build.
  dynamicPipeHeredocPatch = builtins.toFile "bash-darwin-dynamic-pipe-heredoc.patch" (
    builtins.readFile ./bash-darwin-dynamic-pipe-heredoc.patch
  );
  # Backport GNU Bash devel commit 03e7298 while nixpkgs' released Bash still
  # assumes Darwin's pipe capacity is constant. Keep this as a separately named
  # Bash so only consumers that need the workaround use it.
  bashWithDynamicPipeHeredocFix =
    if prev.stdenv.hostPlatform.isDarwin then
      prev.bashNonInteractive.overrideAttrs (old: {
        # The builder still runs under nixpkgs' unpatched Bash. Force its pre-5.1
        # tempfile behavior so configure cannot hit the same heredoc deadlock while
        # compiling the fixed interpreter.
        env = (old.env or { }) // {
          BASH_COMPAT = "5.0";
        };
        patches = (old.patches or [ ]) ++ [ dynamicPipeHeredocPatch ];
        # Both generated configure and its source are backported. Keep configure
        # newest so make does not require Autoconf to regenerate an identical file.
        postPatch = (old.postPatch or "") + ''
          touch configure
        '';
      })
    else
      final.bashNonInteractive;
  # makeWrapper normally emits launchers with nixpkgs' global runtime shell.
  # Give this package a private hook whose substitution uses the scoped Bash,
  # so both the public launcher and wrapped script receive the backport without
  # overriding Bash for any other package.
  makeWrapperWithDynamicPipeHeredocFix = prev.makeShellWrapper.overrideAttrs (_: {
    shell = "${bashWithDynamicPipeHeredocFix}/bin/bash";
  });
  nixPrefetchGitWithDynamicPipeHeredocFix = prev.nix-prefetch-git.override {
    bashNonInteractive = bashWithDynamicPipeHeredocFix;
    makeWrapper = makeWrapperWithDynamicPipeHeredocFix;
  };
  gnutarBuilderBash = final.buildPackages.bash-dynamic-pipe-heredoc;
  gnutarWithDynamicPipeHeredocFix =
    if prev.stdenv.buildPlatform.isDarwin then
      prev.gnutar.overrideAttrs (old: {
        # The generic setup script resets CONFIG_SHELL from its own SHELL, while
        # Autoconf may re-exec generated configure scripts through that value.
        # Use the patched interpreter as GNU tar's real derivation builder and
        # re-establish it before any source phase, without replacing stdenv for
        # unrelated packages or creating a new Darwin bootstrap fixed point.
        realBuilder = "${gnutarBuilderBash}/bin/bash";
        preUnpack = (old.preUnpack or "") + ''
          export SHELL=${gnutarBuilderBash}/bin/bash
          export CONFIG_SHELL="$SHELL"
        '';
        passthru = (old.passthru or { }) // {
          dynamicPipeHeredocBuilder = "${gnutarBuilderBash}/bin/bash";
        };
      })
    else
      prev.gnutar;
in
{
  bash-dynamic-pipe-heredoc = bashWithDynamicPipeHeredocFix;
  gnutar-dynamic-pipe-heredoc = gnutarWithDynamicPipeHeredocFix;
  nix-prefetch-git =
    if prev.stdenv.hostPlatform.isDarwin then
      nixPrefetchGitWithDynamicPipeHeredocFix
    else
      prev.nix-prefetch-git;
}
