# TODO: upstream to Nixpkgs
{ inputs, system, ... }:
let
  inherit (builtins) fromJSON readFile replaceStrings;
  nodes = (fromJSON (readFile ../flake.lock)).nodes;
  normalizeName =
    s:
    replaceStrings
      [
        "."
        "_"
      ]
      [
        "-"
        "-"
      ]
      s;
in
(_: prev: {
  alacritty-theme = prev.alacritty-theme.override { src = inputs.alacritty-theme; };

  bin =
    let
      binMeta = nodes.bin;
    in
    prev.buildGoModule {
      pname = normalizeName binMeta.locked.repo;
      version = binMeta.original.ref;
      src = inputs.bin;
      vendorHash = "sha256-Nw0+kTcENp96PruQEBAdcfhubOEWSXKWGWrmWoKmgN0=";
    };

  jinja-lsp =
    (prev.callPackage inputs.naersk {
      rustc = prev.rust-bin.selectLatestNightlyWith (toolchain: toolchain.default);
    }).buildPackage
      {
        name = normalizeName nodes.jinja-lsp.locked.repo;
        version = inputs.jinja-lsp.rev;
        src = inputs.jinja-lsp;
        CARGO_BUILD_RUSTFLAGS = "-Zunstable-options";
      };

  nix-software-center = inputs.nix-software-center.packages.${system}.default.overrideAttrs {
    buildInputs = with prev; [
      adwaita-icon-theme
      desktop-file-utils
      gdk-pixbuf
      glib
      gtk4
      gtksourceview5
      inputs.nixos-appstream-data.packages.${system}.nixos-appstream-data
      libadwaita
      libxml2
      openssl
      wayland
    ];
  };

  nixos-conf-editor = inputs.nixos-conf-editor.packages.${system}.default.overrideAttrs {
    buildInputs = with prev; [
      adwaita-icon-theme
      gdk-pixbuf
      glib
      gtk4
      gtksourceview5
      libadwaita
      openssl
      vte-gtk4
    ];
  };

  trdsql = prev.buildGoModule {
    pname = normalizeName nodes.trdsql.locked.repo;
    version = inputs.trdsql.rev;
    src = inputs.trdsql;
    vendorHash = "sha256-EnMs32/gbStmgHv0eTsuBUOqtYWe+96mLKmApFn1/yw=";
  };

  vimPlugins = prev.vimPlugins.extend (
    _: _: {
      bufresize-nvim = prev.vimUtils.buildVimPlugin {
        pname = normalizeName nodes.bufresize-nvim.locked.repo;
        version = inputs.bufresize-nvim.rev;
        src = inputs.bufresize-nvim;
      };

      cmp-dbee = prev.vimUtils.buildVimPlugin {
        pname = normalizeName nodes.cmp-dbee.locked.repo;
        version = inputs.cmp-dbee.rev;
        src = inputs.cmp-dbee;
      };

      gitlab-nvim =
        let
          version = nodes.gitlab-nvim.original.ref;
        in
        let
          gitlabNvimGo = prev.buildGoModule {
            pname = normalizeName nodes.gitlab-nvim.locked.repo;
            inherit version;
            src = inputs.gitlab-nvim;
            vendorHash = "sha256-wYlFmarpITuM+s9czQwIpE1iCJje7aCe0w7/THm+524=";
          };
        in
        prev.vimUtils.buildVimPlugin {
          pname = normalizeName nodes.gitlab-nvim.locked.repo;
          inherit version;
          src = inputs.gitlab-nvim;
          buildInputs = with prev.vimPlugins; [
            plenary-nvim
          ];
          nativeBuildInputs = with prev; [
            go
            gitlabNvimGo
          ];
          buildPhase = "mkdir -p $out && cp ${gitlabNvimGo}/bin/cmd $out/bin";
        };

      nvim-treehopper = prev.vimUtils.buildVimPlugin {
        pname = normalizeName nodes.nvim-treehopper.locked.repo;
        version = inputs.nvim-treehopper.rev;
        src = inputs.nvim-treehopper;
      };

      treewalker-nvim = prev.vimUtils.buildVimPlugin {
        pname = normalizeName nodes.treewalker-nvim.locked.repo;
        version = inputs.treewalker-nvim.rev;
        src = inputs.treewalker-nvim;
      };

      vim-bundle-mako = prev.vimUtils.buildVimPlugin {
        pname = normalizeName nodes.vim-bundle-mako.locked.repo;
        version = inputs.vim-bundle-mako.rev;
        src = inputs.vim-bundle-mako;
      };

      lsp-signature-nvim = prev.vimUtils.buildVimPlugin {
        name = normalizeName nodes.lsp-signature-nvim.locked.repo;
        version = inputs.lsp-signature-nvim.rev;
        src = inputs.lsp-signature-nvim;
      };
    }
  );

  yawsso = prev.python3Packages.buildPythonApplication {
    pname = normalizeName nodes.yawsso.locked.repo;
    version = nodes.yawsso.original.ref;
    src = inputs.yawsso;
    doCheck = false;
  };

  # zellij = prev.zellij.overrideAttrs (p: rec {
  #   version = "0.41.0";
  #   src = inputs.zellij;
  #   cargoDeps = p.cargoDeps.overrideAttrs {
  #     name = "${p.pname}-${version}-vendor.tar.gz";
  #     inherit src;
  #     outputHash = "sha256-XAJ6BnxlThY57W6jjHrOmIj8T1TZP59Yr0/uK+bzWd0=";
  #   };
  # });
})
