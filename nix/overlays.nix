# TODO: upstream to Nixpkgs
{ inputs, system, ... }: (final: prev: {
  alacritty-theme = prev.alacritty-theme.override {
    src = inputs.alacritty-theme;
  };
  bin = prev.buildGoModule {
    pname = "bin";
    version = "0.17.5";
    src = inputs.bin;
    vendorHash = "sha256-9kgenzKjo5Lc9JrEdXQlRocl17o4RyKrKuJAFoOEVwY=";
  };
  git-trim = prev.stdenvNoCC.mkDerivation {
    pname = "git-trim";
    version = inputs.git-trim.rev;
    src = inputs.git-trim;
    installPhase = ''
      mkdir -p $out/bin
      cp git-trim $out/bin
      chmod +x $out/bin/git-trim
    '';
  };
  nix-software-center = inputs.nix-software-center.packages.${system}.default;
  nixos-conf-editor = inputs.nixos-conf-editor.packages.${system}.default;
  # pants = final.callPackage ./pants.nix { nix-alien = inputs.nix-alien; };
  sublime-kdl = prev.stdenvNoCC.mkDerivation {
    pname = "sublime-kdl";
    version = inputs.sublime-kdl.rev;
    src = inputs.sublime-kdl;
    installPhase = "cp -r $src $out";
  };
  superfile = inputs.superfile.packages.${system}.default;
  uv = prev.rustPlatform.buildRustPackage rec {
    pname = "uv";
    version = "0.2.5";
    src = inputs.uv;
    cargoLock = {
      lockFile = "${src}/Cargo.lock";
      allowBuiltinFetchGit = true;
    };
    buildInputs = [ prev.openssl ]
      ++ (with prev; lib.lists.optional stdenv.isDarwin
      (with darwin.apple_sdk.frameworks; [ Security SystemConfiguration ]));
    doCheck = false;
    nativeBuildInputs = with final; [
      cmake
      pkg-config
      rust-bin.stable.latest.default
    ];
    OPENSSL_NO_VENDOR = 1;
  };
  vimPlugins = prev.vimPlugins.extend (_: _: {
    # NOTE: does not work on nixbuild.net for some reason but works locally
    codesnap-nvim = prev.vimUtils.buildVimPlugin {
      pname = "codesnap-nvim";
      version = inputs.codesnap-nvim.rev;
      src = inputs.codesnap-nvim;
      nativeBuildInputs = with prev; [ cargo rustc ];
      buildPhase = "make";
    };
    vim-bundle-mako = prev.vimUtils.buildVimPlugin {
      pname = "vim-bundle-mako";
      version = inputs.vim-bundle-mako.rev;
      src = inputs.vim-bundle-mako;
    };
    mini-align = prev.vimUtils.buildVimPlugin {
      pname = "mini-align";
      version = inputs.mini-align.rev;
      src = inputs.mini-align;
    };
    nvim-dbee = prev.vimUtils.buildVimPlugin {
      pname = "nvim-dbee";
      version = inputs.nvim-dbee.rev;
      src = inputs.nvim-dbee;
    };
    nvim-treeclimber = prev.vimUtils.buildVimPlugin {
      pname = "nvim-treeclimber";
      version = inputs.nvim-treeclimber.rev;
      src = inputs.nvim-treeclimber;
    };
  });
  yawsso = prev.python3Packages.buildPythonApplication {
    pname = "yawsso";
    version = "1.2.0";
    src = inputs.yawsso;
    doCheck = false;
  };
})
