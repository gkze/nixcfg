# Updater CI

The `Update` workflow uses disposable GitHub-hosted builders. It runs weekly on
Monday at 07:17 UTC and supports manual target selection. An empty selection uses
the CLI's eligible inventory, including bulk holds. Successful changes become a
signed commit and a pull request; the workflow does not apply a system configuration.
Concurrency is per Git ref. Default-branch runs stay queued; feature-branch
exercise runs cancel an older in-progress run so a newer HEAD can start.

## Execution and ownership

1. Prepare on macOS ARM64, then Linux ARM64, then Linux x86_64. Each stage extends
   one candidate, retaining previously selected release metadata and native hashes.
   Preparation is sequential because updaters can share generated files. Dependent
   updaters recompute metadata from their pinned prerequisites.
2. Validate the final identical tree on all three native builders. After
   `prepare-darwin` freezes flake references, two Linux jobs cache the native
   boundary of Darwin roots (the Rosetta/linux-builder VM image) in `gkze`
   while Linux prepare continues. Those jobs are not publication evidence.
   After the last prepare, the two Linux validators and Darwin package
   validation run in parallel. Closures still wait for the final three-system
   candidate because certify binds reports to that tree. Darwin packages own
   the shared `zed-editor-nightly` / `rust_*` subtree: they `nix build` the
   native package (not only eval `.drvPath`) so Cachix has those crates
   before root shards start. Always-run per-root
   Darwin shards start after that package job (they wait for its cache, not
   its success) and after the Linux VM-image cache jobs. A generated matrix
   from `lib.rootClosureManifest` plus `lib/update/ci/shard_costs.json` is the
   only shard plan; there is no serial yield chain and no
   `closure_complete` skip gate. Each planned shard always runs on the success
   path. An aggregate `root-closures` job then realizes the farm mostly by
   substitution. `assert-coverage` always runs, takes the manifest as
   authority, and fails the run if any root is unbuilt, unpushed, or skipped,
   or if a Linux / Darwin package inventory was silently narrowed. publish
   needs that job.
   Hosted public `macos-15` concurrency is 5 of 20. Peak Darwin use after
   packages is four root shards. `max-jobs` / `cores` stay at 2 until a later
   run measures a safe increase (zed memory on hosted macos-15).
   A transient store fault
   (`Illegal byte sequence`, a vanished store `.drv`, a vanished store build
   input, rustc E0463 after `--extern` named a `/nix/store/` rlib, a crashed
   Nix daemon, or SIGBUS) is retried
   with only the time left in that shard's build budget. The shard then fails
   closed; realized paths stay in `gkze` for the next run.
   Determinate Nix can report that fault as `Cannot build` /
   `Reason: 1 dependency failed` after a substitute EILSEQ; that still retries.
   A builder that exits 1 only because a `/nix/store/` build input vanished,
   or because rustc could not load a store rlib it was passed via `--extern`,
   is the same fault. A builder that actually compiled or linked and then
   exited (`failed with exit code`, `error: builder for`) still fails the shard.
   Validation `nix build` passes `--fallback` so a failed substitute can
   rebuild from source. Every declared package platform and every
   native root is still built.
   Each builder evaluates every declared package platform, builds native package
   validations, and builds its roots from the independently checked root manifest.
   Nix's recursive derivation graph supplies native dependencies of foreign roots,
   including the Linux Rosetta builder image embedded in the Darwin configurations.
   These exact outputs are built natively and handed off through the binary cache;
   no VM configuration or dependency list is duplicated in CI. That graph is
   evaluated with import-from-derivation disabled: a Linux runner cannot realize a
   Darwin derivation during evaluation, so Darwin roots must read theme files and
   other evaluation inputs from flake inputs rather than from built port outputs.
   Hosted ARM runners do not expose KVM. The builder-image overlay permits Nix's
   existing QEMU TCG fallback by removing the image builder's KVM scheduling
   requirement. The full image, bootloader installation and guest configuration
   remain required; this does not advertise nonexistent runner hardware or disable
   the configured builder VM.
3. Certify that every required platform reported success for that exact Git tree.
   Apply the certified patch, run repository hooks and the full Python/coverage
   gates, and check that validation did not alter the candidate. Only then create
   the signed update commit and PR. Publication always opens that PR against the
   default branch from `origin/<default>` with the certified tree as one commit,
   then squash-merges that PR. `--auto` queues the merge when required checks
   are still pending. When GitHub reports the PR is already clean, publish
   squash-merges it immediately. That includes runs dispatched from a feature or
   repair ref. Exercise runs therefore never merge into the branch under review.

