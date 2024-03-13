# TODO: upstream to Nixpkgs
{ inputs }: (_of: op: {
  alacritty-theme = op.alacritty-theme.override {
    src = op.alacritty-theme;
  };
  git-trim = op.stdenvNoCC.mkDerivation {
    pname = "git-trim";
    version = inputs.git-trim.rev;
    src = inputs.git-trim;
    installPhase = ''
      mkdir -p $out/bin
      cp git-trim $out/bin
      chmod +x $out/bin/git-trim
    '';
  };
  zellij = op.zellij.overrideAttrs (p: rec {
    version = "0.40.0";
    src = inputs.zellij;
    cargoDeps = p.cargoDeps.overrideAttrs {
      name = "${p.pname}-${version}-vendor.tar.gz";
      inherit src;
      outputHash = "sha256-LFNYEFl49ATlFV3/ikgW4syaLOAhG0fhoXW3CUa+bZo=";
    };
  });
  uv = op.rustPlatform.buildRustPackage rec {
    pname = "uv";
    version = "0.1.18";
    src = inputs.uv;
    cargoLock = {
      lockFile = "${src}/Cargo.lock";
      allowBuiltinFetchGit = true;
    };
    buildInputs = [ op.openssl ];
    cargoHash = "";
    doCheck = false;
    nativeBuildInputs = with op; [ cmake pkg-config ];
    OPENSSL_NO_VENDOR = 1;
  };
  vimPlugins = op.vimPlugins.extend (_f: _p: {
    vim-bundle-mako = op.vimUtils.buildVimPlugin {
      pname = "vim-bundle-mako";
      version = inputs.vim-bundle-mako.rev;
      src = inputs.vim-bundle-mako;
    };
    mini-align = op.vimUtils.buildVimPlugin {
      pname = "mini-align";
      version = inputs.mini-align.rev;
      src = inputs.mini-align;
    };
    nvim-treeclimber = op.vimUtils.buildVimPlugin {
      pname = "nvim-treeclimber";
      version = inputs.nvim-treeclimber.rev;
      src = inputs.nvim-treeclimber;
    };
  });
})
