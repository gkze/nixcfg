# Currently unused - historical for packages that might be desired
{ pkgs, ... }: {
  environment.systemPackages = with pkgs; [
    # sequoia
    # sequoia-chameleon-gnupg
    ast-grep
    bat
    binutils
    bottom
    cmake
    coreutils
    curl
    curlie
    dasel
    delta
    difftastic
    diffutils
    direnv
    dprint
    du-dust
    duf
    envchain
    eza
    fd
    findutils
    fzf
    gawk
    gh
    git
    git-trim
    gnumake
    gnupg
    gnused
    gnutar
    go
    gping
    helix
    jo
    jq
    less
    license-generator
    llvm
    man-pages
    man-pages-posix
    mercurial
    moreutils
    neovim
    nettle
    nil
    nix-du
    nixd
    nixpkgs-fmt
    nls
    nodejs
    ookla-speedtest
    openssh
    ouch
    pinentry
    pipx
    pkg-config
    poetry
    python311
    rage
    ripgrep
    rsync
    rustup
    sd
    starship
    subversion
    tmux
    tokei
    tree
    vim
    watch
    watchman
    yaml-language-server
    yamlfmt
    yq-go
    zellij
    zoxide
    zsh
  ] ++ (lib.optionals pkgs.stdenv.isDarwin [
    pinentry_mac
    reattach-to-user-namespace
  ]);
}
