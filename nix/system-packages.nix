{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    # sequoia
    # sequoia-chameleon-gnupg
    asdf-vm
    aws-iam-authenticator
    aws-vault
    awscli2
    bash
    bat
    binutils
    bottom
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
    less
    mercurial
    moreutils
    neovim
    nil
    nixpkgs-fmt
    nodejs
    ookla-speedtest
    ouch
    pinentry_mac
    pipx
    poetry
    python311
    rage
    ripgrep
    rustup
    sd
    sentry-cli
    shellcheck
    starship
    stern
    subversion
    tmux
    tree
    vim
    watch
    watchman
    yamlfmt
    yq-go
    zellij
    zoxide
    zsh
  ] ++ (lib.optionals pkgs.stdenv.hostPlatform.isDarwin [
    reattach-to-user-namespace
  ]);
}
