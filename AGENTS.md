# Agent Guide for `nixcfg`

`CLAUDE.md` is a symlink to this file. Edit `AGENTS.md`, not `CLAUDE.md`, and keep the symlink
intact.

## Start Here Every Time

Before changing code, do the smallest possible orientation pass:

1. Check the worktree:
   - `git status --short`
   - `git diff --stat`
   - `git diff --cached` if anything is staged
1. Identify the subsystem from the symptom.
1. If the task is part of ongoing work, inspect continuity sources:
   - `.dex/tasks.jsonl`
   - `git stash list`
   - recent Pi sessions under `~/.pi/agent/sessions/--Users-george-.config-nixcfg--/`
   - recent OpenCode sessions in `~/.local/share/opencode/opencode.db` for worktree
     `/Users/george/.config/nixcfg`
1. Prefer a narrow reproduction before a full build or full-closure apply.
1. Before handoff, review your own diff for regressions, generated-file drift, CI/update fallout,
   and platform-specific breakage.

Local history for this repo heavily favors root-cause analysis, workflow failure investigation, and
explicit self-review passes. Match that style.

## Non-Negotiable Guardrails

These are hard rules, not suggestions.

- Do not use `--no-verify` with `git commit`, `git push`, or related git flows in this repo.
- If hooks fail, fix the underlying issue or stop and ask the user. Do not bypass the hook as a
  shortcut.
- If partial staging or commit-splitting makes hooks awkward, use a safer flow instead: temporary
  patches, an isolated worktree, or a clean commit split that still allows hooks to run normally.
- Before push, do not rely on spot checks when repo hooks or CI define stricter gates. Run
  `prek run -a` or the exact relevant CI-parity commands on the final tree.
- Do not push changes while any known required local quality gate is failing.

## What This Repository Actually Is

This is not just a personal dotfiles repo. It is a mixed Nix + Python + Go codebase with several
public and semi-public surfaces:

- a personal `nix-darwin` + Home Manager flake
- reusable exported `darwin`, `home`, and `nixos` module sets
- a Python `nixcfg` CLI in `nixcfg.py` with substantial logic under `lib/`
- update/CI tooling under `lib/update/` and `lib/recover/`
- a schema/codegen toolchain under `schema_codegen.yaml`, `schemas/`, and `lib/schema_codegen/`
- an incubating Go workflow runner in `ghawfr/`
- custom packages and overlays under `packages/` and `overlays/`

Important entrypoints:

- `flake.nix`
- `default.nix`
- `lib.nix`
- `lib/exports.nix`
- `darwin/argus.nix`
- `darwin/rocinante.nix`
- `home/george/default.nix`
- `nixcfg.py`

The host entrypoints are intentionally thin. Shared behavior usually belongs in `modules/`,
`lib/lib.nix`, `default.nix`, or `lib/exports.nix`, not in a host file, unless the change is truly
machine-specific.

## Where To Look First

### Host / user configuration

- `darwin/` — host entrypoints
- `home/` — user config, scripts, app configs
- `modules/darwin/`, `modules/home/`, `modules/nixos/` — reusable module logic

### Packages / overlays / build failures

- `packages/`
- `overlays/`
- `packages/registry.nix`
- `packages/default.nix`
- `overlays/default.nix`
- `packages/*/sources.json`
- `packages/*/updater.py`
- `packages/*/Cargo.nix`
- `packages/*/crate-hashes.json`
- `packages/*/uv.lock`
- `overlays/*/sources.json`

### Update / CI / workflow failures

- `.github/workflows/ci.yml`
- `.github/workflows/update.yml`
- `lib/update/`
- `lib/recover/`
- `lib/update/ci/`
- `nix run .#nixcfg -- ci --help`
- `nix run .#nixcfg -- update --help`

### Public API / exported framework behavior

- `default.nix`
- `lib.nix`
- `lib/lib.nix`
- `lib/exports.nix`
- `lib/tests/test_default_nix_api.py`

### OpenCode / MCP / profile wiring

- `modules/home/opencode.nix`
- `modules/home/profiles.nix`
- `darwin/argus.nix`
- `darwin/rocinante.nix`

### Zen / Twilight browser work

- `modules/home/zen.nix`
- `home/george/bin/zen-folders`
- `home/george/zen/`
- `home/george/bin/zen-profile-sync`

### `ghawfr` work

- `ghawfr/`
- `ghawfr/README.md`
- `.ghawfr/` only when debugging runtime parity or local runner behavior

## Dominant Workstreams From Repo History

Recent Pi and OpenCode history for this worktree is dominated by five themes:

1. **Periodic Flake Update and CI failures**
1. **Argus system closure regressions**
1. **Package / overlay / crate2nix / hash drift breakage**
1. **OpenCode / MCP / profile configuration**
1. **Audit, simplification, and self-review before handoff**

That history suggests the right default behavior:

