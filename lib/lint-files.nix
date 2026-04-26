let
  pythonScriptPaths = [
    "home/george/bin/git-ignore"
    "home/george/bin/zentool"
  ];
  pythonRuntimeSensitiveHelpers = [
    "lib/crate2nix_tauri_env_rewrite.py"
    "lib/nix/schemas/_fetch.py"
    "lib/update/ci/build_shared_closure.py"
    "lib/update/ci/profile_generations.py"
    "lib/update/updaters/_sourcefile.py"
    "packages/sculptor/updater.py"
  ];
in
{
  python = {
    inherit pythonScriptPaths pythonRuntimeSensitiveHelpers;
    pyupgradePaths = [
      "*.py"
      "*.pyi"
    ]
    ++ pythonScriptPaths;
    compilePaths = [
      "**/*.py"
      "**/*.pyi"
    ]
    ++ pythonScriptPaths;
    ruffMutationExcludes = pythonScriptPaths ++ pythonRuntimeSensitiveHelpers;
  };

  ruff = {
    regex = "(\\.(py|pyi)$|home/[^/]+/bin/(git-ignore|zentool))";
    # Keep extensionless Python entrypoints in Ruff discovery via pyproject.toml.
    globs = [
      "*.py"
      "*.pyi"
    ];
  };

  nix = {
    regex = "\\.nix$";
    globs = [ "*.nix" ];
    excludeRegex = [ "(^|.*/)Cargo\\.nix$" ];
    excludeGlobs = [ "**/Cargo.nix" ];
  };

  yaml = {
    regex = "(\\.ya?ml$|(^|.*/)\\.(yamlfmt|yamllint)$)";
    globs = [
      "*.yaml"
      "*.yml"
      ".yamlfmt"
      ".yamllint"
    ];
  };

  toml = {
    regex = "(\\.toml$|(^|.*/)uv\\.lock$)";
    globs = [
      "*.toml"
      "uv.lock"
      "**/uv.lock"
    ];
  };

  css = {
    regex = "\\.css$";
    globs = [ "*.css" ];
    excludeGlobs = [
      ".direnv/**"
      "misc/zellij-plugin-wasm-ts/assembly/index.ts"
      ".venv/**"
      "node_modules/**"
      "result/**"
    ];
  };

  biome = {
    regex = "(\\.(cjs|css|js|json|jsonc|ts)$|(^|.*/)flake\\.lock$)";
    globs = [
      "*.css"
      "*.js"
      "*.cjs"
      "commitlint.config.ts"
      "misc/zellij-plugin-wasm-ts/scripts/*.ts"
      "packages/**/*.ts"
      "*.json"
      "*.jsonc"
      "flake.lock"
    ];
    excludeGlobs = [
      ".direnv/**"
      ".venv/**"
      "node_modules/**"
      "result/**"
    ];
  };

  jsonl = {
    regex = "\\.jsonl$";
    globs = [ "*.jsonl" ];
  };

  go = {
    regex = "\\.go$";
    globs = [ "*.go" ];
  };

  goMod = {
    regex = "(^|.*/)go\\.mod$";
    globs = [
      "go.mod"
      "**/go.mod"
    ];
  };

  markdown = {
    regex = "\\.md$";
    globs = [ "*.md" ];
  };

  protobuf = {
    regex = "\\.proto$";
    globs = [ "*.proto" ];
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

  twilightAutoconfig = {
    regex = "(^|.*/)twilight\\.cfg$";
    globs = [ "home/george/zen/autoconfig/twilight.cfg" ];
  };

  text = {
    globs = [
      ".editorconfig"
      ".gitignore"
      "**/.gitignore"
      ".root"
      "LICENSE"
      "NIXOS_VERSION"
      "misc/zellij-plugin-wasm-ts/assembly/index.ts"
      "*.cfg"
      "*.jsonl"
      "*.patch"
      "*.proto"
      "*.svg"
      "*.typed"
      "bun.lock"
      "**/bun.lock"
      "go.mod"
      "**/go.mod"
      "go.sum"
      "**/go.sum"
    ];
  };
}
