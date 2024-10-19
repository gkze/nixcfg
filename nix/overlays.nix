# TODO: upstream to Nixpkgs
{ inputs, system, ... }:
let
  inherit (builtins) fromJSON readFile replaceStrings;
  lockedFlake = fromJSON (readFile ../flake.lock);
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
(_final: prev: {
  alacritty-theme = prev.alacritty-theme.override { src = inputs.alacritty-theme; };

  bin =
    let
      binMeta = lockedFlake.nodes.bin;
    in
    prev.buildGoModule {
      pname = normalizeName binMeta.locked.repo;
      version = binMeta.original.ref;
      src = inputs.bin;
      vendorHash = "sha256-Nw0+kTcENp96PruQEBAdcfhubOEWSXKWGWrmWoKmgN0=";
    };

  nix-software-center = inputs.nix-software-center.packages.${system}.default;

  nixos-conf-editor = inputs.nixos-conf-editor.packages.${system}.default;

  sqruff =
    (prev.callPackage inputs.naersk {
      rustc = prev.rust-bin.selectLatestNightlyWith (toolchain: toolchain.default);
    }).buildPackage
      {
        name = normalizeName lockedFlake.nodes.sqruff.locked.repo;
        version = lockedFlake.nodes.sqruff.original.ref;
        src = inputs.sqruff;
      };

  uv = prev.uv.overrideAttrs { version = "0.4.20"; };

  # uv = prev.rustPlatform.buildRustPackage rec {
  #   pname = "uv";
  #   version = "0.4.7";
  #   src = inputs.uv;
  #   cargoLock = {
  #     lockFile = "${src}/Cargo.lock";
  #     allowBuiltinFetchGit = true;
  #   };
  #   buildInputs =
  #     [ prev.openssl ]
  #     ++ (
  #       with prev;
  #       lib.lists.optional stdenv.isDarwin (
  #         with darwin.apple_sdk.frameworks;
  #         [
  #           Security
  #           SystemConfiguration
  #         ]
  #       )
  #     );
  #   doCheck = false;
  #   nativeBuildInputs = with final; [
  #     cmake
  #     installShellFiles
  #     pkg-config
  #     rust-bin.stable.latest.default
  #   ];
  #   postInstall = ''
  #     installShellCompletion --cmd uv --zsh <($out/bin/uv generate-shell-completion zsh)
  #   '';
  #   OPENSSL_NO_VENDOR = 1;
  # };

  vimPlugins = prev.vimPlugins.extend (
    _: _: {
      bufresize-nvim = prev.vimUtils.buildVimPlugin {
        pname = normalizeName lockedFlake.nodes.bufresize-nvim.locked.repo;
        version = inputs.bufresize-nvim.rev;
        src = inputs.bufresize-nvim;
      };

      gitlab-nvim =
        let
          version = lockedFlake.nodes.gitlab-nvim.original.ref;
        in
        let
          gitlabNvimGo = prev.buildGoModule {
            pname = normalizeName lockedFlake.nodes.gitlab-nvim.locked.repo;
            inherit version;
            src = inputs.gitlab-nvim;
            vendorHash = "sha256-wYlFmarpITuM+s9czQwIpE1iCJje7aCe0w7/THm+524=";
          };
        in
        prev.vimUtils.buildVimPlugin {
          pname = normalizeName lockedFlake.nodes.gitlab-nvim.locked.repo;
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

      vim-bundle-mako = prev.vimUtils.buildVimPlugin {
        pname = normalizeName lockedFlake.nodes.vim-bundle-mako.locked.repo;
        version = inputs.vim-bundle-mako.rev;
        src = inputs.vim-bundle-mako;
      };

      lsp-signature-nvim = prev.vimUtils.buildVimPlugin {
        name = normalizeName lockedFlake.nodes.lsp-signature-nvim.locked.repo;
        version = inputs.lsp-signature-nvim.rev;
        src = inputs.lsp-signature-nvim;
      };
    }
  );

  yawsso = prev.python3Packages.buildPythonApplication {
    pname = normalizeName lockedFlake.nodes.yawsso.locked.repo;
    version = lockedFlake.nodes.yawsso.original.ref;
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