The system inventory comes from `lib/system-policy.json`. `nixcfg ci update matrix`
projects it to hosted runner labels. A conformance test keeps preparation and
validation jobs consistent with that inventory. Actions declares the job graph and tool setup.
`lib/update/ci/jobs.py` owns job commands, evidence collection, quality checks and
publication; every authored command step runs Python, with no shell glue. The updater
core owns source discovery, declared output authority, candidate identity and validation. Nix owns derivations, dependency ordering, builds and cache reuse.

Cachix's daemon uploads built outputs continuously in every job that uses
`update-runtime`. Each native job also has an explicit `if: always()` flush
that pushes leftover prefetch receipts and runs `cachix daemon stop` so a
failed, cancelled, or near-timeout job still drains the queue. Slack before
the 360-minute hard kill is the five-hour build budget (ED-7.1: a SIGKILL
during flush can still drop the queue tail; runner loss keeps only paths
Cachix already acknowledged).
Preparation, validation, and coverage also publish the exact files recorded
by URL prefetches on every exit path, not only when the updater succeeds.
Those files enter the store directly and do not trigger Nix's post-build hook.
Encoded path basenames use
nixpkgs' fetchurl spelling instead of URL-decoded spelling for matching store identities;
explicit package-specific source names remain independent overrides. Prefetches
append store paths to an invocation-local JSONL receipt retained with the job
artifacts. Publication validates and deduplicates these paths without scanning
the runner's store.

Preparation and repair restore Cargo registry/Git downloads and verified generator
receipts through the Actions cache. Each platform uses a fresh key per run/attempt
and can restore its latest prior cache. Receipts still require exact generator
input and output identities; restoring a cache does not authorize stale output.
Validation and publication do not download these generator-only caches. Cargo
credentials, configuration, DBOS state and mutable workspaces are excluded.
Restoring this cache proves download availability, not skipped generation. Receipts
contain output digests rather than generated files, so the checkout must already
contain matching outputs. The receipt identity still includes the full lockfile
and environment, but normalizes `NIX_USER_CONF_FILES` by config file contents so
temporary runner-local paths alone do not force regeneration. Cargo downloads
remain reusable independently.

Copilot Cloud uses `.github/workflows/copilot-setup-steps.yml` on the default
branch to prepare the same Nix runtime, development shell and read-only binary
caches before an agent starts. Setup verifies Python 3.14, the updater's imports,
the packaged CLI and GitHub repository reads. Agent commands should run through
`nix develop --command ...` (or the retained `NIXCFG_DEVSHELL` profile) so checks
use the pinned tools. No upload credential is required for setup. Native platform
builds still belong to the Update workflow. Setup's read token does not establish
that the agent can dispatch workflows or sign and publish repair branches; those
operations need separate verification with the agent's own GitHub permissions.

The same updater implementation serves local and CI execution. Local `nixcfg update`
uses DBOS/SQLite for recovery on the same filesystem and the existing filesystem
journal for atomic promotion. CI runs the preparation core without that local
execution history. It carries a versioned JSON candidate containing a binary Git
patch, exact base/result tree identities, resolved metadata, and completed platforms.
Neither SQLite nor a retained checkout is a CI persistence requirement.

## Credentials and runtime

Actions are pinned to commits. Each job builds the packaged updater and development
environment from the triggering checkout, retaining them as Nix roots for that job.
Candidate changes do not silently change the code executing the job.

Before installing Nix, a Python step reclaims unused preinstalled image tools.
The job launcher uses Python 3.12 syntax and standard-library imports so it can
run with the hosted image's interpreter before the Python 3.14 runtime exists.
On macOS it retains the selected Xcode and removes other Xcodes, the
Android SDK, unused iOS simulators, Xcode device-support caches, the
hosted tool cache, and `~/Library/Caches`.
Those last cache trees are deleted best-effort: hosted macOS can rewrite
`~/Library/Caches` during `rmtree` and fail with `ENOTEMPTY`; that must not
fail the job. Unused Xcode and simulator trees still fail closed.
Android, .NET and unused Linux compiler libraries are removed
where present. The step refuses local or self-hosted execution and logs available
space before and after cleanup. This matters because the measured Darwin root
closure alone occupies about 73.5 GB. A combined hosted Darwin job still GCs
the store between package validation and `root-closures`. Split shards do not:
each closure runner starts empty and reuses paths from `gkze`. That build omits
per-derivation `-L` logs. The Cachix daemon uploads each realized path. Publish quality also
runs on macos-15, and CLI help assertions must survive Rich's hosted TTY
geometry. Runner capacity remains an acceptance check for local Darwin.

