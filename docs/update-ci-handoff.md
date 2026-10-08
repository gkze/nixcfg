# Update CI handoff (#221)

Lane for `cursor/no-skip-darwin-shards-6614` / [PR #221](https://github.com/gkze/nixcfg/pull/221).
George approved handing this to a fresh agent once `37809856116` ended.
This note is the stop point: **do not kick another Update from this handoff commit.**

## Done bar (standing rules, verbatim)

No skipped shards, `assert-coverage` green, every Darwin root in `gkze.cachix.org`
with `path-info` evidence, flush proof green (`cachix-flush-proof`), `publish`
green, a per-job wall-clock table against the 2h54m baseline
(`#1246` / `37657691147`), storage evidence, and `rust_*` substitution evidence.
Only then mark #221 ready, merge, and drive a green Update on `main`.

Until that bar is met:

- stay on #221; milestones and status live on this PR only
- kick via `.github/update-kick` (feature-branch Update uses `cancel-in-progress`)
- do not merge #221 or drive `main` Update

## Hard constraints

- Never waive Darwin. Never skip Darwin roots or planned shards.
- Do not weaken the 400-local-drv gate (`MAX_SHARD_LOCAL_BUILDS`) without George.
- nixcfg stays off Linear.
- Do not change flake inputs / `flake.nix` / `flake.lock` / nixpkgs without George.
- Do not use `--no-verify`. Do not evict `aqsm7q08`. Do not drop zed from x86
  validation. No timeout/retry band-aids.
- `gh` is read-only for writes (403). Use ManagePullRequest for PR mutations.

## Current head

Code tip before this note: `3be750a36b33b1b2849f983437b4a3ff80875a6a`.
This file is a docs-only commit on that tip. Confirm the handoff SHA with
`git rev-parse HEAD` after you pull; it is also in the #221 comment that
posted this note.

What the tree already contains (ancestors of this file):

| SHA | What |
| --- | --- |
| `bab4a774` | Fail-closed Cachix socket wait (`require_cachix_daemon`) |
| `5bf5db5a` | Derivation-v4 relative Darwin store paths |
| `155125f0` | Kick that started `37809856116` |
| `ce43e92e` | Cherry-pick of `d6083a4f`: Linux rust_zed unsplit + 5-wide rust_* warmup |
| `65150039` | Dropped the #222 kick branch so only #221 triggers Update |
| `c7794ea0` | Realize rust_* warmup from exported `.drv` files, not output store paths |
| `3be750a3` | Kick that queued `37851246740` |

#222 is **closed as superseded** (2026-10-08T21:40:26Z). The rust_zed fix and
5-wide split live on this branch, not on `cursor/fix-zed-out-lib-cycle-6614`.

`outputs = [ "out" ]` / `outputDev = [ "out" ]` is **Linux-only**
(`linuxZedUnsplit` is identity on Darwin). Instantiated aarch64-darwin rust_zed
on `155125f0` and `ce43e92e`: same drv
`/nix/store/giqqsirxl7r5d6rgf3qyzibiamksrgm4-rust_zed-1.25.0.drv`, same out
`/nix/store/p8ihzbih3vkvyvvv9ixsmkl5x840kmrw-rust_zed-1.25.0`, still
`outputs = ["out" "lib"]`. **0 Darwin rust_* hashes moved; 0 of today's gkze
rust_* were invalidated.**

5-wide is on this head: `RUST_WARMUP_SLOTS = 5`, job
`validate-darwin-warm-rust` matrix slots 0–4, skip-if-in-gkze, per-derivation
push, 400-local-drv gate unchanged, root shards still always-run after rust-warmup
success. Packages inventory no longer serializes the 3755-path warmup.

## Outcome of `37809856116` (SHA `155125f0`)

Run: https://github.com/gkze/nixcfg/actions/runs/37809856116

This run **ended** at 22:13:03Z. Overall conclusion: **cancelled** (feature-branch
`cancel-in-progress` after the `3be750a3` kick). It **cannot publish green**.
It is the last single-slot warmup attempt.

| Job | Result | Wall |
| --- | --- | --- |
| prepare-darwin | success | 27m (16:34–17:01Z) |
| cache-darwin-linux-deps-x86 | success | 17m |
| prepare-arm | success | 28m |
| cache-darwin-linux-deps-arm | success | 36m |
| prepare-x86 | success | 12m |
| validate-arm | success | 25m |
| plan-darwin-closures | success | 28m |
| validate-x86 | **failure** | 73m (17:40–18:53Z) |
| validate-darwin-packages | **failure** | 217m (18:09–21:45Z) |
| validate-darwin-roots ×2 | cancelled 22:06Z | ~20m then cancelled |
| validate-darwin-closures | skipped | — |
| assert-coverage | **failure** | 7m (22:06–22:13Z); required jobs already failed/cancelled |
| publish | cancelled | 22:13Z |
| repair | cancelled | 22:13Z |

400-local-drv gate **passed** at plan time: warmup set 3755 (3680 rust_*), shard
remainders 76 and 80, `remaining_rust_crates = 0`.

### validate-x86

`error: cycle detected in build of '...-rust_zed-1.25.0.drv' in the references
of output 'lib' from output 'out'` (three retries). ARM skipped zed
(`meta.platforms` is `aarch64-darwin` + `x86_64-linux` only). Fixed on this
branch by Linux-only unsplit. Proven here with
`nix build .#packages.x86_64-linux.zed-editor-nightly` (`BUILD_EXIT:0`).

### validate-darwin-packages and warmup throughput

Inventory (`nix build` of package attrs, including zed) ran ~18:20–21:35Z.
That is what actually pushed rust_* to gkze.

Structural 3755-path warmup started at 21:35Z and died immediately:

```
[root-warmup] error: path '/nix/store/...-rust_*' is required, but there is no
substituter that can build it
```

`nix build` of an **output store path** cannot rebuild from source. Fixed on
this branch in `c7794ea0`: planner writes `outputDrvs` and a `warmup-drvs/`
cache; Darwin `nix-store --add`s the `.drv` files, then `nix build`s those.

gkze HIT of the planned 3755-path set (GET narinfo, User-Agent):

| When (UTC) | Hits | rust hits | Remaining |
| --- | ---: | ---: | ---: |
| 18:14 | 0 | 0 | 3755 |
| 19:25 | — | 28 | — |
| 19:46 | — | 435 | — |
| 21:14 | — | 1275 | — |
| 21:23 | — | 1308 | — |
| 21:27 | — | 1312 | — |
| 21:46 | 1319 | 1298 | **2436** |

Overall ~374 paths/h (0 at 18:14 → 1319 at 21:46). Peak during inventory ramp
~1160 rust/h; later flat. At ~400 paths/h, 2436 remaining is ~6.1h and does
**not** fit one 6h/`timeout-minutes: 360` macos-15 slot. The next run must use
the 5-wide split, not single-slot warmup.

Live-candidate rust_zed from that plan (prepare-applied tree, not the committed
tree) is still split and still in gkze:

- `/nix/store/95n6ci9fxwpgxw8xmbismw39prmalh72-rust_zed-1.25.0`
- `/nix/store/1g9sh7spvxsy0bn0am6pwvldd0cvhg0f-rust_zed-1.25.0-lib`

## Root causes found so far

1. **Cachix socket race** (`37802332798` prepare-darwin). Gate looked at pid/hook
   while `daemon.sock` was not up yet. Fail-closed wait is `bab4a774`.
2. **rust_zed out↔lib cycle** (`37809856116` validate-x86). crate2nix splits
   lib+bin; Linux rustc records `$out` inside `$lib`. `overrideAttrs { outputs =
   ["out"]; }` is too late because `outputDev` stays `["lib"]`. Builder-arg
   `outputs` + `outputDev = ["out"]` on Linux only. Darwin hashes stay split.
3. **rust_* eviction / 15× local builds vs #1246**. Same nixpkgs pin. The extra
   local work is crate2nix `rust_*` (custom hashes, not on cache.nixos.org) plus
   gkze LRU dropping yesterday's rust_* — not a stdenv overlay. CVE coreutils
   patches in nixpkgs change stdenv vs hydra; do not drop them here.
4. **Misread post-hook log**. `Cachix Daemon not started. Skipping push` on the
   action post-hook is **success after we drain and clear `CACHIX_DAEMON_DIR`**.
   It is not proof that the job failed to push. Real push is the daemon +
   explicit flush last.

Also: realizing warmup as `nix build /nix/store/<output>` cannot compile missing
paths. That is why packages died at 21:35Z after inventory.

## gkze plan / limit / usage

Public cache `gkze.cachix.org` (ZSTD, priority 41). `GET /api/v1/cache/gkze/pin`
returns `[]`. This environment had no `CACHIX_AUTH_TOKEN`, so authenticated
`/api/v1/user` and `/details` (`subscriptionPlan`, `subscriptionStorageLimit`,
`subscriptionStorageUsage`) could not be read. Prior inference still stands:
**Starter / 50 GiB** is the most consistent fit; LRU by last-accessed (or
creation if never accessed) is firing. Pins are immune with `--keep-revisions`
/ `--keep-days`. Do not evict `aqsm7q08`.

## Eviction-fix options (costs)

1. **Pin the four Darwin roots** (`argus`, `rocinante`, `zeus`, `home-george`)
   with `--keep-revisions 2` after a successful warmup. ~$0. Preferred. Stops
   the root-closure treadmill without pinning 3680 rust_*.
2. **Starter → Standard**. Historically +€40 / ~$42/mo and +200 GiB (50 → 250).
   Buys headroom; does not pin. Current public page may say contact sales.
3. **Do not pin every rust_***. 3680 paths would blow pin budget and still
   churn on the next crate bump.

## Already queued (do not double-kick)

A previous turn already kicked **Update `37851246740`** on `3be750a3`
(https://github.com/gkze/nixcfg/actions/runs/37851246740). That SHA is an
ancestor of this docs-only handoff and already has the rust_zed unsplit,
5-wide split, and `.drv` realize. At handoff write time that run was
**in_progress** (started ~22:13Z). Feature-branch `cancel-in-progress`
cancelled the `37809856116` root shards at 22:06Z and finished that run
at 22:13Z when the newer kick began.

This handoff **does not kick**. A docs-only push does not match
`update.yml` `paths: .github/update-kick`, so it will not cancel
`37851246740`. Touching `.github/update-kick` again **will** cancel it.

## Open questions

- Authenticated gkze `subscriptionPlan` / `subscriptionStorageLimit` /
  `subscriptionStorageUsage` / `totalFileSize` (need the live token).
- Whether `37851246740` actually starts 5-wide rust-warmup and whether the
  exported `.drv` cache imports on macos-15 (`nix-store --add`).
- Whether skip-if-in-gkze plus 5-wide finishes the remaining ~2436 before the
  6h cap (projected ~1.2h/slot at 400/h if layers stripe cleanly).
- 2-wide vs 4-wide root shards: still provisional; measure bytes written and
  update-runtime after rust_* are in gkze.
- #1246 was 2h54m with shards 2–4 skipped by design. The done-bar table must
  compare a **no-skip** run, not copy that skip.

## Exact next step

Kick **one** full Update on head `3be750a36b33b1b2849f983437b4a3ff80875a6a`
(the required tree: rust_zed unsplit + 5-wide + `.drv` realize).

That kick is **already live** as `37851246740`. Watch it. Do not start a
second kick while it is pending or in progress.

If `37851246740` is dead (cancelled, never started, or failed before
rust-warmup for a reason this tree already fixes), kick **one** full Update
on the current #221 head after you `git rev-parse HEAD` (this docs commit,
or later code if you landed more):

```text
# edit .github/update-kick with a new timestamp line only
# commit: chore(update): kick Update after <reason>
# push cursor/no-skip-darwin-shards-6614
```

Do not kick while `37851246740` is alive. Do not change flake inputs. Do not
merge #221. Do not drive `main` Update until the done bar above is green.
Do not schedule more self-check-ins or timers from this lane.
