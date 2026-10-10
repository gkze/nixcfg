# Update CI handoff (#221)

Lane for `cursor/no-skip-darwin-shards-6614` / [PR #221](https://github.com/gkze/nixcfg/pull/221).
George approved handing this to a fresh agent once `37809856116` ended.
This note is the stop point after the #1260 mixed-generation / intern
fix is on the branch: **do not kick a second Update from that commit
until `37998753277` has fully ended (including repair), and do not
self-schedule checks.** The manager routine watches the run.

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

Confirm the lane SHA with `git rev-parse HEAD` after you pull. The tree
already contains:

| SHA | What |
| --- | --- |
| `bab4a774` | Fail-closed Cachix socket wait (`require_cachix_daemon`) |
| `5bf5db5a` | Derivation-v4 relative Darwin store paths |
| `155125f0` | Kick that started `37809856116` |
| `ce43e92e` | Cherry-pick of `d6083a4f`: Linux rust_zed unsplit + 5-wide rust_* warmup |
| `65150039` | Dropped the #222 kick branch so only #221 triggers Update |
| `c7794ea0` | Realize rust_* warmup from exported `.drv` files, not output store paths |
| `3be750a3` | Kick that queued `37851246740` (#1254) |
| `2ab7291b` | Replace `nix-store --add` with store-closure export/import |
| `1450528e` | Kick that queued `37868270521` (#1255) |
| `e7bc1c7b` | Named fetchurl `cannot download \\S+ from any mirror` retry |
| `d77a6fc8` | Rosetta recache after rust-warmup + daemon kickstart |
| `bd56a070` | Kick that queued `37947952083` (#1258); terminal, cause 10 |
| `45bb4efd` | Bare-E0463 classifier + temporary Darwin locator diagnostic |
| `950a8812` | Kick that queued `37976189014` (#1259); terminal, cause 11 |
| `85c7f526` | ruff-format of the diagnostic helpers |
| `493c899b` | Darwin `language_models` extra `-C metadata` (wrong; caused #1260 mix) |
| `2ff8fb84` | Kick that queued `37998753277` (#1260) |
| this head | Revert that salt; same-slot `language_models --rebuild` before `agent_ui` |

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
5. **`nix-store --add` CA path** (`37851246740` / #1254). Copying `.drv` files
   and `--add`ing them minted `<newhash>-<oldhash>-name.drv`. Fixed in
   `2ab7291b` by exporting the original store closure.
6. **Unrooted import + `nix build foo.drv`** (`37868270521` / #1255).
   `nix-store --import` of `closure.nar` exited 0, but hosted `min-free =
   32GiB` GC collected the unrooted `.drv`s (`/nix` stayed ~4.6Gi). The
   realize step then asked `nix build /nix/store/foo.drv`, which only
   substitutes the derivation text (`don't know how to build` / no
   substituter). Fix: `nix copy --derivation` into a `file://` cache,
   GC-root each imported `.drv`, `nix path-info` fail-closed, and
   `nix build foo.drv^*`.
7. **`nix-store --add-root` is not an operation** (`37879308953` / #1256).
   Copy-back reached rooting, then macos-15 `nix-store --add-root ROOT
   --indirect DRV` exited `error: no operation specified`. `--add-root` is
   a modifier; `--realise` on a `.drv` would build outputs. Root with
   `nix build --out-link --offline`. PR quality `certify-python` also
   failed: ruff format on `test_update_candidate.py`.
8. **Rosetta VM missing + daemon crash** (`37890830675` / #1257). Warm-rust
   slots 0-4 succeeded. `cache-darwin-linux-deps` had pushed the
   aarch64-linux VM from the Darwin-prepare tree *before* warmup; rust_*
   then LRU-evicted it. Darwin roots `--fallback`-built `etc-fstab.drv`
   (`platform mismatch`), then EILSEQ unlinked an `active-builds` lock and
   the Nix daemon died. Isolation retried 3× against the dead socket.
   Recache after warmup from the final candidate; Darwin substitutes the
   Linux boundary with `--max-jobs 0`; restart determinate-nixd before
   daemon-disconnect retries. The post-hook `Cachix Daemon not started`
   line is still cause 4 (flush already drained). Nothing from that
   shard reached gkze because Darwin never realized the Linux VM;
   per-derivation hook + `if: always()` flush stay in place.
9. **JSR fetchurl name is not "source"** (`37890830675` / #1257
   argus+home-george). `curl: (6) Could not resolve host: jsr.io` then
   `cannot download _std_collections-1.1.6-sum_of_test.ts from any
   mirror`. The URL `https://jsr.io/@std/collections/1.1.6/sum_of_test.ts`
   is HTTP 200 and its sha256 matches `packages/linear-cli/deno-deps.json`.
   The retry list only matched the literal `cannot download source from
   any mirror`; Deno `fetchurl` names the file. Match `cannot download
   \\S+ from any mirror` on the validation network path.
10. **`rust_agent_ui` E0463 for `language_models` is not a missing Cachix
    rlib** (`37947952083` / #1258 warm-rust 3 / `113921652313`).
    `ks6dzvch…-rust_agent_ui-0.1.0.drv` built from source. Both
    `h3crq11a…-lib` and sibling `gxx4jc6a…-lib` (`language_model`)
    substituted from `gkze.cachix.org`. rustc `--extern language_models=`
    and `--extern language_model=` named the hashed store rlibs. The only
    error, 3/3 identical, is bare `error[E0463]: can't find crate for
    \`language_models\`` at `src/buffer_codegen.rs:24` (`use
    language_models::provider::…`) with no `ExternLocationNotExist`,
    `via_invalid` / E0786, or `via_triple` / E0461 notes. `language` and
    `language_model` in the same invocation produced no error.

    The "cached `-lib` is missing the rlib / stale rust_zed split" hunch
    is **disproven**. Cachix NAR `h3crq11a` is
    `/lib/liblanguage_models-f75b2474e2.rlib` (42070568) + `/lib/link` +
    `propagated-build-inputs`. `$out` (`7h1bjnkidcms`) is empty (normal
    lib-only). BSD ar has `#1/12 lib.rmeta` (Mach-O ARM64,
    `__DWARF,.rmeta`, `#rustc 1.98.1 (48a229cea 2026-09-01)`). rustc
    1.98.1 on Linux reads that NAR and reports crate `language_models`,
    triple `aarch64-apple-darwin`, `is_proc_macro=false` (E0461 wrong
    host triple — crate found). `linuxZedUnsplit` is Linux-only and only
    for crateName `zed`. Evicting `h3crq11a` is useless (same hash → same
    NAR). Cargo.nix has no `language_models` cycle; `--extern` uses exact
    `BTreeMap` keys (`language_model` ≠ `language_models`). rustc
    `find_commandline_library` would emit notes for a missing or unreadable
    `--extern` path. Bare E0463 is `LocatorCombined` with empty rejections
    or `CrateError::NotFound` — Darwin rustc 1.98.1 rejected this crate
    after substitution, not a store I/O fault.

    The retry classifier `_nix_output_has_unreadable_store_rlib` treated
    any E0463 plus any store `--extern` as transient (Update #1244). That
    is why #1258 retried 3×. This head requires a locator I/O note
    (`extern location … does not exist` / `is not a file`, vanished
    `.rlib`, or EILSEQ on the rlib). Bare E0463 is a builder failure.

    The post-hook `Cachix Daemon not started. Skipping push` is still
    cause 4 (flush already drained). Packages, closures, roots, and
    linux-deps were skipped because warm-rust 3 failed.

    A Darwin-native diagnostic does **not** need George. `45bb4efd` added
    a temporary Darwin `agent_ui` locator log plus a warm-rust
    `language_models --check`. The `--check` `print()` never appeared in
    hosted logs (stdout was not the rust-warmup progress logger).
11. **`intern_stable_crate_id` NotFound after a successful rlib read**
    (`37976189014` / #1259 warm-rust 0+1 / `114006058130` +
    `114006057970` @ `950a8812`, branch head was `85c7f526`). Same
    compile error on both slots, not an import or substituter fault.
    Locator read
    `x49g8qy1…-lib/lib/liblanguage_models-f75b2474e2.rlib` (substituted
    from gkze; extra-filename still `f75b2474e2`, Nix path moved from
    `h3crq11a` because `45bb4efd` added an `agent_ui` key to the shared
    `crateOverrides` closure). creader logged `register newly loaded
    library for language_models`, then **no** `register crate … cnum`
    line, then `resolving crate core` (the `resolve_crate` error
    handler's `missing_core` probe; core was Previous cnum 2). Bare
    E0463 at `src/buffer_codegen.rs:24`. rustc 1.98
    `intern_stable_crate_id` maps `create_crate_num` miss +
    `metas[existing] is None` to `CrateError::NotFound` — the only intern
    error that is bare E0463 (`SymbolConflictsCurrent` /
    `StableCrateIdCollision` have other messages). Cargo.nix has **no**
    `language_models` runtime cycle (dependents: `agent_ui`, `eval_cli`,
    `edit_prediction_cli`, `zed`). So this is not a crate2nix dep-graph
    loop; it is the cached rlib's StableCrateId colliding with an
    in-progress / leftover `metas=None` slot, same shape as
    nixpkgs#482646 (substitutable rlib rustc can `--extern` but cannot
    intern). The `agent_ui` locator override is removed. `493c899b`
    then appended Darwin-only `-C metadata=nixcfg-221-e0463` for
    crateName `language_models`. Linux stayed identity. Do not evict
    `h3crq11a`. **#1260 proved that salt mixed generations and did not
    intern** — see cause 12.

    Slots 2/3/4 of #1259 succeeded. #1257 holds on this head: Rosetta
    recache still `needs: validate-darwin-warm-rust`; Darwin Linux-VM
    realize still `--max-jobs 0`; daemon disconnect still
    `launchctl kickstart`; fetchurl retry still `cannot download \\S+
    from any mirror`. The cachix-action post `Cachix Daemon not started.
    Skipping push` is still cause 4: explicit flush ran
    `cachix daemon stop returncode=0` (daemon.log 352 → 644801 bytes;
    `nix.conf` has `post-build-hook`; `NIX_CONF_has_post_build_hook=False`
    is the require-script looking at `NIX_CONF` instead of
    `NIX_USER_CONF_FILES`). `Collected 0 prefetched store paths` is the
    prefetch-receipt counter, not a failed per-derivation push.

    Packages, roots, closures, and linux-deps were skipped because
    warm-rust 0/1 failed. assert-coverage `114035222846` required-jobs
    failed. publish skipped. repair `114037497085` finished failed;
    the run ended 21:48:55Z.
12. **`493c899b` `-C metadata=nixcfg-221-e0463` mixed rust_* generations
    and did not fix intern** (`37998753277` / #1260 warm-rust 0/1/2 /
    `114074133244` + `114074133247` + `114074133308` @ `2ff8fb84`).
    Slots 3 and 4 passed. Slots 0/1/2 failed, so packages/roots/closures
    and both cache-darwin-linux-deps jobs skipped.

    Slot 0 title_bar `s77zkghb…-rust_title_bar-0.1.0.drv` built from
    source against **substituted** `lbm5c5mc…-rust_recent_projects-0.1.0-lib`
    while also **building** `jwk3kr03…-rust_extension_host-0.1.0.drv`.
    rustc E0460: `found possibly newer version of crate extension_host
    which recent_projects depends on` — `target/deps/libextension_host-85905da4ec.rlib`
    vs the cached recent_projects rlib. Slot 3 built a *different*
    recent_projects drv `xsr87bz6` from source. That is a mixed-SVH
    graph, not a missing dep edge.

    The intern salt produced new `r00ivv9k…-rust_language_models-0.1.0-lib`
    with the **same** extra-filename `f75b2474e2`. Slots 0/1/2/3
    **substituted** that NAR from gkze (slot 3 never compiled it; it
    was already in Cachix). agent_ui `--extern language_models=r00ivv9k…/liblanguage_models-f75b2474e2.rlib`
    still bare E0463 at `buffer_codegen.rs:24`. So a from-source
    language_models rebuild that is then substituted onto the agent_ui
    runner does **not** intern. nixpkgs#482646 shape: rustc can
    `--extern` a substitutable rlib and still `intern_stable_crate_id`
    → `CrateError::NotFound`.

    This head reverts the Darwin-only extra `-C metadata` (restores
    the pre-`493c899b` rust_* hashes for non-dependents so title_bar /
    recent_projects / extension_host can substitute as one generation)
    and `--rebuild`s `rust_language_models` on the same macos-15 slot
    that compiles `agent_ui`, after the agent_ui drv closure import.
    Other rust_* stay substitutable. Do not evict `h3crq11a`.

    Cachix post `Cachix Daemon not started. Skipping push` is still
    cause 4: require-script captured daemon.log at 352 bytes (startup
    only); explicit `if: always()` flush already stopped the socket
    (`cleared CACHIX_DAEMON_DIR`). Per-derivation post-build-hook
    pushes of successful rust_* in those slots are not that line.
    `NIX_CONF_has_post_build_hook=False` is the require-script looking
    at `NIX_CONF` instead of `NIX_USER_CONF_FILES`. #1257 holds.

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

`37998753277` (#1260) @ `2ff8fb84` is the run this fix waits on. Do
not kick until that run's conclusion is set, including repair. Warm-rust
3/4 succeeded; 0/1/2 failed (cause 12); Darwin packages/closures/roots
and linux-deps skipped. `37976189014` (#1259), `37947952083` (#1258),
and `37890830675` (#1257) are also terminal (causes 8–11). The next
Update is the same-slot language_models `--rebuild` kick
(`.github/update-kick`). Do not queue a second one.

## Open questions

- Authenticated gkze `subscriptionPlan` / `subscriptionStorageLimit` /
  `subscriptionStorageUsage` / `totalFileSize` (need the live token).
- Whether skip-if-in-gkze plus 5-wide finishes the remaining rust_* before
  the 6h cap once `foo.drv^*` actually builds after import.
- 2-wide vs 4-wide root shards: still provisional; measure bytes written and
  update-runtime after rust_* are in gkze.
- #1246 was 2h54m with shards 2–4 skipped by design. The done-bar table must
  compare a **no-skip** run, not copy that skip.

## Exact next step

**After `37998753277` fully ends, kick exactly one Update** (this head).
Watch warm-rust agent_ui slots for `nix build …rust_language_models… --rebuild`
on the same runner as `rust_agent_ui`, then agent_ui succeeding without
E0460 on title_bar/recent_projects. Do not evict `h3crq11a` or
`aqsm7q08`. Do not change flake inputs without George. Do not merge
#221. Do not drive `main` Update until the done bar above is green.
Do not schedule self-check-ins or timers; the manager routine watches
the run.