Repository secrets used by the workflow:

- `UPDATE_SELF_HEAL_GITHUB_TOKEN`: public upstream API access for Nix and updaters.
- `CACHIX_AUTH_TOKEN`: populate the existing `gkze` binary cache; `zed` is also read.
- Job `GITHUB_TOKEN` on `publish` (`contents: write`, `pull-requests: write`):
  push the update branch and open the PR. `start-repair` also needs
  `actions: write` so `gh workflow run` can create the follow-up dispatch.
  `GH_TOKEN_FOR_UPDATES` is unused until it can authenticate `git push`.
- `GPG_PRIVATE_KEY` and `GPG_PASSPHRASE`: sign the update commit.

The repair agent uses the short-lived Actions `GITHUB_TOKEN` with job-scoped
`copilot-requests: write`, and explicitly selects `gpt-6-astra`. No stored Copilot
token is supplied: `COPILOT_GITHUB_TOKEN` would override the built-in token.
See [GitHub's Actions authentication documentation](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli-in-actions).
The independent `Update agent check` workflow exercises that token and model with
a single tool-free response before a real repair run. It shares the pinned CLI
installer and can also be dispatched manually.

Checkout never persists Git credentials. Write credentials are supplied only to
publication steps. No persistent-runner registration or state-root variables are
needed. Native package and root builds still require sufficient cache coverage and
runner capacity; tests of workflow wiring do not establish either.

## Failure and retry

Each native job uploads its candidate or validation report, structured result and
stderr diagnostics, including on failure. Preparation streams phase and target
progress to stderr while keeping stdout as one machine-readable JSON result.
Validation streams Nix command and build lines (`nix build -L`) to the live job
log as they arrive. The job wrapper also tails redacted `runs/*/output.log`
files into that same log and only emits a liveness heartbeat when both the child
and those run logs are quiet. Artifacts expire after 30 days. An
interrupted job may need to repeat work; completed upstream artifacts can be reused
by Actions reruns. A failed preparation cannot advance to another platform, and a
missing or mismatched validation report cannot authorize publication.
A source whose latest-version fetch fails on retry-exhausted DNS or connect
(for example `dl.wisprflow.com` `RELEASES.json`) keeps its current pin instead
of failing preparation.

Validation distinguishes completed target failures from incomplete execution.
Only completed failures can trigger batch subdivision and package withholding.
A command timeout, signal termination, or OS error (including failure to launch
Nix) aborts validation without blaming individual packages or restarting the batch
in smaller groups.
Parallel validation cancels sibling commands and reaps owned children before returning
the original failure; pending groups do not start after the failure is observed.
Local execution returns a run-level validation error with `validationIncomplete`
diagnostics in JSON and retained run evidence, discards the candidate and exports
no accepted patch. Native CI
validation exits without issuing a report, so certification cannot proceed.
The repair supervisor still permits its one bounded proposal/retry; an incomplete
retry cannot reach quality acceptance or promotion.

The local package timeout remains 40 minutes per command, including the whole
batch; roots default to six hours. CI package validation has no subprocess timeout
and remains bounded by the native job deadline. Completed Nix outputs remain
reusable. Each started command attempt closes its progress state on failure,
cancellation or retry. Timeout errors retain the last 16 KiB of each captured
stream (or 16 Ki characters for runner-supplied text), with an omission marker
when truncated. The partial first line is discarded before URL redaction so a
truncated credential cannot escape detection. The shared owner sanitizes retained
diagnostics and removes the original exception chain; observed output remains in
the redacted run log.
This policy belongs to the shared command owner, not package attribution. Nix's
per-builder timeout/silence controls have different semantics and are not silently
substituted for the existing command deadline.

By default, a failed run collects job logs and artifacts for one Copilot CLI repair
attempt. The agent works in an isolated checkout and may change packaging under
`packages/` and `overlays/`, plus `flake.nix`, `flake.lock`, and planner/selection
coherence in `lib/update/planner.py` (with `lib/tests/test_update_planner.py`).
Changes outside that scope are rejected, including CI, acceptance gates, and
persistence. The agent receives no publication credentials.

The repair prompt encodes a narrow checkPhase exception. The agent may skip or
disable **checkPhase only** when every condition holds:

1. The failure is an upstream package's own tests (`checkPhase` / XCTest /
   pytest / similar), not compile or link.
