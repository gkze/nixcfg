# GitHub Actions free-tier Update ceiling

Measured from no-skip Update
[39166099302](https://github.com/gkze/nixcfg/actions/runs/39166099302)
(`295eef92`, 2026-10-18 21:03:17Z–2026-10-19 02:09:54Z). Baseline is
[#1246](https://github.com/gkze/nixcfg/actions/runs/37657691147) at **2h54m**
with Darwin shards 2–4 skipped by design. Do not copy that skip into a
done-bar comparison.

This is a report. It does not enable paid runners.

## Constraints (public repo, no spend)

GitHub Actions on a public repository:

- Unlimited minutes.
- 6 hour job timeout (`timeout-minutes` must stay under that).
- About 20 concurrent jobs on the free plan.
- Standard `macos-15` is a 3-core / 7 GiB hosted runner. Larger Mac
  SKUs (`macos-15-xlarge` and friends) are paid and are out of scope.
- Standard `ubuntu-24.04` is 4-core / 16 GiB. 4-wide Darwin
  `derivation show --recursive` SIGTERM'd that image at ~800s
  (38137325241, 38142145131), so plan-shards stays serial until proven
  otherwise.
- `gkze.cachix.org` is treated as Starter / 50 GiB with LRU eviction.
  Pins (`cachix pin`) are the free way to keep the Rosetta VM image and
  four Darwin roots.

This repo currently uses at most **5** `macos-15` jobs at once (public
macos pool plus Cachix LRU). The 5-wide rust and cascade stripes occupy
that pool; packages wait so they do not steal a slot.

## 5h06m critical path (39166099302)

Wall clock was **306 minutes**. Additive critical path:

| Phase | Wall | Notes |
| --- | ---: | --- |
| prepare-pins | 4m | ubuntu |
| prepare-darwin | 39m | longest of the three native prepares |
| prepare-merge | 3m | waits Darwin + arm + x86 |
| plan-darwin-closures | 50m | serial graph eval; **blocks rust warmup** |
| canary-warm-rust | 8m | serial before the 5-wide stripe |
| validate-darwin-warm-rust | 30m | slots 21–30m, already fairly even |
| canary-warm-cascade | 7m | serial before cascade 5-wide |
| cascade-warmup | 47m | slots **16 / 46 / 37 / 13 / 11m** |
| linux-deps | 15m | waits for cascade today |
| Darwin roots (4-wide) | 36m | argus longest; none skipped |
| canary-darwin-cache | 4m | ubuntu path-info |
| validate-darwin-closures | 40m | aggregate proof, mostly substitute |
| assert-coverage | 7m | |
| publish certify | 16m | `5996 passed`, coverage 100% |

Linux validate and FOD canary ran beside plan-shards and were not on
the critical path.

## Where the extra ~2h12m went versus 2h54m

The skip-shards baseline did not run rust-warmup, cascade-warmup, or
roots 2–4. No-skip adds that work on purpose. The avoidable part is
queueing, not the builds:

1. **Cascade slot imbalance (22m on the wall).** 123 minutes of cascade
   compile packed 5-wide would be ~25m if even; slot 1 ran 46m.
2. **linux-deps after cascade (15m).** Recaching `nixos-disk-image` does
   not need cascade outputs; it only needs rust-warmup and the merged
   tree.
3. **Serial rust canary (8m).** Default push still runs settings-family
   canary on one macos-15, then starts five validate slots. Canary
   failure currently does not even fail-close the 5-wide job.
4. **plan-shards 50m** is eval, not compile. Serial on purpose. Not
   lowered here (2-wide/4-wide SIGTERM'd 16 GiB ubuntu).
5. **Aggregate closures 40m** after four green roots. If gkze LRU
   dropped a root NAR, this job compiles; pinning roots is the free
   fix. If it is pure substitute I/O, bigger Macs would help and we
   will not buy them.

## Theoretical free-tier ceiling

Same validation, no skipped shards, no paid SKUs.

**Cold no-skip** (empty gkze for this candidate), after the draft
speed-ups in this batch:

- Keep prepare Darwin ~39m and plan-shards ~50m.
- Drop serial rust canary (~8m) by overlapping it with the 5-wide
  stripe (6 macos jobs; one may queue if the pool is still 5).
- Balance cascade so the wall is ~25m instead of 47m.
- Overlap linux-deps with cascade (remove 15m from the critical path).
- Pin Darwin roots so closures stay substitute-only (~15–25m instead of
  40m if LRU was the cause).
- Crane-style dep-first rust realize: a few minutes on a cold rust
  stripe, not a new job.

Expected cold wall: **about 4h05m–4h20m** (down from 5h06m). Still
above 2h54m because no-skip actually builds four roots and the shared
warmup.

**Warm incremental** (gkze already holds rust_*, cascade, and the four
roots from the previous green run):

- rust-warmup and cascade become skip-if-in-gkze no-ops (~5–10m each
  including drv import, or seconds if import is skipped when the stripe
  is empty).
- Roots and closures substitute.
- plan-shards (~50m) and Darwin prepare (~39m) plus certify (~16m)
  remain.

Expected warm wall: **about 2h30m–2h50m**, which can meet or beat the
skip-shards baseline **without skipping shards**. The remaining ceiling
is plan-shards eval plus Darwin prepare hashing.

**Absolute floor** if prepare Darwin and plan-shards also hit cache
(no pin/hash work, reused graphs): certify + coverage + a short
substitute of four roots. That is **under 1h**, but this repo’s Update
always re-prepares and re-plans so it is not the operational ceiling.

## Bigger Mac runners (report only)

| SKU | Cores / RAM | Money | Expected compile speedup |
| --- | --- | --- | --- |
| `macos-15` (current) | 3 / 7 GiB | free | 1.0× (39166099302) |
| `macos-15-xlarge` | 6 / 14 GiB | paid | ~1.5–2× rust/cascade/roots |

Paid large Macs could cut rust 30m→~18m, balanced cascade 25m→~15m,
roots 36m→~22m. That is ~30–40m off a **cold** run and almost nothing
off a **warm** run that only substitutes. Not enabled.

## 39166099302 follow-on speedups (no skipped work)

Measured on the 306m no-skip run. No queue time; the cap is 5 macos-15
jobs plus serialized steps. Each critical-path Mac job spent 5.7–9.2m
on setup (~56m total); Xcode/Android purge was 3.3–4.6m of that.

| Change | Measured basis | Expected wall |
| --- | --- | ---: |
| Fold assert-coverage into publish (first step, blank write token) | 7.2m macos job for <30s of work | ~7m |
| Skip Xcode/Android on publish/repair; 8 GiB fail-closed floor | 3.3–4.6m purge on that job | ~4m |
| Fold canary-darwin-cache into closures as a pre-build step | 4m ubuntu path-info after roots | ~3m |
| Start cascade canary in the slot rust canary frees, not after all rust slots | 6.8m canary for 17s of work; rust canary 8m, rust slots 21–30m | 0–7m |
| Batch `nix path-info --json` (gkze first, then hydra remainder) | plan-shards 50m includes serial path-info of 31.6k outputs | a few minutes of plan-shards |
| Determinate `eval-cores = 2` plus host nix on PATH | `unknown setting 'eval-cores'` from devshell `pkgs.nix` | 0m until `derivation show` is measured parallel |

Expected additional cut on a similarly shaped cold run: **about 14–21m**.
`nix derivation show` is not in Determinate's parallel-eval command list,
so eval-cores is a warning fix, not a claimed 27m win. Per-path df after
each purge is diagnostic only.

## What not to do

- Do not skip Darwin shards, rust-warmup, cascade, coverage, or
  certify to chase 2h54m.
- Do not raise `DARWIN_GRAPH_EVAL_WORKERS` without a passing 2-wide
  plan-shards measurement; 4-wide already SIGTERM'd. Do not set
  `eval-cores = 0` (all cores) on the 16 GiB ubuntu planner.
- Do not pin every rust_* (thousands of pins, still churns on the next
  crate bump). Pin the four Darwin roots and the linux-builder image.
- Do not `workflow_dispatch` or `gh run cancel` from the Cursor GitHub
  App token (403). Kick remains a `lib/update/**` push, and these
  speed-up branches are **not** `cursor/no-skip-darwin-shards-6614`.
- Do not buy `macos-15-xlarge`. Do not drop the combined closures proof.
- Do not remove canaries. Do not overlap cascade canary with the cascade
  5-wide stripe (duplicate buzz compile / 6 macos vs a 5 pool).
