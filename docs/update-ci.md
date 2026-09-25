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
2. Validate the final identical tree in parallel on all three native builders.
   Each builder evaluates every declared package platform, builds native package
   validations, and builds its roots from the independently checked root manifest.
3. Certify that every required platform reported success for that exact Git tree.
   Apply the certified patch, run repository hooks and the full Python/coverage
   gates, and check that validation did not alter the candidate. Only then create
   the signed update commit and PR.

The system inventory comes from `lib/system-policy.json`. `nixcfg ci update matrix`
projects it to hosted runner labels. A conformance test keeps the preparation chain
consistent with that inventory. Actions declares the job graph and tool setup.
`lib/update/ci/jobs.py` owns job commands, evidence collection, quality checks and
publication; every authored command step runs Python, with no shell glue. The updater
core owns source discovery, declared output authority, candidate identity and validation. Nix owns derivations, dependency ordering, builds and cache reuse.

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

Repository secrets used by the workflow:

- `UPDATE_SELF_HEAL_GITHUB_TOKEN`: public upstream API access for Nix and updaters.
- `CACHIX_AUTH_TOKEN`: populate the existing `gkze` binary cache; `zed` is also read.
- `GH_TOKEN_FOR_UPDATES`: push the update branch and open its PR.
- `GPG_PRIVATE_KEY` and `GPG_PASSPHRASE`: sign the update commit.
- `COPILOT_GITHUB_TOKEN`: authenticate the bounded CI repair agent.

Checkout never persists Git credentials. Write credentials are supplied only to
publication steps. No persistent-runner registration or state-root variables are
needed. Native package and root builds still require sufficient cache coverage and
runner capacity; tests of workflow wiring do not establish either.

## Failure and retry

Each native job uploads its candidate or validation report, structured result and
stderr diagnostics, including on failure. Artifacts expire after 30 days. An
interrupted job may need to repeat work; completed upstream artifacts can be reused
by Actions reruns. A failed preparation cannot advance to another platform, and a
missing or mismatched validation report cannot authorize publication.

By default, a failed run collects job logs and artifacts for one Copilot CLI repair
attempt. The agent works in an isolated checkout and may change packaging under
`packages/` and `overlays/`, plus `flake.nix` and `flake.lock`. Changes outside that
scope are rejected. The agent receives no publication credentials. Repository hooks
and Python/coverage gates must pass before the repair is committed to a separate
branch. A fresh Update run then prepares and validates it on every native builder.
That run has repair disabled, so failures cannot create an unbounded retry loop.
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