2. The derivation build itself succeeded before checks.
3. The failure is not in George's overlay, app, or home-manager module code.
   Wrapping an upstream project in `packages/` is allowed.
4. Prefer `doCheck = false` (or equivalent) pinned to the failing version,
   with a short comment citing the failing test and signal.
5. Document the skip so it is visible in the change.

Still hard-fail — never skip or waive Darwin: SIGBUS, hosted runner lost
communication, store `ValidationIncomplete` (infra; retry), compile/link
failures, failures in George's own overlay/app/home-manager module code, or
waiving Darwin validation entirely.

Repair runs on
the Darwin runner, where repository hooks and the Python/coverage gates are
maintained; they must pass before the repair is committed to a separate
branch. A fresh Update run then prepares and validates it on every native builder.
That run has repair disabled, so failures cannot create an unbounded retry loop.
It explicitly sets `validate_all_packages=true`: every registered updater's
declared package validations run, including held and unselected packages, even
when preparation produces no update changes. Update targets and bulk holds still
govern version selection and generation; broader validation does not update those
extra packages. Each builder evaluates all declared systems and builds only its
native declarations, followed by the existing root closure gates. This inventory
covers updater declarations, not exported packages without declarations.

The validation scope travels with the candidate through native preparation stages
and is recorded in each report. Certification rejects reports with a different
scope, even for the same tree. Ordinary targeted runs retain selected-package
validation by default. Manual repaired candidates must opt in with the workflow's
`validate_all_packages` input or `ci update prepare --validate-all-packages`.
Only successful native validation permits a PR against the default branch.

`gh workflow run` does not apply YAML boolean defaults, so a manual dispatch
must pass `-f repair=true` when repair should run. The job condition accepts
only an explicit true, matching that CLI behavior. Prefer
`gh workflow run Update --ref main -f repair=true -f validate_all_packages=true`
when the token has Actions write.

The Cursor GitHub App install token (`ghs_…`) used by cloud agents gets
HTTP 403 `Resource not accessible by integration` on `workflow_dispatch`
until the App is granted **Actions: Read and write** on `gkze/nixcfg`.
Repo Settings → Actions → Workflow permissions only affect `GITHUB_TOKEN`
inside jobs; they do not grant that App dispatch rights.

Until Actions write is granted, cloud agents should open and merge a
one-line timestamp bump to `.github/update-kick` on `main`. That push
queues one Update run with the same path filter. Kick-file pushes set
`validate_all_packages` and enable the repair job so they match EM's
`repair=true` + `validate_all_packages=true` dispatches. Do not start a
second main Update while one is already running.

Push events that touch `.github/update-kick` therefore enable
repair; that path exists because some tokens cannot create `workflow_dispatch`
events. Set `repair=false` to retain
failure evidence without invoking an agent. Agent output is a proposal, never validation evidence. CI does
not reuse a DBOS history after changing code. This repair scope includes planner
and target-selection defects that leave packaging and flake pins incoherent.
It still leaves acceptance-gate, CI, persistence, and credential failures for a
maintainer to resolve.

Commands can be exercised outside Actions, with artifacts outside the repository:

```sh
nixcfg ci update prepare --output /tmp/candidate.json example
nixcfg ci update prepare --previous /tmp/previous.json --output /tmp/candidate.json
nixcfg ci update validate --candidate /tmp/candidate.json --output /tmp/report.json
nixcfg ci update certify --candidate /tmp/candidate.json \
  --report /tmp/darwin.json --report /tmp/arm.json --report /tmp/x86.json \
  --output /tmp/update.patch
```

Preparation requires one native stage per configured system before validation.
Candidates only apply to their exact original source tree. Reports describe
prepared trees, not successful promotion or deployment. Existing local DBOS runs
remain local and retain their original resume constraints.
