# CI Failure Analysis Report — 2026 YTD (Jan 1 – Mar 3)

## Executive Summary

The **Periodic Flake Update** workflow (`update.yml`) is the primary source of CI
instability. It runs every 6 hours (plus on every push to `main`) and has
accumulated **~398 total runs** since inception, with roughly **270 runs since
January 1, 2026**. The vast majority of non-success outcomes fall into three
categories: **hash mismatches in fixed-output derivations (FODs)**, **transient
network/infrastructure failures**, and **concurrency cancellations**.

The **CI** workflow (`ci.yml`) is lightweight (commitlint + pytest) and has had
only **20 runs** total — it is not a significant source of instability.

---

## Workflow Overview

| Workflow | File | Trigger | Frequency | Total Runs |
|---|---|---|---|---|
| Periodic Flake Update | `update.yml` | `schedule` (every 6h), `push` to main, `workflow_dispatch` | ~4–6×/day | ~398 |
| CI | `ci.yml` | `pull_request`, `push` to main | Per-PR/push | 20 |
| Copilot coding agent | (dynamic) | Issue assignment | On-demand | — |

The update workflow has a complex **10-phase pipeline**:

1. `update-lock` — Update `flake.lock`
2. `resolve-versions` — Pin upstream package versions
3. `compute-hashes` — Build on 3 platforms (aarch64-darwin, x86_64-linux, aarch64-linux)
4. `merge-hashes` — Merge per-platform `sources.json` files
5. `quality-gates` — Formatting, linting, codegen checks
6. `darwin-shared-heavy` — Build heavy macOS targets (e.g., `zed-editor-nightly`)
7. `darwin-shared` — Build shared Darwin closure
8. `darwin-argus` / `darwin-rocinante` — Per-host Darwin configs
9. `linux` — Linux smoke check
10. `create-pr` — Create PR with all updates

A failure in **any** phase blocks all downstream phases.

---

## Failure Categories

### 1. 🔴 Hash Mismatches in Fixed-Output Derivations (FODs) — **Most Frequent**

**Impact:** Blocks `compute-hashes` or `darwin-shared` phases, cascading to skip
all downstream jobs.

**Root cause:** Packages that fetch from rapidly-updating upstream sources
(nightly builds, rolling releases) have their download hashes change between when
the pipeline computes them and when they are used. The Nix FOD mechanism requires
exact hash matches, so stale hashes cause build failures.

**Affected packages observed:**

| Package | Date | Error |
|---|---|---|
| `VSCode-insiders` (aarch64-darwin) | Feb 23 | Hash mismatch for `VSCode-insiders-1.110.0-insider-darwin-arm64.zip` |
| `axiom-cli` (go-modules) | Feb 1 | Hash mismatch — placeholder `sha256-AAA...` vs actual hash |
| `codex` (cargo-deps-vendor) | Feb 1 | Build failed due to dependency hash mismatch |
| `beads` | Feb 1 | Build failure in compute-hashes |

**Example error (Feb 23):**
```
##[error]To correct the hash mismatch for VSCode-insiders-1.110.0-insider-darwin-arm64.zip,
use "sha256-4GrhK/RnoJJpQBRmTqSK81bWtxUX/bMvGyemoVrx4zU="
```

**Example error (Feb 1):**
```
error: hash mismatch in fixed-output derivation '.../axiom-cli-v0.14.8-go-modules.drv':
         specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
            got:    sha256-tDl2pmHPNXxpUdFLCBM33SQX/SsNtn7XqrZkZsy8k6o=
```

### 2. 🟡 Transient Network / Infrastructure Failures

**Impact:** Blocks `quality-gates` or other phases that download external
binaries.

**Root cause:** GitHub Actions runners occasionally experience transient DNS
resolution failures or network connectivity issues when downloading external
tools (Deno, Nix packages from cache, etc.).

