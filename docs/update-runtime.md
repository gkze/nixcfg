# Update runtime efficiency

Updates prepare and validate one disposable candidate before atomic promotion.
Runtime optimizations preserve that boundary, platform requirements, cancellation
cleanup, and per-target failure ownership. They do not activate configurations.

## Admission and scheduling

Each update invocation owns its concurrency limits, shared work, and timings.
There are no process-global build semaphores tied to a previous event loop.

| Environment variable          | Default | Controls                                 |
| ----------------------------- | ------: | ---------------------------------------- |
| `UPDATE_MAX_SOURCE_TASKS`     |       8 | Active source updater tasks              |
| `UPDATE_MAX_NIX_EVALUATIONS`  |       1 | Concurrent Nix evaluation processes      |
| `UPDATE_MAX_NIX_BUILDS`       |       1 | Concurrent Nix build/run/shell processes |
| `UPDATE_MAX_DOWNLOADS`        |       8 | HTTP requests and URL prefetch processes |
| `UPDATE_MAX_MATERIALIZATIONS` |       1 | Active crate2nix materialization workers |

`--max-nix-builds` overrides the build environment setting. Python callers can
set each limit through `resolve_config`. Zero and negative limits clamp to one.
These limits control updater processes; Nix daemon job/core settings and remote
builder capacity still control work inside each process.

All selected flake input refreshes finish before concurrent source evaluation
starts. A source waits only for its declared prerequisites. Its completed source
and artifact results become visible before dependent tasks start. A failed
prerequisite prevents dependent work, while independent sources continue.
Waiting for a prerequisite does not consume a source-task slot.

Dependency edges come from `companion_of` and `aggregate_into`. Python's
`graphlib.TopologicalSorter` owns cycle detection and prerequisite ordering;
the planner translates cycle errors into updater diagnostics. Tasks are created
in that order, so prerequisites exist even with an eager task factory, and each
task awaits its own prerequisites within an `asyncio.TaskGroup`. The order does
not impose execution waves. Dependency depths remain only for target ordering
and the existing wave-planning helper.

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

Successful validation retains existing grouping. Failed groups are split in
half to isolate sparse failures; small groups use individual checks with the
original retry policy. When both halves fail, subdivision stops and individual
diagnostics cover the whole group. This bounds extra work for systemic failures
while preserving every required target and the root-closure build gate.

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
instrumentation records bytes/time while retaining both reads and race checks.

## Validation evidence and remaining costs

Local component measurements during this change:

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
