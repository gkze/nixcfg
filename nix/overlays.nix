_: super:
let sources = import ./sources.nix; in {
  # TODO: upstream to Nixpkgs
  zellij = super.zellij.overrideAttrs (_: {
    version = "0.40.0";
    src = sources.zellij;
  });
  vimPlugins = super.vimPlugins.extend (_: _: {
    # TODO: upstream to Nixpkgs
    vim-bundle-mako = super.vimUtils.buildVimPlugin {
      pname = "vim-bundle-mako";
      version = sources.vim-bundle-mako.rev;
      src = sources.vim-bundle-mako;
    };
    # TODO: upstream to Nixpkgs
    mini-align = super.vimUtils.buildVimPlugin {
      pname = "mini-align";
      version = sources.mini-align.rev;
      src = sources.mini-align;
    };
  });
}
