# Compatibility overlay: nixpkgs renamed neovim's `lua` arg to `lua5_1`
# This must be applied BEFORE neovim-nightly-overlay to intercept the old arg
# Tracking: https://github.com/nix-community/neovim-nightly-overlay/pull/1166
# TODO: Remove once neovim-nightly-overlay merges the fix
_: prev: {
  neovim-unwrapped =
    let
      original = prev.neovim-unwrapped;
      # Wrapper that translates lua -> lua5_1 before calling the real override
      compatOverride =
        args:
        let
          fixedArgs =
            if args ? lua then (builtins.removeAttrs args [ "lua" ]) // { lua5_1 = args.lua; } else args;
        in
        original.override fixedArgs;
    in
    # Replace the override function while preserving everything else
    original
    // {
      override = compatOverride;
      overrideAttrs =
        f:
        (original.overrideAttrs f)
        // {
          override = compatOverride;
        };
    };
}
