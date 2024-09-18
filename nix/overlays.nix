# TODO: upstream to Nixpkgs
{ inputs, system, ... }: (final: prev: {
  alacritty-theme = prev.alacritty-theme.override { src = inputs.alacritty-theme; };

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

  sqruff = (prev.callPackage inputs.naersk {
    rustc = prev.rust-bin.selectLatestNightlyWith (toolchain: toolchain.default);
  }).buildPackage { src = inputs.sqruff; };

  sublime-kdl = prev.stdenvNoCC.mkDerivation {
    pname = "sublime-kdl";
    version = inputs.sublime-kdl.rev;
    src = inputs.sublime-kdl;
    installPhase = "cp -r $src $out";
  };

  superfile = inputs.superfile.packages.${system}.default;

  uv = prev.rustPlatform.buildRustPackage rec {
    pname = "uv";
    version = "0.4.7";
    src = inputs.uv;
    cargoLock = { lockFile = "${src}/Cargo.lock"; allowBuiltinFetchGit = true; };
    buildInputs = [ prev.openssl ]
      ++ (with prev; lib.lists.optional stdenv.isDarwin
      (with darwin.apple_sdk.frameworks; [ Security SystemConfiguration ]));
    doCheck = false;
    nativeBuildInputs = with final; [
      cmake
      installShellFiles
      pkg-config
      rust-bin.stable.latest.default
    ];
    postInstall = ''
      installShellCompletion --cmd uv --zsh <($out/bin/uv generate-shell-completion zsh)
    '';
    OPENSSL_NO_VENDOR = 1;
  };

  vimPlugins = prev.vimPlugins.extend (_: _: {
    bufresize-nvim = prev.vimUtils.buildVimPlugin {
      pname = "bufresize-nvim";
      version = inputs.bufresize-nvim.rev;
      src = inputs.bufresize-nvim;
    };

    # NOTE: does not work on nixbuild.net for some reason but works locally
    # codesnap-nvim = prev.vimUtils.buildVimPlugin {
    #   pname = "codesnap-nvim";
    #   version = inputs.codesnap-nvim.rev;
    #   src = inputs.codesnap-nvim;
    #   nativeBuildInputs = with prev; [ cargo rustc ];
    #   buildPhase = "make";
    # };

    # gitlab-nvim =
    #   let
    #     gitlabNvimGo = prev.buildGoModule {
    #       pname = "gitlab-nvim-go";
    #       version = inputs.gitlab-nvim.rev;
    #       src = inputs.gitlab-nvim;
    #       vendorHash = "sha256-wYlFmarpITuM+s9czQwIpE1iCJje7aCe0w7/THm+524=";
    #     };
    #   in
    #   prev.vimUtils.buildVimPlugin {
    #     pname = "gitlab-nvim";
    #     version = inputs.gitlab-nvim.rev;
    #     src = inputs.gitlab-nvim;
    #     buildInputs = with prev.vimPlugins; [
    #       diffview-nvim
    #       dressing-nvim
    #       nui-nvim
    #       plenary-nvim
    #     ];
    #     nativeBuildInputs = with prev; [ go gitlabNvimGo ];
    #     buildPhase = "mkdir -p $out && cp ${gitlabNvimGo}/bin/cmd $out/bin";
    #   };

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

    lsp-signature-nvim = prev.vimUtils.buildVimPlugin {
      name = "lsp-signature-nvim";
      version = inputs.lsp-signature-nvim.rev;
      src = inputs.lsp-signature-nvim;
    };

    render-markdown-nvim = prev.vimUtils.buildVimPlugin {
      name = "render-markdown-nvim";
      version = inputs.render-markdown-nvim.rev;
      src = inputs.render-markdown-nvim;
    };

  });

  yawsso = prev.python3Packages.buildPythonApplication {
    pname = "yawsso";
    version = "1.2.0";
    src = inputs.yawsso;
    doCheck = false;
  };

  zellij = prev.zellij.overrideAttrs (p: rec {
    version = "0.41.0";
    src = inputs.zellij;
    cargoDeps = p.cargoDeps.overrideAttrs {
      name = "${p.pname}-${version}-vendor.tar.gz";
      inherit src;
      outputHash = "sha256-0O+9Om4JA4YQW+gkoQW3t1ZaTSv3N5Tv/xP2ri3Sc2Q=";
    };
  });
})
