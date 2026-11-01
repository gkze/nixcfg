# Buildkite pipeline

Buildkite runs the same job graph as GitHub Actions. The workflow files under
`.github/workflows/` stay the authority. `.buildkite/pipeline.yml` is only the
bootstrap. It runs `python -m lib.buildkite upload`, which reads those files
and uploads the graph.

Do not add a second copy of a job to the bootstrap. Change the Actions
workflow. The parity tests fail when the generated graph drifts.

## Agents

Register one agent for each queue the workflows use:

| Queue       | GitHub-hosted label | Nix system       |
| ----------- | ------------------- | ---------------- |
| `linux-x86` | `ubuntu-24.04`      | `x86_64-linux`   |
| `linux-arm` | `ubuntu-24.04-arm`  | `aarch64-linux`  |
| `macos-arm` | `macos-15`          | `aarch64-darwin` |

This repository does not build `x86_64-darwin` on a native runner. Do not
register a `macos-x86` queue unless a workflow gains an Intel macOS label.

Use persistent hosts. The agents on one host share one Nix daemon and one
`/nix/store`. Give the agent user access to that daemon.

Each agent needs:

- `nix` on `PATH`. The steps do not install Determinate Nix. A missing `nix`
  fails the job.
- A full git clone. `fetch-depth: 0` unshallows a shallow clone. PR target
  mapping needs the base commit.
- Python 3.14 on `PATH` for the quality workflow, or a `nix develop` that
  provides it. The bootstrap command is `nix develop`.
- The Buildkite agent token from the Buildkite UI. Do not commit that token.

Start an agent with the queue tag. Example:

```sh
buildkite-agent start --tags "queue=linux-x86"
```

Repeat the command with `queue=linux-arm` or `queue=macos-arm` on the matching
host. One host can run one queue. Do not point two queues at one agent.

Create the pipeline from `.buildkite/pipeline.yml`. Set the default branch to
`main`. The Monday schedule is cron `17 7 * * 1` (07:17 UTC). Path filters in
the Buildkite UI are optional. The generator also applies the workflow path
filters. A manual build uploads nothing until you set
`NIXCFG_BUILDKITE_WORKFLOW` to a workflow file name, such as `update.yml`.

## Nix store and Cachix

The host Nix daemon is the substituter. Add the public caches once:

```nix
extra-substituters = https://gkze.cachix.org https://zed.cachix.org
extra-trusted-public-keys = gkze.cachix.org-1:... zed.cachix.org-1:...
```

Copy the key values from the Cachix cache pages. Do not commit them.

Match the workflow GC thresholds. Darwin uses fixed values from
`nix_gc_thresholds` in `lib/update/ci/jobs.py`:

- `min-free` = 34359738368 (32 GiB)
- `max-free` = 68719476736 (64 GiB)

Linux uses the same `max-free`. Linux `min-free` is the smaller of 32 GiB and
the larger of 1 GiB and `free_bytes - 28 GiB`. The 28 GiB term is the peak
store growth the workflow allows. Set the host `min-free` from that function
for the disk you have. Do not leave `min-free` at the Nix default.

The hosted action also sets `max-jobs`, `cores`, and `eval-cores` to 2. Set
the same three values on the persistent daemon when you want the same
parallelism as GitHub-hosted runners.

Each job that holds `CACHIX_AUTH_TOKEN` starts its own `cachix daemon` on a
private socket. The Nix post-build hook pushes to `gkze` through that socket.
`flush-cachix` stops that job's daemon. It does not stop a system daemon.
Cachix pushes stay enabled. A second push of the same store path is
idempotent.

The host keeps `~/.cache/nixcfg`. There is no Buildkite equivalent of
`actions/cache`. Generator receipts still require the same identity.

## Secrets

Create these Buildkite secrets. The names match the Actions secrets. Never
commit a value.

| Buildkite secret                 | Actions source                                      | Used for                                      |
| -------------------------------- | --------------------------------------------------- | --------------------------------------------- |
| `UPDATE_SELF_HEAL_GITHUB_TOKEN`  | `secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN`             | Publish, PR creation, upstream API            |
| `CACHIX_AUTH_TOKEN`              | `secrets.CACHIX_AUTH_TOKEN`                         | Push store paths to `gkze`                    |
| `GPG_PRIVATE_KEY`                | `secrets.GPG_PRIVATE_KEY`                           | Sign the update commit when publish is on    |
| `GPG_PASSPHRASE`                 | `secrets.GPG_PASSPHRASE`                            | Unlock that key                               |
| `GITHUB_TOKEN`                   | `github.token`                                      | Actions API reads and the repair agent       |
| `BUILDKITE_API_TOKEN`            | none (Buildkite only)                               | Admission, sibling poll, matrix fail-fast    |

