# Development

This document covers the development shell, formatting, quality gates, and the
validation ladder. Read [architecture.md](architecture.md) for the layout, and
[packaging.md](packaging.md) for package work.

## Development shell

```bash
# Enter the dev environment (tooling plus pre-commit hooks)
nix develop

# Keep Python tooling in sync for editor and test workflows
uv sync
```

Python is 3.14 or newer.

## Formatting

`nix fmt` runs the `treefmt` wrapper. It formats Nix, Python, shell, YAML, TOML,
JSON, Go, protobuf, and Markdown. Run it after every edit.

The Markdown pass uses [mdformat](https://github.com/hukkin/mdformat) with the
GFM plugin and the frontmatter plugin. Skill files keep their YAML frontmatter.

Do not hand-edit `.pre-commit-config.yaml`. `git-hooks.nix` generates it from
`flake.nix`.

## Pre-commit hooks

```bash
prek run -a
```

Rules:

- Do not use `--no-verify` with `git commit`, `git push`, or a related flow.
- If a hook fails, fix the cause, or stop and ask.
- Do not push while a required local gate fails.
- Before a push, run `prek run -a` or the relevant CI-parity commands on the
  final tree.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/). `commitlint`
enforces the format. Recent subjects use these prefixes:

- `fix(ci)`, `fix(update)`, `fix(packages)`
- `feat(packages)`
- `refactor(update)`
- `chore`

Sign a commit with `git commit -S` when the user asks for a commit.

## Python quality gates

```bash
uv run ruff check --config pyproject.toml .
uv run ty check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report
```

Quality bar:

- Ruff runs with an aggressive rule set.
- `ty` warnings fail the check.
- The coverage floor is 100 percent.

Coverage exclusion policy:

- The floor stays at 100 percent.
- A line or branch may carry `# pragma: no cover -- <reason>` or
  `# pragma: no branch -- <reason>`. The reason is mandatory.
- An exclusion is valid only when a test merely restates the implementation.
  Examples:
  - a defensive re-raise
  - a `__main__` guard
  - an abstract method body
  - a platform branch that CI cannot execute
- If a branch can fail in a meaningful way, write a behavioral test instead. If
  a branch cannot execute at all, consider deleting it.
- A whole-module exemption requires a commented entry in `pyproject.toml`.
- Do not mock every collaborator just to walk a line.

Mutation testing:

```bash
uv run cosmic-ray init cosmic-ray.toml .cosmic-ray.sqlite
uv run cosmic-ray exec cosmic-ray.toml .cosmic-ray.sqlite
uv run cr-report .cosmic-ray.sqlite
```

## Python test philosophy

Python tests are the executable specification for maintained Python behavior.
Cover success paths, failure paths, and edge cases. Treat an uncovered branch as
a missing test or a design smell.

Use this order:

1. Pure Python unit tests.
1. Nix AST assertions with `nix-manipulator`.
1. Mocked Python-side subprocess or wrapper tests.
1. Real Nix evaluation only when the property cannot be established above.

Additional rules:

- Test generated Nix structurally. Prefer AST equality over string equality,
  unless the rendered text is itself the contract.
- Do not assert that a source file contains or omits a raw string fragment.
  Parse the source instead: Nix ASTs for Nix, `ast` or `libcst` for Python,
  format parsers for data files.
- Keep substring assertions for presentation output only, where the text is the
  user-facing contract.
- Do not use Nix evaluation to prove that Python emitted the right Nix. Parse
  the output and compare ASTs, or refactor the production code toward explicit
  AST builders.
- Mock `run_nix`, `run_nix_json`, or `subprocess.run` to cover control flow,
  argument construction, parsing, timeouts, and failures.

A test that needs real Nix evaluation uses the shared `lib.tests._nix_eval`
helper and declares `@pytest.mark.nix_eval(reason="Why evaluation is necessary")` locally. The helper rejects unmarked execution and enforces a
30-second timeout. Keep the expression limited to the semantic unit under test.
Host and closure checks belong in the native Nix checks.

Any new eval-based test needs a short docstring or comment that explains why
AST-only or pure-Python testing was insufficient.

## Schema contracts

Vendored Nix schemas define the wire contract. The public Python models keep
construction defaults, legacy full store paths, and ergonomic views of
experimental data. `lib/tests/test_nix_model_contracts.py` checks their fields,
enums, scalar constraints, numeric bounds, and deliberate compatibility
differences against those schemas.

Generated bindings are reference artifacts. `nixcfg schema verify` checks their
freshness. Freshness alone does not prove that the public models conform.

## Nix checks

```bash
# Formatting and evaluation
nix fmt
nix flake check --no-build --option allow-import-from-derivation false
nix flake check --all-systems --no-build --option allow-import-from-derivation false
nix flake check

# Host checks
nix build .#checks.aarch64-darwin.darwin-argus
nix build .#checks.aarch64-darwin.darwin-rocinante
nix build .#checks.aarch64-darwin.darwin-zeus
nix build .#homeConfigurations.george.activationPackage

# Package-focused work
nix build .#pkgs.<system>.<name>

# CSS and web assets
nix build .#checks.x86_64-linux.format-web-oxfmt
nix build .#checks.x86_64-linux.lint-web-oxlint
```

Apply a Darwin configuration locally only when the task truly requires it:

```bash
nh darwin switch --no-nom .#
```

## Validation ladder

Use the narrowest relevant check first.

```mermaid
flowchart LR
  fmt["nix fmt"] --> hooks["prek run -a"]
  hooks --> py["ruff, ty, pytest,<br/>coverage"]
  py --> flake["nix flake check"]
  flake --> hosts["darwin host checks"]
  hosts --> apply["nh darwin switch"]
```

Additional update and artifact checks:

- `uv run python -m lib.update.ci.crate2nix`
- `nix run .#nixcfg -- ci --help`
- `nix run .#nixcfg -- update --help`

## Related

- [architecture.md](architecture.md): layout and outputs
- [packaging.md](packaging.md): package and overlay discovery
- [source-modifications.md](source-modifications.md): upstream source edits
- [update/README.md](update/README.md): update subsystem
- [AGENTS.md](../AGENTS.md): full agent-facing rule set
