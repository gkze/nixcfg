let
  pythonScriptPaths = [
    "home/george/bin/git-ignore"
    "modules/home/zen/zentool"
    # Extensionless entrypoint: without this it is checked by ruff/ty (via
    # pyproject's extend-include) but skipped by the compile and pyupgrade passes.
    "home/george/bin/tcctool"
  ];
  pythonPyupgradeExcludes = [
    # Actions bootstraps Nix with the hosted image's Python 3.12.
    "lib/update/ci/jobs.py"
  ];
  ruffMutationExcludes =
    (builtins.fromTOML (builtins.readFile ../pyproject.toml)).tool.ruff.format.exclude;
in
{
  # File selection is glob-driven: every consumer (treefmt, repo-checks,
  # flake.nix) reads `globs`/`excludeGlobs`. Regex fields used to shadow these
  # without a reader, which made the regex look authoritative when it was not.
  python = {
    inherit pythonPyupgradeExcludes pythonScriptPaths;
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
    inherit ruffMutationExcludes;
  };

  ruff = {
    # Keep extensionless Python entrypoints in Ruff discovery via pyproject.toml.
    globs = [
      "*.py"
      "*.pyi"
    ];
  };

  nix = {
    globs = [ "*.nix" ];
    excludeGlobs = [ "**/Cargo.nix" ];
  };

  yaml = {
    globs = [
      "*.yaml"
      "*.yml"
      ".yamlfmt"
      ".yamllint"
    ];
  };

  toml = {
    globs = [
      "*.toml"
      "uv.lock"
      "**/uv.lock"
    ];
  };

  css = {
    globs = [ "*.css" ];
    excludeGlobs = [
      ".direnv/**"
      ".venv/**"
      "node_modules/**"
      "result/**"
    ];
  };

  oxfmt = {
    globs = [
      "*.css"
      "*.js"
      "*.cjs"
      "commitlint.config.ts"
      "packages/**/*.ts"
      "*.json"
      "*.jsonc"
      "flake.lock"
    ];
    excludeGlobs = [
      "schemas/codegen/testdata/lockfile-golden/expected.codegen.lock.json"
      ".direnv/**"
      ".claude/worktrees/**"
      ".venv/**"
      "node_modules/**"
      "result/**"
      "result-*"
    ];
  };

  oxlint = {
    globs = [
      "*.js"
      "*.cjs"
      "*.ts"
    ];
    excludeGlobs = [
      ".direnv/**"
      ".claude/worktrees/**"
      ".venv/**"
      "node_modules/**"
      "result/**"
      "result-*"
    ];
  };

  jsonl = {
    globs = [ "*.jsonl" ];
  };

  go = {
    globs = [ "*.go" ];
  };

  goMod = {
    globs = [
      "go.mod"
      "**/go.mod"
    ];
  };

  markdown = {
    globs = [ "*.md" ];
    excludeGlobs = [ ];
  };

  protobuf = {
    globs = [ "*.proto" ];
  };

  shell = {
    globs = [
      ".envrc"
      "*.sh"
      "*.bash"
      "*.zsh"
    ];
  };

  twilightAutoconfig = {
    globs = [ "home/george/zen/autoconfig/twilight.cfg" ];
  };

  text = {
    globs = [
      ".editorconfig"
      ".gitattributes"
      ".gitignore"
      "**/.gitignore"
      ".root"
      "LICENSE"
      "NIXOS_VERSION"
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
