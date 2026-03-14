{
  ruff = {
    regex = "(\\.(py|pyi)$|home/[^/]+/bin/git-ignore)";
    globs = [
      "*.py"
      "*.pyi"
      "home/*/bin/git-ignore"
    ];
  };

  nix = {
    excludeRegex = [ "(^|.*/)Cargo\\.nix$" ];
    excludeGlobs = [ "**/Cargo.nix" ];
  };

  toml = {
    regex = "\\.toml$";
    globs = [ "*.toml" ];
  };

  shell = {
    regex = "(\\.envrc|misc/zsh-plugins/.*\\.zsh)";
    globs = [
      ".envrc"
      "misc/zsh-plugins/*.zsh"
    ];
    excludeRegex = [ "misc/zsh-plugins/go\\.plugin\\.zsh" ];
    excludeGlobs = [ "misc/zsh-plugins/go.plugin.zsh" ];
  };
}