**Example (Mar 3, Run #479):**
```
error sending request for url (https://dl.deno.land/release/v2.6.10/denort-x86_64-unknown-linux-gnu.zip):
client error (Connect): dns error: failed to lookup address information:
Temporary failure in name resolution
```

This failure occurred in the `quality-gates` job when Deno tried to compile/run
TypeScript code for the quality gate checks.

### 3. 🟠 Concurrency Cancellations — **Expected but Wasteful**

**Impact:** Runs are cancelled mid-execution, wasting compute resources.

**Root cause:** The workflow uses `concurrency: { group: flake-update, cancel-in-progress: true }`.
Since it runs every 6 hours AND on every push to `main`, pushes during a
scheduled run cancel the in-progress run. With macOS builds taking 1–3 hours,
this frequently causes cancellations.

**Example (Feb 26, Run #449):** Cancelled after running for 6 hours
(12:24 → 18:24 UTC).

### 4. 🟣 Long-Running Darwin Builds

**Impact:** darwin-shared and per-host Darwin builds consume significant runner
time (often 30–60+ minutes each), making the overall pipeline take 2–4 hours
per successful run.

**Root cause:** Building the full Darwin system closure involves many derivations,
and pushing them all to Cachix adds overhead. Combined with the concurrency
cancellations, runs are frequently interrupted before completing.

---

## Sampled Run Results (2026)

| Run # | Date | Conclusion | Failed Job | Root Cause |
|---|---|---|---|---|
| #479 | Mar 3 | ❌ failure | quality-gates | DNS failure downloading Deno |
| #449 | Feb 26 | ⚠️ cancelled | — | Concurrency cancellation |
| #419 | Feb 23 | ❌ failure | darwin-shared | VSCode Insiders hash mismatch |
| #389 | Feb 17 | ❌ failure | darwin-shared | Build/hash issue |
| #329 | Feb 10 | ✅ success | — | — |
| #269 | Feb 1 | ❌ failure | compute-hashes (3 jobs) | Hash mismatches: codex, axiom-cli, beads |
| #209 | Jan 21 | ✅ success | — | — |

**Observed success rate (from sample): ~29%** (2 successes out of 7 sampled runs)

---

## Recommendations

### High Priority

1. **Retry transient network failures** — Add retry logic to steps that download
   external binaries (Deno, etc.). Use `retry` wrappers or the
   `nick-fields/retry` action for network-dependent steps.

2. **Handle FOD hash mismatches gracefully** — For packages with nightly/rolling
   releases (VSCode Insiders, codex, etc.), consider:
   - Auto-updating hashes on failure and retrying
   - Using `nix-prefetch-url` to verify hashes before building
   - Excluding highly volatile packages from the automated pipeline
   - Adding a post-hash-computation verification step

3. **Reduce concurrency conflicts** — Since the pipeline takes 2–4 hours and
   runs every 6 hours, consider:
   - Increasing the schedule interval to every 12 or 24 hours
   - Not triggering on `push` to `main` (let the schedule handle it)
   - Using `cancel-in-progress: false` to let runs complete

### Medium Priority

4. **Split volatile vs. stable packages** — Separate the pipeline into:
   - A "stable" pipeline for packages with stable release hashes
   - A "nightly" pipeline for VSCode Insiders, codex, etc. (can tolerate failures)

5. **Add better error reporting** — Surface the specific failure category in PR
   comments or workflow annotations to speed up triage.

6. **Cache Deno binary** — Pin and cache the Deno binary used in quality-gates
   to avoid downloading it from `dl.deno.land` on every run.

### Low Priority

7. **Optimize Darwin build times** — Use more aggressive Cachix caching and
   consider building fewer derivations per run (incremental updates).

8. **Add CI health dashboard** — Track success rates and failure categories
   over time to measure improvement.

---

## Appendix: Workflow Architecture

```
┌─────────────┐
│ update-lock  │  Phase 1: Update flake.lock
└──────┬──────┘
       ▼
┌──────────────────┐
│ resolve-versions  │  Phase 2: Pin upstream versions
└──────┬───────────┘
       ▼
┌──────────────────────────────────────────────────────┐
│ compute-hashes (3 parallel: darwin, linux, linux-arm) │  Phase 3
└──────┬───────────────────────────────────────────────┘
       ▼
┌──────────────┐
│ merge-hashes  │  Phase 4: Merge per-platform sources.json
└──────┬───────┘
       ▼
┌────────────────┐  ┌────────────────────┐  ┌─────────┐
│ quality-gates   │  │ darwin-shared-heavy │  │ linux   │  Phases 5-9
│ (fmt, lint,    │  │ darwin-shared       │  │         │
│  codegen)      │  │ darwin-argus        │  │         │
│                │  │ darwin-rocinante    │  │         │
└────────┬───────┘  └──────────┬─────────┘  └────┬────┘
         │                     │                  │
         └─────────┬───────────┘──────────────────┘
                   ▼
           ┌───────────┐
           │ create-pr  │  Phase 10: Create update PR
           └───────────┘
```

*Report generated: 2026-03-03*
