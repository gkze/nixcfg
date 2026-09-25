# Update runtime and durable execution

Updates prepare and validate one disposable candidate before atomic promotion.
The safety invariants this preserves are enumerated in
[update-safety.md](update-safety.md). Runtime optimizations preserve that
boundary, platform requirements, cancellation cleanup, and per-target failure
ownership. They do not activate configurations.

## Durable ownership and recovery

The CLI uses [DBOS workflows and steps](https://docs.dbos.dev/python/tutorials/workflow-tutorial)
with a SQLite system database per run. No daemon or external database is required.
DBOS owns execution identity, completed step results, exceptions, workflow outcomes
and restart recovery. SQLite also stores the original request, immutable candidate
manifests, deduplicated file contents and diagnostic projections. This replaces the
separate JSON status/metadata files and JSONL event stream. The plain subprocess
log is retained for `tail -f`.

| Existing updater concept | Explicit contract |
| --- | --- |
| Selected targets and captured dirty checkout | Immutable workflow request and SQLite baseline |
| Latest-version discovery | Checkpointed resolution step; replay reuses the resolved version |
| Hashing and artifact generation | Checkpointed materialization step, including declared artifact bytes |
| Source dependency graph | Independent source workflows with serializable prerequisite results |
| Ref/input refresh and source persistence | Steps returning a verified candidate snapshot |
| Derivation/root validation | Checkpoint keyed by the exact immutable validation snapshot |
| Live promotion | Idempotent filesystem transaction reconciled until the root completes |
| Progress and heartbeat | Diagnostic projections; DBOS remains execution authority |

`nixcfg update --resume RUN_ID` starts DBOS against the existing database. Native
recovery resumes pending root/source workflows; completed workflows return their
recorded outcomes. Domain failures remain completed failures, so a new run is
required after correcting them. Normal source failures remain isolated to their
promotion clusters; they do not fail unrelated source workflows.

Recovery restores the original baseline into a new isolated workspace. Root
steps restore completed ref/input and materialization snapshots; source workflows
receive source metadata, generated artifacts and effective prerequisite results as
serializable inputs. Source recovery waits until the root restores resolved inputs
and admits the shared resource environment. Dynamic updater modules have stable
import identities so their checkpointed metadata and typed retry errors can be
reconstructed. Replayed ref outcomes and input-refresh errors rebuild the same
terminal progress state; original failures remain available in the run database.

The execution fingerprint covers Python, DBOS, platform and the repository's
packaged runtime source policy, including `uv.lock` and dynamic updater code.
Changing these rejects resume. Target selection, update policy and timeouts come
from the recorded request; only presentation options may be overridden. Each
invocation resolves terminal layout and phase headings from its current presentation
options and terminal, independently of the checkpointed update plan. Nix command
and hash-mismatch errors retain their types, command results and notes on replay.

A resumed completed run reports its historical result, not a fresh check of current
upstream or live checkout state. Direct `run_updates` library calls remain ephemeral; the
CLI's `run_update_command` owns the durable service lifecycle.

Completed steps are skipped. An interrupted step may run again, so network reads,
Nix builds and generators must tolerate repetition; generated artifacts must be
returned through the artifact contract. SQLite cannot atomically commit Git worktree
files. The existing descriptor-based filesystem journal therefore remains the
promotion authority, including rollback, interference detection and post-commit
cleanup. If promotion commits before DBOS records root completion, replay accepts
only the exact complete promoted candidate. A later external edit blocks promotion.

Candidate snapshots are content-addressed and verified before restoration. The run
directory is private and contains repository bytes and Python-serialized execution
state; it is local trusted state, not an import format for remote workers. Recovery
requires retaining its database on durable local storage. Deleting a run retires
its execution history. The [Actions integration](update-ci.md) instead uses
disposable native jobs and immutable JSON/Git candidates. It shares updater and
validation logic with local execution; SQLite histories never cross machines.

`--run-id ID` starts or resumes one named history under an exclusive run lock.
Repeated invocations must supply the same execution options; `--resume ID` instead
loads those options without restating them. Both retain the original configuration
and reject repository, platform or runtime drift. `--patch PATH` exports the exact
successful candidate stored in the DBOS result, including additions, deletions and
binary changes. Failed runs export an empty patch. Export is repeatable after a
crash without incorporating later checkout edits.

The deliberate retained mechanics are dependency/promotion policy, exact validation,
resource limits, subprocess cleanup and filesystem commit recovery. Replacing them
with generic workflow status would erase domain invariants. SQLite was chosen over
a shared Postgres service for the existing single CLI owner and per-run lifetime;
that deployment choice must change before sharing this database across hosts.

## Admission and scheduling

An updater can declare `bulk_update_hold` with a reason to keep its source and
generated artifacts out of untargeted `nixcfg update` runs (including `--check`).
Coupled companion and aggregate sources are held together. Explicit target
selection still allows updates. This does not freeze shared flake dependencies.

Unsloth is temporarily held at the existing source pin because desktop
`v0.1.811-beta` requests backend `2026.9.7`, its GitHub source declares
`2026.9.6`, and PyPI publishes no source archive. Use `nixcfg update unsloth`
to retry explicitly; remove `UnslothUpdater.bulk_update_hold` once matching
source is published and the candidate passes validation.

Each update invocation owns its concurrency limits, shared work, and timings.
There are no process-global build semaphores tied to a previous event loop.

| Environment variable          | Default | Controls                                 |
| ----------------------------- | ------: | ---------------------------------------- |
| `UPDATE_MAX_SOURCE_TASKS`     |       8 | Active source updater tasks              |
| `UPDATE_MAX_NIX_EVALUATIONS`  |       4 | Concurrent Nix evaluation processes      |
| `UPDATE_MAX_NIX_BUILDS`       |       1 | Concurrent Nix build/run/shell processes |
| `UPDATE_MAX_DOWNLOADS`        |       8 | HTTP requests and URL prefetch processes |
| `UPDATE_MAX_MATERIALIZATIONS` |       1 | Active crate2nix materialization workers |

`--max-nix-builds` overrides the build environment setting. Python callers can
set each limit through `resolve_config`. Zero and negative limits clamp to one.
These limits control updater processes; Nix daemon job/core settings and remote
builder capacity still control work inside each process.

All selected flake input refreshes finish before concurrent source evaluation
starts: every selected input resolves in one `nix flake update` batch, so N
lock evaluations become one. A source waits only for its declared
prerequisites. Its completed source
and artifact results become visible before dependent tasks start. A failed
prerequisite prevents dependent work, while independent sources continue.
Waiting for a prerequisite does not consume a source-task slot.

Dependency edges come from `companion_of` and `aggregate_into`. Python's
`graphlib.TopologicalSorter` owns cycle detection and prerequisite ordering;
the planner translates cycle errors into updater diagnostics. Tasks are created
in that order, so prerequisites exist even with an eager task factory, and each
task awaits its own prerequisites within an `asyncio.TaskGroup`. The order does
not impose execution waves. Dependency depths remain only for target ordering; obsolete wave scheduling has
been removed.

A workspace read/write gate protects temporary generated files. Ordinary
readers can overlap; a temporary artifact writer excludes unrelated readers
through generation, consumption, and restoration. Prepare fixed-output probes
in the owning task before launching child builds. Those builds use immutable
store derivations and do not need access to the mutable workspace.

Crate2nix workers read the workspace under this gate and generate outputs in
scratch directories. Their individual subprocesses acquire the same Nix
resource limits as async commands. The calling task joins a cancelled worker
before releasing its workspace access. The shared Cargo cache lock covers
actual generation attempts and is released during retry backoff.

The source phase joins shared producers before its HTTP session and disposable
workspace close. The outer runtime retains a fallback join for standalone
helpers; these boundaries protect different lifetimes. Event delivery and
crate2nix worker teardown use the same cancellation-safe completion helper:
owners cancel or signal work, shield its cleanup from additional stop requests,
and propagate cancellation after it finishes. Shared producer failures remain
local to their consumers rather than aborting unrelated updates.

The workspace gate and thread admission bridge remain local because they own
updater-specific invariants: temporary artifacts stay hidden until restored,
and synchronous crate2nix commands share the async Nix budgets. Reader ownership
is the single authority for the active-reader count. A new workflow engine or
an AnyIO migration would not remove these requirements; the current runtime
uses standard-library graph, task, semaphore, and condition primitives.

## Probe preparation and reuse

`PreparedProbe` retains the exact isolated derivation path and the fingerprint
of the original fake-hash derivation. A platform matrix is evaluated in one JSON
command with a shared native nixpkgs binding for probe isolation. Platform
package evaluation contexts remain distinct. The build uses `drvPath^out`;
retries reuse that path, without evaluating the expression again.

The hash parser associates each hash with its own mismatch block. A prepared
probe accepts only a mismatch naming its exact derivation, so a nested
fixed-output dependency cannot accidentally certify its parent.

Shared hash strategies target their fixed-output dependency directly:
`goModules`, `cargoDeps`, `npmDeps`, or `node_modules`. Custom pnpm/offline-cache
paths remain explicit. OpenCode Desktop probes `node_modules`. T3 workspace
uses the same prepared-probe contract instead of repeated fingerprint
stabilization evaluations.

`sources.json` can now include `platformDrvHashes`. A requested platform hash
is reused only with a matching certificate; changed platforms are probed again.
Legacy entries remain readable; when hashing is required, platforms lacking
certificates are reprobed. Native-only updates preserve foreign certificates
only when their associated values remain valid. Final package/root validation
still runs; certificate reuse does not bypass those checks.

Node candidate discovery evaluates available versions in one command. Each
candidate is forced under `tryEval`, so a missing or broken candidate does not
invalidate the entire inventory. Selection still chooses the lowest compatible
version. Whole-package inventories are not memoized against a mutable checkout.
Locked source realization is shared within a run using the immutable locked
node expression, and each caller validates the returned path.

## Generated artifacts and downloads

The audited crate2nix targets use optional receipts under
`$XDG_CACHE_HOME/nixcfg/generation-receipts` (the normal user cache fallback
applies). A receipt binds the realized patched store source, target options,
platform, full flake lock, generator/normalizer implementation, relevant tool
and environment identities, and every current output digest. Missing, changed,
malformed, or unreadable receipts regenerate normally. Only digests and output
names are persisted; raw environment values and credentials are not stored.
The patched source is still resolved before a receipt can authorize reuse.

T3 Desktop and CLI share one runtime-lock generation within a run, keyed by the
resolved generator derivation and its workspace runtime inputs. The producer
returns immutable artifact contents, restores its temporary files, and lets
each consumer install and restore those contents under the workspace gate.
Cancelling one consumer cannot cancel work another consumer needs. Failed
producers are removed from the run cache, and cache entries do not outlive the
run. The two lock refreshes share one disposable Bun download cache.

Repeated Bun tarball URLs share downloaded bytes within the same attempt.
Deno metadata requests share in-flight work per manifest. URL source hashing
uses `nix store prefetch-file --json`, which returns SRI directly and removes
the separate hash-conversion process. HTTP/prefetch retries release admission
slots before waiting.

Mutable registry responses are not persisted across update runs. This avoids
changing latest-version resolution or treating an unauthenticated URL as an
immutable content identity.

## Validation and observability

Successful validation retains existing grouping. Batched builds pass
`--keep-going`, so one Nix invocation builds everything that can build and
reports every failing derivation, and later isolation rounds mostly hit the
store. Failed groups are split in half to isolate sparse failures; small groups
use individual checks with the original retry policy. When both halves fail,
subdivision stops and individual diagnostics cover the whole group. This bounds
extra work for systemic failures while preserving every required target and
the root-closure build gate.

Validation failures do not discard the run. Each failing target is withheld
with its coupled targets (see the README), the smaller candidate is validated
again, and root closures build once on the candidate that will be promoted.
The rounds are bounded at three; a candidate still failing after that is
discarded as a whole. A failed target's own declared paths and every written
path not owned by a target still being promoted return to the captured
baseline before the next round.

Progress state is owned by one run monitor shared by the asynchronous phases
and the synchronous validation phases. Producers record events from any
thread; the live panel, the plain-output heartbeat, and `--status` all read
snapshots of it. SQLite stores structured events and the heartbeat snapshot;
`output.log` contains subprocess lines and remains line-buffered. Both diagnostic
paths apply the same URL redaction as terminal output. A failure inside an updater
task is confined to that target and its traceback is stored in the error event's
`detail`. `--status` reads DBOS status without starting workers or deserializing
workflow inputs/results. Heartbeat age and execution status remain distinct.


Use `--timings` for per-source operation counts, active time, admission wait,
cache hits, failures/cancellations, and captured byte counts. Combine it with
`--json` for a machine-readable `timings` array. Timing collection is run-owned;
reporting is opt-in. Rows contain operation labels rather than commands, URLs,
environment values, or cache keys.

Rows are nested and may overlap: do not sum them to infer wall time. Source
elapsed time includes nested operations, `dependencies` records prerequisite
waiting, and `wait_seconds` records resource/workspace admission. Nonzero exits
are separate from exceptions because an expected FOD mismatch is a successful
hash-probing outcome. Output byte fields describe retained command output.

The subprocess line queue is bounded at 128 items, and the update event queue
at 512 items. Consumer failure wakes blocked producers and joins both sides.
Diagnostic-only generators retain bounded output tails; JSON and hash parsers
retain complete output. Plain log lines bypass ANSI parsing. Input declaration
and lock-graph caches are bounded and keyed by exact input bytes. Snapshot
instrumentation records bytes/time: one content read plus a stat-fingerprint
race check instead of a second content read.

## Validation evidence and remaining costs

Existing runtime optimization measurements:

- A tiny local fixed-output fixture completed with exactly one `nix eval` and
  one `nix build`; its SHA-256 matched Python's independently computed digest.
  The two commands took 1.363 seconds on the development host with substitutes
  disabled. This is a protocol acceptance check, not a package benchmark.
- Real Gogcli `goModules` and Hwatch `cargoDeps` probes prepared successfully
  in a single evaluation with import-from-derivation disabled; no builds ran.
- For 21 distinct backing inputs in the inspected source inventory, the old
  receipt function took 115.7–128.9 ms per pass. The new first pass took 13.6 ms;
  repeated calls on the same bytes took 0.0016–0.0026 ms. All receipts matched.
  These figures exclude filesystem reads and network/build work.
- Tests cover a 26-target validation group with one failure using at most
  11 commands, versus the previous 27-command full fallback. If all 26 fail,
  the new path uses 29 commands, including its two subdivision checks.

First-run crate2nix generation can still hold workspace read access while Cargo
works, delaying temporary artifact writers. Cross-source package contexts are
not combined speculatively: their candidate overrides and dependencies can
differ. Real builders, downloads, and required root-closure validation can still
dominate total runtime. No full-update percentage speedup is claimed.
