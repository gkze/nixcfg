---
name: engineering-discipline
description: Apply the user-level Engineering Discipline to implementation, design, debugging, testing, performance work, refactoring, or review. Use when the user explicitly names this skill or asks to apply Engineering Discipline to the current task.
---

# Engineering Discipline

Use this workflow when the user explicitly names this skill or asks to apply
Engineering Discipline. A mention applies the standard to the current task;
it does not turn that task into a review or authorize additional actions.
Discovery or automatic selection by a host is not itself an explicit request.
The standing engineering standard still applies wherever required by user or
repository instructions.

Follow the requested mode: implement or repair when changes are authorized;
analyze, plan, or review read-only when that is the request. If only this skill
is mentioned, use the established task context. If no task or target exists,
ask what work to apply it to.

## Authority and source

1. Read `references/engineering-discipline.md` completely before working.
   It resolves to the single authoritative user-level standard.
2. Apply the authority order defined in that standard. An explicit task
   contract or repository instruction can narrow the applicable approach; it
   does not silently erase a relevant engineering risk.
3. Preserve the task's authorization boundary. Analysis, planning, and review
   are read-only unless changes are requested. For implementation, make only
   authorized changes; invoking this skill does not authorize external
   comments, issue changes, commits, pushes, or other additional actions.
4. If the standard cannot be read, do not reconstruct it from memory. Identify
   the missing source and the resulting limitation; for a review, use an
   `inconclusive` verdict.

## Resolve the task scope

1. Identify the requested outcome, target, and boundary. For a change review,
   establish the exact change set and comparison base; do not assume a branch
   or default base when it can be discovered.
2. When inspecting a working tree, include staged, unstaged, and untracked files.
   Record pre-existing changes and do not attribute them to the current task
   without evidence.
3. Read the applicable repository instructions, task or specification,
   implementation, tests, contract-bearing documentation, and generated-source
   authorities needed to understand the change.
4. State material exclusions and evidence gaps. Bound the work and its claims
   accordingly. For a review, use the `inconclusive` verdict when those gaps
   prevent a responsible conformance judgment.

## Working method

1. Understand the behavior before choosing or judging a change. Trace callers,
   data and trust boundaries, state transitions, side effects, failure paths, recovery,
   supported consumers, and generated projections far enough to identify the
   owning invariant.
2. Select the `ED-*` rules relevant to the affected risks. Mark a final-check
   item not applicable when appropriate; do not manufacture findings merely to
   mention every rule. When implementing or reviewing code, explicitly apply
   the factoring pass in `ED-2.8` and the evidence check in `ED-F.10`.
3. Challenge the design or implementation with concrete scenarios and likely
   counterexamples. A finding requires a reachable or contract-relevant
   violation or a concrete maintainability defect, not a stylistic preference
   or unsupported speculation. A factoring finding can establish demonstrable
   maintenance cost or duplicated semantic authority without an existing
   runtime failure. Support it with specific ownership, call-site, or
   capability evidence as required by `ED-2.8` and `ED-F.10`.
4. Do not introduce, recommend, or accept new backward-compatibility
   machinery, such as shims, aliases, legacy fallbacks, dual read/write paths,
   version-specific branches, or silent coercions, unless an explicit task
   requirement or supported contract requires it. A credible risk of material
   migration cost that cannot be bounded from available evidence is a narrow
   exception only when the uncertainty and likely impact were explicitly
   disclosed to the user before or when the compatibility path was introduced.
   Hypothetical consumers, generic caution, and disclosure made only after the
   fact are not sufficient. Cite `ED-2.1`, `ED-2.5`, or `ED-10.4` as applicable.
5. During implementation, resolve material defects within the authorized scope.
   During analysis or review, report them with evidence and repair directions.
   Run proportionate checks at the cheapest faithful layers that can falsify
   the material risks, keeping checks non-mutating for read-only tasks.
   Reinspect the workspace afterward and report any validation side effects.
6. For a large or high-risk change, use an independent fresh review when that
   capability is available. Give it the exact scope and canonical standard,
   then independently verify its findings before including them.
7. Recognize documented deviations required by a higher-authority contract.
   Record the deviation and its evidence separately from violations.

## Review findings

For review findings or unresolved defects, use these severities:

- **P0:** Immediate catastrophic harm, data loss, security compromise, or a
  system-wide blocker.
- **P1:** High-impact correctness, security, integrity, or compatibility defect
  that should block acceptance.
- **P2:** Material defect with a narrower impact or a credible future failure
  in supported behavior.
- **P3:** Low-impact but concrete maintainability, observability, or contract
  defect worth correcting.

Each finding must include:

- one or more violated `ED-*` rule IDs;
- the tightest available file and line, symbol, or document-section location;
- the concrete triggering scenario or counterexample;
- the resulting impact; and
- the smallest coherent repair direction.

Do not report a finding when evidence is insufficient. Put uncertainty and
unverified risk in limitations instead.

## Output

Match the response to the requested work. For implementation, describe the
result, relevant validation, remaining defects, and limitations. For design,
planning, or debugging, give the requested artifact or evidence-based
conclusions, including material tradeoffs and uncertainty. Cite relevant
`ED-*` rules when they explain a decision, defect, or deviation; do not impose
a formal review report on other tasks.

For a requested conformance review, report in this order:

1. **Reviewed scope:** target, comparison base, included change types, and
   material exclusions.
2. **Verdict:** exactly one of `conforms on reviewed scope`, `does not conform`,
   or `inconclusive`.
3. **Findings:** ordered P0 through P3. Start each with
   `[severity] [rule ID] location` and then give the scenario, impact, and
   repair. If there are none, say so explicitly.
4. **Documented deviations:** higher-authority exceptions and their evidence,
   or `none`.
5. **Validation and workspace effects:** checks run, meaningful results, and
   any files or state changed by validation.
6. **Residual risk and limitations:** untested boundaries, unavailable
   evidence, and uncertainty that remains.

Never claim universal or repository-wide compliance from a bounded review.
