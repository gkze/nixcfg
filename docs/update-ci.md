# Updater CI

The `Update` workflow uses disposable GitHub-hosted builders. It runs weekly on
Monday at 07:17 UTC and supports manual target selection. An empty selection uses
the CLI's eligible inventory, including bulk holds. Successful changes become a
signed commit and a pull request; the workflow does not apply a system configuration.

## Execution and ownership

1. Prepare on macOS ARM64, then Linux ARM64, then Linux x86_64. Each stage extends
   one candidate, retaining previously selected release metadata and native hashes.
   Preparation is sequential because updaters can share generated files. Dependent
   updaters recompute metadata from their pinned prerequisites.
2. Validate the final identical tree on all three native builders. The two Linux
   validators run in parallel and finish their Cachix uploads before Darwin starts.
   Each builder evaluates every declared package platform, builds native package
   validations, and builds its roots from the independently checked root manifest.
   Nix's recursive derivation graph supplies native dependencies of foreign roots,
   including the Linux Rosetta builder image embedded in the Darwin configurations.
   These exact outputs are built natively and handed off through the binary cache;
   no VM configuration or dependency list is duplicated in CI.
   Hosted ARM runners do not expose KVM. The builder-image overlay permits Nix's
   existing QEMU TCG fallback by removing the image builder's KVM scheduling
   requirement. The full image, bootloader installation and guest configuration
   remain required; this does not advertise nonexistent runner hardware or disable
   the configured builder VM.
3. Certify that every required platform reported success for that exact Git tree.
   Apply the certified patch, run repository hooks and the full Python/coverage
   gates, and check that validation did not alter the candidate. Only then create
   the signed update commit and PR.

The system inventory comes from `lib/system-policy.json`. `nixcfg ci update matrix`
projects it to hosted runner labels. A conformance test keeps preparation and
validation jobs consistent with that inventory. Actions declares the job graph and tool setup.
`lib/update/ci/jobs.py` owns job commands, evidence collection, quality checks and
publication; every authored command step runs Python, with no shell glue. The updater
core owns source discovery, declared output authority, candidate identity and validation. Nix owns derivations, dependency ordering, builds and cache reuse.

Cachix's daemon uploads built outputs continuously. Successful preparation also
publishes the exact files recorded by successful URL prefetches, which enter the
store directly and do not trigger Nix's post-build hook. Encoded path basenames use
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
On macOS it retains the selected Xcode and removes other Xcodes.
Android, .NET and unused Linux compiler libraries are removed
where present. The step refuses local or self-hosted execution and logs available
space before and after cleanup. This matters because the measured Darwin root
closure alone occupies about 73.5 GB; runner capacity remains an acceptance check.

Repository secrets used by the workflow:

- `UPDATE_SELF_HEAL_GITHUB_TOKEN`: public upstream API access for Nix and updaters.
- `CACHIX_AUTH_TOKEN`: populate the existing `gkze` binary cache; `zed` is also read.
- `GH_TOKEN_FOR_UPDATES`: push the update branch and open its PR.
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
progress to stderr while keeping stdout as one machine-readable JSON result. It
also retains the updater's redacted run logs under `runs/` for detailed source
errors. Artifacts expire after 30 days. An
interrupted job may need to repeat work; completed upstream artifacts can be reused
by Actions reruns. A failed preparation cannot advance to another platform, and a
missing or mismatched validation report cannot authorize publication.

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
`packages/` and `overlays/`, plus `flake.nix` and `flake.lock`. Changes outside that
scope are rejected. The agent receives no publication credentials. Repository hooks
and Python/coverage gates must pass before the repair is committed to a separate
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

Set the manual workflow's `repair` input to false to retain failure evidence without
invoking an agent. Agent output is a proposal, never validation evidence. CI does
not reuse a DBOS history after changing code. This repair scope intentionally leaves
framework defects and unavailable credentials for a maintainer to resolve.

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
