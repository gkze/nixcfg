{ prev, ... }:
{
  nix-index-unwrapped = prev.nix-index-unwrapped.overrideAttrs (old: {
    # nixpkgs removed nodePackages in March 2026, but the currently packaged
    # nix-index still queries it via EXTRA_SCOPES.
    # TODO: remove this overlay once nixpkgs packages nix-index with upstream
    # commit 33ce2347e8c68afe45adcce9b22ad641ca9a35f7 (or any later rev that
    # drops nodePackages from EXTRA_SCOPES).
    postPatch = (old.postPatch or "") + ''
      substituteInPlace src/listings.rs \
        --replace-fail 'pub const EXTRA_SCOPES: [&str; 5] = [' 'pub const EXTRA_SCOPES: [&str; 4] = [' \
        --replace-fail '    "nodePackages",' ""
    '';
  });
}