- start from the failing artifact, job, derivation, or package
- assume the visible failure may be downstream of a different root cause
- review staged or final diffs explicitly before finishing
- avoid broad rewrites when a targeted fix is available

A recurring pattern: failures often surface through `argus`, but the real bug lives in `packages/`,
`overlays/`, or `lib/update/`.

## Repo Patterns You Must Preserve

### 1. `flake.nix` pins are often intentional operational workarounds

`flake.nix` contains comments and pins for active upstream breakage or behavior differences.
Examples include temporary forks, pinned revisions, and disabled integrations. Do not casually
“clean up” those pins without checking why they were introduced.

### 2. Package discovery is structured, not ad hoc

- `packages/default.nix` and `packages/registry.nix` are part of the packaging architecture.
- Packages are auto-discovered.
- System constraints live in `packages/registry.nix`.
- Companion `crate2nix-src.nix` entries are discovered and exported too.

If you add or rename a package, check discovery and system constraints, not just the derivation
itself.

### 3. Overlays are fragment-based

`overlays/default.nix` auto-imports overlay fragments and combines them with helper overlays. Use
`overlays/` when you need `final` / `prev` semantics or are overriding nixpkgs behavior. Use
`packages/` for standalone `callPackage` derivations.

### 4. Source-backed packages use shared `selfSource` plumbing

The repo now centralizes sibling `sources.json` injection through:

- `lib/package-self-source.nix`
- `default.nix`
- `packages/default.nix`

If a package expects `selfSource`, make sure the sibling `sources.json` pattern still works through
the shared helper instead of inventing a one-off path.

### 5. Darwin app packaging has a shared pattern

For macOS GUI apps, prefer the shared pattern built around:

- `overlays/_lib/helpers/darwin-apps.nix`
- `lib/mac-apps.nix`
- `passthru.macApp`
- `nixcfg.macApps.systemApplications`

Do not add bespoke activation hacks for `/Applications` unless the shared pattern genuinely cannot
express the need.

### 6. `ghawfr/` source and `.ghawfr/` runtime state are different things

- `ghawfr/` is source code and tests.
- `.ghawfr/` is local runtime state, plans, artifacts, caches, worker state, and possibly sensitive
  material.

Do not mix them up in reviews or commits. If a `ghawfr` task leaves `.ghawfr/` debris around,
separate source changes from runtime leftovers.

### 7. `default.nix` / `lib.nix` / `lib/exports.nix` are API surfaces

The repo exports constructors and module sets for downstream use. If you touch these files, treat
them as public API and validate accordingly. Preserve constructor names and module export intent
unless the task explicitly changes API shape.

## Investigation Rules

### Prefer targeted root-cause analysis

Start from one of these before doing anything expensive:

- the failing workflow job
- the failing derivation log
- the specific package or overlay
- the generated artifact that drifted
- the exact staged diff that introduced the change

### Do not accept “build from source” as a substitute when caches should exist

If the user expects cached or prebuilt outputs, investigate why a cached path, version, or hash
diverged. Do not assume a from-source rebuild is an acceptable outcome.

### Preserve platform guards

This flake exports:

- `aarch64-darwin`
- `aarch64-linux`
- `x86_64-linux`

Do not remove or weaken platform guards casually. Many recurring failures in this repo are caused by
platform-specific drift.

### Treat hash and generated artifact drift as first-class issues

Common failure classes here:

- `sources.json` drift
- `uv.lock` drift
- `Cargo.nix` / `crate-hashes.json` drift
- `packages/superset/bun.nix` drift
- `npmDepsHash` / `denoDepsHash` / `vendorHash` mismatches
- workflow artifact naming/path mismatches

When touching workflow artifact contracts, update both the workflow and the verification/testing
side. Relevant places include:

- `lib/update/ci/workflow_steps.py`
- `lib/tests/test_ci_workflow_artifact_contracts.py`
- `nixcfg ci workflow verify-artifacts`

### When simplifying, remove accidental complexity without breaking discovery or exports

Recent history includes several simplification passes. Good simplifications here usually:

- deduplicate helper plumbing
- reduce repeated let-bindings in thin derivations
- move common package/update logic into shared helpers

Bad simplifications usually:

- hide platform distinctions
- bypass package/overlay discovery
- break exported constructors or module names
- inline generated values that should stay machine-maintained

## Task-Specific Guidance

### Fixing `argus` or `rocinante` build failures

Start with:

- `nix build .#checks.aarch64-darwin.darwin-argus`
- `nix build .#checks.aarch64-darwin.darwin-rocinante`

If the failure mentions a package, switch quickly into that package or overlay instead of staring
only at the host module.

### Adding or updating a package

Check these first:

- `packages/registry.nix`
- similar existing packages in `packages/` or `overlays/`
- `packages/default.nix`
- `lib/package-self-source.nix` for sibling `sources.json` / `selfSource` plumbing

For source-backed packages, make sure the full pattern is coherent:

- derivation file
- `sources.json` if needed
- `updater.py` if needed
- system constraint in `packages/registry.nix` if not universal
- validation build for the real target platform

