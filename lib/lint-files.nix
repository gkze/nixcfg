{
  ruff = {
    regex = "(\\.(py|pyi)$|home/[^/]+/bin/(git-ignore|zen-folders|zen-profile-sync))";
    # Keep extensionless Python entrypoints in Ruff discovery via pyproject.toml,
    # but do not feed them through treefmt's Ruff formatter until the upstream
    # multi-exception formatting regression is fixed.
    globs = [
      "*.py"
      "*.pyi"
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

  css = {
    regex = "\\.css$";
    globs = [ "*.css" ];
    excludeGlobs = [
      ".direnv/**"
      ".venv/**"
      "node_modules/**"
      "result/**"
    ];
  };

  biome = {
    regex = "\\.(cjs|css|js|mjs)$";
    globs = [
      "*.css"
      "*.js"
      "*.cjs"
      "*.mjs"
    ];
    excludeGlobs = [
      ".direnv/**"
      ".venv/**"
      "node_modules/**"
      "result/**"
    ];
  };

  shell = {
    regex = "(\\.envrc|.*\\.(bash|sh|zsh))";
    globs = [
      ".envrc"
      "*.sh"
      "*.bash"
      "*.zsh"
    ];
    excludeRegex = [ "misc/zsh-plugins/go\\.plugin\\.zsh" ];
    excludeGlobs = [ "misc/zsh-plugins/go.plugin.zsh" ];
  };
}