`BUILDKITE_API_TOKEN` needs permission to list and cancel builds on this
pipeline. It is not an Actions secret.

Fork builds do not receive secrets when the job `if` is already false without
reading `needs`. Set `NIXCFG_HEAD_REPOSITORY` to the head
`owner/name` when it differs from `NIXCFG_REPOSITORY` (default `gkze/nixcfg`).
Buildkite does not do this by itself.

## Publish gate

`NIXCFG_BUILDKITE_PUBLISH` defaults to unset. The steps `publish` and
`start-repair`, and the GPG import, run only when the value is exactly
`true`. Leave it unset while Actions is still publishing. Set it to `true`
only on the system that should open the update PR.

Coverage, certify, and Cachix flush still run when the flag is off.

## What the generator keeps

The generator copies from each workflow file:

- Job order, `needs`, and `if`
- Runner label to queue
- `timeout-minutes` (callers of `update-native.yml` use that file's 360)
- Static matrix cells, `max-parallel`, and `fail-fast`
- Dynamic `fromJSON(needs…)` matrices, expanded after the producer finishes
- Concurrency group and cancel-in-progress, applied before upload
- Secret names on the steps that use them

`lib/update/ci/jobs.py` still runs every stage. Sibling stop stays in
`lib/update/sibling_failfast.py`. The Buildkite poll only writes the jobs
document that policy already reads.

Artifacts use `buildkite-agent artifact upload` and `download` under
`bk-artifacts/<actions-name>/`. Job outputs use `buildkite-agent meta-data`.
A download with no name recreates `dest/<artifact-name>/`.

## No clean Buildkite equivalent

These Actions features do not map onto one pipeline field. The generator
keeps the nearest safe behavior and leaves the Actions workflow unchanged.

1. **Conditional cancel-in-progress.** Admission uses the Builds API
   (`BUILDKITE_API_TOKEN`). One build can contain every workflow that matched
   the event, so a cancel stops all of them.
2. **Environment required reviewers.** `update-repair-approval` is a block
   step. It is not a protected reviewer list. The block is skipped unless
   `NIXCFG_BUILDKITE_PUBLISH` is `true`.
3. **`strategy.fail-fast: true`.** Only `canary-darwin-fods` sets this. The
   failed cell asks the API to cancel sibling cells. That can skip their
   flush, which matches Actions. Without `BUILDKITE_API_TOKEN` the cancel
   does not run.
4. **Hosted block-device faults.** Detection needs `GITHUB_ACTIONS` and a
   GitHub-hosted runner. The generator does not set `GITHUB_ACTIONS`. Retry
   jobs stay in the graph and do not fire on a healthy persistent agent.
5. **`actions/cache`.** The persistent `~/.cache/nixcfg` directory is the
   cache. The restore step is a no-op.
6. **`clean-image`.** That stage refuses a runner that is not GitHub-hosted.
   The generator skips it.
7. **`copilot-requests: write`.** The Copilot CLI token has no Buildkite
   equivalent. Repair and `update-agent-check` still call the same
   entrypoints and need a `GITHUB_TOKEN` that Copilot accepts.
8. **`collect-evidence`.** It reads Actions job logs with `gh api`.
   `GITHUB_RUN_ID` is the Buildkite build number, so those logs are absent.
9. **`start-repair`.** It still runs `gh workflow run update.yml`. That
   dispatches Actions, not Buildkite. The publish flag gates the step.
10. **Publish evidence URL.** The PR body link uses the Actions run URL shape
    (`GITHUB_SERVER_URL`, repository, and `GITHUB_RUN_ID`).
11. **Fork secret isolation.** Set `NIXCFG_HEAD_REPOSITORY` when the head
    repository differs. Buildkite does not infer it.
12. **`permissions:` blocks.** Buildkite has no token-permission block. Scope
    the secrets above in the Buildkite UI.
13. **`workflow_dispatch` boolean defaults.** Unset dispatch inputs stay null.
    `gh workflow run` does not apply the YAML defaults either.
14. **Schedule and path filters in the Buildkite project.** Create the cron in
    the UI. The generator enforces the same cron selection and path filters
    when the build source is `schedule`, `push`, or a pull request.

## Related

- [Update on GitHub-hosted builders](update/ci.md)
- [Update overview](update/README.md)
- [Development guide](development.md)
- [Documentation index](README.md)