### Updating CI or update workflow logic

Understand which phase you are touching. The update workflow is phase-structured: lock update,
version resolution, per-platform hash computation, merge, crate2nix refresh, then downstream
validation/build steps. Make changes phase-consciously.

### Working on OpenCode / MCP / profile setup

Use `modules/home/opencode.nix` and `modules/home/profiles.nix` as the source of truth. Do not
scatter MCP or profile behavior across unrelated host files unless the behavior is truly
host-specific.

### Working on Zen / Twilight

Use the dedicated module and repo-managed Zen tooling:

- `modules/home/zen.nix`
- `home/george/bin/zen-folders`
- `home/george/bin/zen-profile-sync`
- `home/george/zen-folders.yaml`

Remember that Zen folder reconciliation is stateful and interacts with a live browser profile.

## Validation Ladder

Use the narrowest relevant checks first. Do not jump straight to the most expensive command unless
the task needs it.

### Formatting and local hooks

- `nix fmt`
- `prek run -a`

### Python quality

- `uv run ruff check --config pyproject.toml .`
- `uv run ty check .`
- `uv run pytest`
- `uv run coverage run -m pytest && uv run coverage report`

Quality bar reminders:

- Python is `3.14+`
- Ruff is configured aggressively
- `ty` warnings fail
- coverage floor is `100%`

### Nix / flake / host outputs

- `nix flake check`
- `nix build .#checks.aarch64-darwin.darwin-argus`
- `nix build .#checks.aarch64-darwin.darwin-rocinante`
- `nix build .#homeConfigurations.george.activationPackage`
- `nix build .#pkgs.<system>.<name>` for package-focused work

Only use this when the task truly requires an actual local apply:

- `nh darwin switch --no-nom .`

### Workflow / update / artifact validation

- `nix run .#nixcfg -- ci workflow verify-artifacts`
- `uv run python -m lib.update.ci.crate2nix`
- relevant `nix run .#nixcfg -- ci ...` subcommands
- relevant `nix run .#nixcfg -- update ...` commands

### Go / `ghawfr`

- `cd ghawfr && go test ./...`

### Workflow hygiene tools enforced in CI

- `pinact run --check`
- `nix run --inputs-from . nixpkgs#actionlint`
- `yamllint -c .yamllint ...`

### CSS / web-y additions

If the task touches CSS or related frontend-ish repo assets, validate that too:

- `nix build .#checks.x86_64-linux.format-web-biome`

## Commit And Review Norms

Recent human-authored commits are overwhelmingly conventional-commit style. Prefer subjects like:

- `fix(ci): ...`
- `fix(update): ...`
- `fix(packages): ...`
- `feat(packages): ...`
- `refactor(update): ...`
- `chore: ...`

Notes:

- old `nix: Update ...` and `flake.lock: Update ...` commits exist, but they are
  older automation-shaped commits
- commitlint is enforced
- if the user explicitly asks you to commit, use `git commit -S`

Before finishing substantial work, do a review pass against your own diff. OpenCode history for this
repo shows very heavy use of explicit self-review subagents; emulate that discipline even when
working alone.

## Generated, Sensitive, And Local-State Files

### Do not hand-edit unless the task is explicitly about regeneration

- `.pre-commit-config.yaml`
- `packages/**/Cargo.nix`
- `packages/**/crate-hashes.json`
- `packages/**/sources.json`
- `packages/**/uv.lock`
- `overlays/**/sources.json`
- `packages/superset/bun.nix`
- `lib/**/_generated.py`

### Treat carefully

- `secrets.yaml`
- `.sops.yaml`
- `~/.pi`
- `~/.claude`
- `~/.local/share/opencode`
- `.ghawfr/` runtime state

Use local history for continuity, but do not surface credentials or paste sensitive local data back
into the conversation.

## Continuity Sources

If the user asks “where were we?”, “what is this worktree trying to do?”, or wants work formalized:

- inspect `git status`, `git diff`, and `git diff --cached`
- inspect `git stash list`
- inspect `.dex/tasks.jsonl`
- inspect recent Pi sessions under `~/.pi/agent/sessions/--Users-george-.config-nixcfg--/`
- inspect recent OpenCode sessions in `~/.local/share/opencode/opencode.db`
- for `ghawfr` work, inspect `.ghawfr/runs/` and related runtime artifacts

The local histories are genuinely useful here; this repo has a lot of long-running, iterative
debugging and architecture work.

## Usually Ignore Unless The Task Is About Them

These are commonly local state or generated artifacts, not source-of-truth code:

- `.direnv/`
- `.venv/`
- `node_modules/`
- `result/`
- `.pytest_cache/`
- `.ruff_cache/`
- `.coverage*`
- `.ghawfr/` for normal source changes

Exception: inspect `.ghawfr/` when the task is specifically about local workflow-runner parity,
cached run state, or generated runner artifacts.
