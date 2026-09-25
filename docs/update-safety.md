# Update safety invariants

This is the executable safety argument for `nixcfg update`. Each invariant
names the code that owns it and the test that pins it. Reviewers of the update
runtime should verify changes against this list instead of reconstructing the
safety argument from scattered modules; authors of a change that weakens one
of these invariants must call it out explicitly.

The pipeline shape: Python owns the update graph (feeds, reviews, file
rewrites), DBOS owns workflow/step recovery, SQLite retains immutable baselines and
candidate contents, Nix owns the build graph below the file boundary, and the UI
is a pure consumer of run events.

## Candidate isolation and write authority

1. **One writer per repository.** A process-global lock plus a repo-level
   file lock admit exactly one update workspace per checkout; concurrent
   runs fail fast instead of interleaving. (`persistence.IsolatedUpdateWorkspace`)

2. **The live checkout is immutable during a run.** All updater work happens
   in a disposable candidate tree; the live checkout changes only at
   promotion. (`persistence.IsolatedUpdateWorkspace`)

3. **Write authority is predeclared.** Every persisted path must belong to
   the planner-declared upper bound; an unexpected write aborts the
   transaction before promotion. (`cli._workspace_allowed_paths`)

4. **Snapshots are stable or rejected.** A source view is read once and
   verified by a stat-fingerprint second pass (`st_ctime_ns` changes on any
   write or metadata change, `st_ino` changes on replacement); a moving
   working tree fails the run rather than validating a mixed state.
   (`persistence._snapshot_source_view`,
   `tests/test_update_persistence_transactions.py`)

5. **Symlinks cannot escape.** Repository-relative symlink targets must
   resolve inside the repository, in both the workspace and any snapshot.
   (`persistence._validate_workspace_symlink`)

6. **Baseline restore precedes re-validation.** Withheld targets' declared
   paths, plus every written path not owned by a still-promoted target,
   return to the captured baseline before the next validation round.
   (`persistence.IsolatedUpdateWorkspace.validation_snapshot`)

7. **Promotion is the only live-checkout commit, and it is journaled.** SQLite
   checkpoints candidate bytes and DBOS checkpoints execution results. The
   filesystem journal guards the separate promotion crash window. Replay after
   a committed promotion accepts only the exact full candidate, and otherwise
   preserves external edits. The scratch tree is disposable.
   (`persistence`, `durable`, `tests/test_update_durable.py`)

## Validation gating

8. **A candidate is never promoted unvalidated.** Derivations declared by
   selected updaters evaluate on all declared systems; root closures build on
   the exact candidate that would be promoted, for every configured system,
   whenever any path changed. Cached validation is keyed by the exact source view
   materialized for that validation, not a prior or later mutable tree.
   (`derivation_validation`,
   `cli._validate_round`, `cli._requires_root_closure_validation`)

9. **Failure attribution is bounded, not silent.** Batched validations pass
   `--keep-going`; a failed batch is bisected, and if both halves fail the
   subdivision stops so a systemic failure adds at most two attempts per
   boundary. Eval-mode bisection is required by the evaluator: per-request
   failures are not attributable inside one batched eval because
   missing-attribute errors escape `builtins.tryEval`. That evaluator
   property is pinned by `tests/test_update_tryeval_contract.py`; if it
   changes, attribution can move into a single command.

10. **Withholding is coupled, not per-target.** A failing target withholds
    its dependency cluster so partial candidates are never promoted, bounded
    at three validation rounds; strict mode keeps the whole run atomic.
    (`cli._withhold_failed_clusters`, `cli._validate_and_gate`)

11. **Evaluations disable import-from-derivation** so candidate evaluation
    cannot execute downstream build code during probing.
    (`derivation_validation._validation_args`)

12. **Batch keys stay conservative.** Only explicit local attribute paths
    from one immutable snapshot batch together; anything else validates
    individually. (`derivation_validation._batch_key`)

## Lock and receipt consistency

13. **flake.lock writes are serialized and receipt-scoped.** Ref tasks
    serialize flake edits and receipt capture under one edit lock; a source
    task reuses a refresh only when the recorded receipt still equals the
    current declarations and reachable lock graph. Unknown fetcher fields,
    dynamic Nix, or unfamiliar lock semantics degrade to exact-file identity
    instead of a narrower receipt.
    (`refs.update_refs_task`, `input_state.flake_input_state`)

14. **A failed ref task publishes no receipt.** Receipts forward only from
    tasks that completed a verified refresh or remote check; both the
    "updated" and "no-change" outcomes record receipts, and errors never do.
    (`source_runner.run_ref_phase`,
    `tests/test_update_input_refresh.py::test_ref_result_failure_never_publishes_a_refresh_receipt`)

15. **Input refreshes finish before any source task starts.** All selected
    inputs resolve in one lock update; a failed resolution fails every source
    whose closure includes a pending input, while independent sources
    continue. (`source_runner._refresh_source_inputs`)

## Process boundaries

16. **Failures are confined to their target.** An updater failure of any
    exception class becomes that target's error event; cancellation is the
    only exception that propagates, and its cleanup is shielded from further
    stop requests. (`process.run_queue_task`, `runtime.await_cleanup`)

17. **Shared producers cannot outlive their consumers' workspace.** The
    source phase joins memoized producers before the session and disposable
    workspace close; failed producers are never retained for reuse.
    (`runtime.memoize`, `runtime.join_shared_work`)

18. **Diagnostics are redacted at every boundary.** Terminal output, run
    logs, and event payloads pass the same URL redaction; timings carry
    operation labels, never commands, URLs, or cache keys.
    (`diagnostics.redact_urls`, `runtime.OperationTiming`)

19. **Generation receipts bind their environment.** crate2nix receipt reuse
    requires the realized source, target options, platform, lock, generator
    and tool identities, and all output digests to match; only digests are
    persisted. (`generated_artifact_commands`,
    `generation_receipts`)

## Disposable builders and repair

20. **Preparation cannot authorize publication.** Native stages may temporarily
    carry incomplete platform hashes. They extend one pinned candidate serially;
    validation requires every configured preparation platform and checks the exact
    final tree. Certification requires one successful report from every native
    builder, with no duplicate or mismatched identities.
    (`candidate.Preparation`, `ci.candidate`, `tests/test_update_candidate.py`)

21. **Quality checks cannot inherit evidence after changing a candidate.** The
    publication job verifies the baseline, applied tree and post-check tree.
    Formatter or generator drift requires a new validation attempt.
    (`ci.jobs.certify`, `tests/test_update_ci.py`)

22. **Repair is bounded and has no validation authority.** One isolated agent
    proposal may change packaging and flake references. A repaired attempt starts
    fresh execution, passes the existing quality gates and builds, and cannot
    recursively repair itself. CI gives publication credentials only to the later
    publication step. Local repair retains the same atomic promotion boundary.
    (`repair`, `ci.jobs.start_repair`, `tests/test_update_repair.py`)
