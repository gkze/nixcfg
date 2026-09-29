# nixcfg

[![License][c]][d] [![Last commit][e]][f]
[![Commit activity][g]][f] [![Nix flake][h]][i]
[![Platforms][j]][k]

Unified Nix flake for macOS hosts, Home Manager user configuration, and reusable
module building blocks.

This repository is still tailored to George's machines, user profile, and
workflows today. Ongoing work is focused on separating those personal defaults
into reusable framework primitives and a standalone library of modules.

## Current state

- Primary focus is [`nix-darwin`](https://github.com/LnL7/nix-darwin) plus
  [Home Manager](https://github.com/nix-community/home-manager).
- Active Darwin hosts: [`argus`](darwin/argus.nix) and
  [`zeus`](darwin/zeus.nix) (work profile enabled), plus
  [`rocinante`](darwin/rocinante.nix) (personal profile).
- Active Home Manager output: [`homeConfigurations.george`](flake.nix).
- Exported systems: [`aarch64-darwin`](flake.nix),
  [`aarch64-linux`](flake.nix), [`x86_64-linux`](flake.nix).
- NixOS modules are exported, but there are currently no
  [`nixosConfigurations`](flake.nix) defined.

## Repository layout

- [`darwin/`](darwin/): host entrypoints.
- [`home/`](home/): user configuration ([`home/george`](home/george/)).
- [`modules/`](modules/): reusable modules
  ([`common`](modules/common.nix), [`darwin`](modules/darwin/),
  [`nixos`](modules/nixos/), [`home`](modules/home/)).
- [`packages/`](packages/): custom package outputs
  ([`axiom-cli`](packages/axiom-cli/),
  [`codex-desktop`](packages/codex-desktop/),
  [`conductor`](packages/conductor/), [`droid`](packages/droid/),
  [`gogcli`](packages/gogcli/),
  [`homebrew-zsh-completion`](packages/homebrew-zsh-completion/),
  [`linear-cli`](packages/linear-cli/),
  [`nix-manipulator`](packages/nix-manipulator/),
  [`scratch`](packages/scratch/), [`sculptor`](packages/sculptor/),
  [`sublime-kdl`](packages/sublime-kdl.nix),
  [`superset`](packages/superset/), [`toad`](packages/toad/)).
- [`overlays/`](overlays/): package overrides and source pinning.
- [`lib/`](lib/): Python libraries for update tooling and Nix model/schema
  helpers.
- [`nixcfg.py`](nixcfg.py): Typer CLI exposed through
  [`nix run .#nixcfg -- ...`](nixcfg.py).

## Install and apply

1. Install vanilla Nix using the official multi-user installer:

   ```bash
   curl -L https://nixos.org/nix/install | sh -s -- --daemon
   ```

   Then either open a new terminal or load the daemon profile in the current
   shell:

   ```bash
   . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
   ```

1. Clone this repository to [`~/.config/nixcfg`](.).

1. Bootstrap nix-darwin with the intended host selected explicitly (the
   pre-switch macOS hostname is not yet declarative):

   ```bash
   cd ~/.config/nixcfg
   sudo /nix/var/nix/profiles/default/bin/nix \
     --extra-experimental-features 'nix-command flakes' \
     run --inputs-from . nix-darwin#darwin-rebuild -- \
     switch --flake .#zeus
   ```

1. After the first successful switch, use the managed `nh` command for normal
   updates:

   ```bash
   nh darwin switch --no-nom .#zeus
   ```

Useful build-only checks:

```bash
nix build .#checks.aarch64-darwin.darwin-argus
nix build .#checks.aarch64-darwin.darwin-rocinante
nix build .#checks.aarch64-darwin.darwin-zeus
nix build .#homeConfigurations.george.activationPackage
```

## Day-to-day commands

```bash
# Enter the dev environment (tooling + pre-commit hooks)
nix develop

# Keep Python tooling in sync for editor/test workflows
uv sync

# Format and evaluate. The default no-build pass checks the current system;
# the all-systems pass is the full purity matrix. Neither may inspect outputs.
nix fmt
nix flake check --no-build --option allow-import-from-derivation false
nix flake check --all-systems --no-build --option allow-import-from-derivation false
nix flake check

# Pre-commit hooks
prek run -a

# Individual quality checks
uv run coverage run -m pytest
uv run coverage report

# Python test suite
uv run pytest

# Mutation testing with cosmic-ray
uv run cosmic-ray init cosmic-ray.toml .cosmic-ray.sqlite
uv run cosmic-ray exec cosmic-ray.toml .cosmic-ray.sqlite
uv run cr-report .cosmic-ray.sqlite
```

Python tests compare parsed source structure or behavior. A test that needs
actual Nix evaluation uses the shared `lib.tests._nix_eval` helper and declares
`@pytest.mark.nix_eval(reason="Why evaluation is necessary")` locally. The
helper rejects unmarked execution and enforces a 30-second timeout. Keep the
expression limited to the semantic unit under test; host and closure checks
belong in the native Nix checks.

Vendored Nix schemas define the wire contract. The public Python models retain
construction defaults, legacy full store paths, and ergonomic views of
experimental data. `lib/tests/test_nix_model_contracts.py` checks their fields,
enums, scalar constraints, numeric bounds, and deliberate compatibility
differences against those schemas. Generated bindings remain reference
artifacts, with freshness checked by `nixcfg schema verify`; freshness alone
does not establish conformance of the public models.

## Update tooling

Updates and package-artifact maintenance are explicit CLI operations. The
[Update workflow](.github/workflows/update.yml) prepares and validates one candidate
on hosted macOS ARM64, Linux ARM64 and Linux x86_64 builders, then proposes the
validated changes in a pull request. See [CI operation](docs/update-ci.md).

For one bounded local repair attempt, use `nixcfg update --repair codex TARGET`
(or `--repair copilot` with that CLI installed and authenticated). Both attempts run
in isolation; a repair starts fresh execution and must pass updater validation,
repository quality gates, and root builds before atomic promotion. `--check` keeps
the result unapplied and `--patch PATH` exports it. Normal updates retain DBOS resume;
repair mode requires a fresh run and cannot be combined with `--resume` or `--run-id`.

```bash
nix run .#nixcfg -- --help
nix run .#nixcfg -- update --help
nix run .#nixcfg -- ci --help
nix run .#nixcfg -- schema --help
```

`nixcfg update` prepares changes in an isolated copy of the checkout. Every run
that would change the checkout must first build all configured root closures.
A root closure contains a system or Home Manager configuration and all its
dependencies. A candidate identical to the checkout changes nothing, so it
skips the build gate. Root discovery follows `darwin/*.nix`, `nixos/*.nix`, and
`home/*/default.nix`. It does not require a separate list of host names.

If a root build fails, the updater leaves the candidate changes outside the
checkout. The updater also rejects source changes that invalidate the tested
snapshot. It preserves existing user edits and does not activate a system or
Home Manager configuration.

Targets are promoted individually. A target that fails, whether while
resolving, hashing, or validating its derivations, is withheld together with
the targets coupled to it: companion and aggregate sources, and every target
hashed against the same flake input when that input moved during the run,
since flake.nix and flake.lock revert as a unit. The remaining candidate is
validated again, at most three rounds, and then promoted. Any failure still
sets a non-zero exit status, and the summary lists updated, withheld, and
failed targets separately. `--strict` restores the all-or-nothing behavior in
which any failure discards every candidate.

Updater coroutines return their typed results directly and send progress through
an awaited `emit` callback. Hooks receive an explicit `UpdateContext`.
Intermediate values use ordinary returns. `--check` performs the same
candidate preparation and validation, then skips promotion to the checkout.

A full update requires valid, current hashes for every requested platform.
When recomputation is needed, a failed Linux hash probe cannot count as success
by retaining its previous hash. Use `--native-only` to request only the current
platform explicitly.
Freshness checks require all requested hashes and include each platform's
derivation fingerprint, so a Darwin fingerprint cannot hide Linux-only changes.
Existing multi-platform sources with a native-only fingerprint are refreshed
once to establish this complete fingerprint.

Use `--timings` (optionally with `--json`) to inspect operation time, admission
waits, and cache reuse. See [update runtime and durability](docs/update-runtime.md)
for independent resource limits, cache invalidation, and validation behavior.

Progress is visible by default. On a terminal the live panel shows only the
targets currently in flight, with finished targets printed above it, and ends
with a status line naming the phase, done/running/failed counts, and the
longest-running target with its idle time. Plain output (pipes, `--tty off`,
Zellij) prints phase headers, per-target results, and that status line every
30 seconds (`UPDATE_HEARTBEAT_INTERVAL`). A target that produces no output for
five minutes (`UPDATE_INACTIVITY_WARNING_SECONDS`) is flagged as stalled. The
derivation and root closure validation phases report the running command, the
derivation being built, and idle time through the same status line; add
`--verbose` to stream the underlying build logs.

Every CLI update is a DBOS workflow backed by one SQLite database at
`$XDG_STATE_HOME/nixcfg/update/runs/<run-id>/run.sqlite`
(`UPDATE_RUN_LOG_DIR` overrides the root). Resume an interrupted run with
`nixcfg update --resume RUN_ID`: it reuses the original inputs, completed source
work and validation results, and reconstructs candidate files from SQLite.
The original repository, platform and updater runtime must still match.
Use a new invocation to retry a completed failure or discover newer releases.

`nixcfg update --status [RUN_ID]` reports the latest or a named run, including
DBOS execution status and heartbeat freshness. A pending workflow with stale
activity may be interrupted; it is not evidence of a live worker. SQLite also
holds structured diagnostics and status snapshots, replacing `events.jsonl`,
`run.json` and `state.json`. `output.log` remains available for tailing subprocess
output. `UPDATE_RUN_LOG=0` disables detailed diagnostic events and subprocess
logging; execution checkpoints and status remain durable. Diagnostics redact URL
credentials, query strings and fragments. Legacy JSON logs remain historical
files and cannot be resumed. Retain the run directory to retain recovery state.

Flake edits refresh the lockfile once, through the same streaming
command runner as source refreshes. `--subprocess-timeout` applies to these
commands as well as package and root validation; a command that hits it is not
retried.

Source-derived toolchain metadata comes from the pinned upstream manifests and
locks. Node and pnpm selection must satisfy upstream requirements through the
pinned nixpkgs package set. Mux and Superset use the exact Bun version from
`packageManager`, with updater-generated runtime hashes. The updater owns these
generated values. Reviewed compatibility pins and platform policy remain
explicit.

## Reuse as a framework

This flake can be consumed by another repository as a module framework.
Public API version 2 removes the site-specific `nixcfgProfiles` exports and
the `mkDarwinHost.work` policy shortcut. It also stops importing `sops-nix`
through `mkHomeModules`. Downstream configurations should import their own
profile modules and, when needed, the `sops-nix` Home Manager module explicitly.
Cache policy is now opt-in: the common substituter and trusted-key options
default to empty lists. `mkDarwinHost` also enables the Rosetta builder by
default without consulting ambient CI state; CI and other callers without a
Linux builder must pass `enableRosettaBuilder = false` explicitly.

- Exported `darwinModules`, `nixosModules`, and `homeModules` are declared in
  [`lib/exports.nix`](lib/exports.nix), the canonical module inventory.

- Exported constructors in [`lib`](lib/lib.nix):

  - [`mkSystem`](lib/lib.nix), [`mkDarwinHost`](lib/lib.nix),
    [`mkHome`](lib/lib.nix), [`mkHomeModules`](lib/lib.nix),
    [`mkSetOpencodeEnvModule`](lib/lib.nix)

- Downstream-oriented controls:

  - [`mkHome`](lib/lib.nix) supports `extraSpecialArgs` for
    downstream-only module arguments
  - [`mkSystem`](lib/lib.nix) supports `extraSpecialArgs`,
    `homeManagerExtraSpecialArgs`, and `homeModuleArgsByUser`. Darwin systems
    require at least one user; userless NixOS systems set `primaryUser = null`.
  - [`mkDarwinHost`](lib/lib.nix) forwards `extraSpecialArgs`,
    `homeManagerExtraSpecialArgs`, and `homeModuleArgsByUser`; it also supports
    `includeDefaultUserModule = false`, `homeModulesByUser`, and a custom `system`.
  - [`default.nix`](default.nix) and its `mkLib` helper accept an explicit
    `evaluationContext` for update source overrides and fake-hash evaluation.
    Ambient environment variables do not alter the API.

- Policy knobs intended to be overridden in downstream repos:

  - [`nixcfg.common.hostname`](modules/common.nix)
  - [`nixcfg.common.nix.substituters`](modules/common.nix)
  - [`nixcfg.common.nix.trustedPublicKeys`](modules/common.nix)
  - [`nixcfg.darwin.homebrew.{user,taps,mutableTaps,enableRosetta}`](modules/darwin/homebrew.nix)

Example downstream pattern:

```nix
{
  outputs = { nixcfg, ... }: {
    darwinConfigurations.my-host = nixcfg.lib.mkDarwinHost {
      user = "alice";
      includeDefaultUserModule = false;

      extraSpecialArgs = {
        org = "acme";
      };
      homeManagerExtraSpecialArgs = {
        privateRoot = ./.;
      };
      homeModuleArgsByUser.alice = {
        role = "platform";
      };

      extraHomeModules = [
        nixcfg.homeModules.nixcfgGit
        ./home/alice.nix
      ];

      extraSystemModules = [
        {
          nixcfg.common.nix.substituters = [ "https://cache.nixos.org" ];
          nixcfg.common.nix.trustedPublicKeys = [
            "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
          ];
        }
      ];
    };
  };
}
```

Site-specific policy (for example cache keys, org profile settings, host/user
modules) should live in the consuming repository, while these shared modules
stay generic.

## License

[MIT](LICENSE)

[c]: https://img.shields.io/github/license/gkze/nixcfg?style=flat-square
[d]: https://github.com/gkze/nixcfg/blob/main/LICENSE
[e]: https://img.shields.io/github/last-commit/gkze/nixcfg/main?style=flat-square
[f]: https://github.com/gkze/nixcfg/commits/main
[g]: https://img.shields.io/github/commit-activity/m/gkze/nixcfg?style=flat-square
[h]: https://img.shields.io/badge/Nix-flake-5277C3?logo=nixos&logoColor=white&style=flat-square
[i]: https://nixos.org
[j]: https://img.shields.io/badge/platform-aarch64--darwin%20%7C%20aarch64--linux%20%7C%20x86_64--linux-334155?style=flat-square
[k]: https://github.com/gkze/nixcfg#current-state
