{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    # sequoia
    # sequoia-chameleon-gnupg
    asdf-vm
    ast-grep
    aws-iam-authenticator
    aws-vault
    awscli2
    bash
    bat
    binutils
    bottom
    cmake
    colima
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
    exa
    fd
    findutils
    fzf
    gawk
    gh
    git
    gnumake
    gnupg
    gnused
    gnutar
    go
    gping
    helix
    jo
    jq
    kubectl
    kubeswitch
    less
    license-generator
    mercurial
    moreutils
    neovim
    nettle
    nil
    nix-du
    nixpkgs-fmt
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
    sentry-cli
    shellcheck
    starship
    stern
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
  ] ++ (lib.optionals pkgs.stdenv.hostPlatform.isDarwin [
    reattach-to-user-namespace
    pinentry_mac
  ]);
}
