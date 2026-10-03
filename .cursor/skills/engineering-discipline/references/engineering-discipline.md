# George's Engineering Discipline

Use this as the standing instruction set for code, architecture, technical design, debugging, testing, performance work, and refactoring. It includes product security and authorization as domain semantics. It excludes rules about whether an agent may perform an action, external-system operations, Git and release workflow, communications, and response formatting.

## Authority and scope

Use these layers in order when instructions differ:

1. **ED-A.1** Explicit task requirements and supported product contracts define the required outcome and scope.
2. **ED-A.2** Repository instructions and domain documentation define local constraints, established mechanics, and supported conventions.
3. **ED-A.3** The core principles below define the personal engineering quality bar where higher layers leave a choice.
4. **ED-A.4** Conditional defaults apply only when the higher layers do not select a different approach.
5. **ED-A.5** Report an unresolved conflict instead of silently discarding a core principle or repository contract.

A lower layer does not override a higher layer. This standard does not grant authority to mutate code, repositories, external systems, or production.

## North star

Build the simplest model that preserves known required semantics and supported contracts. Give each invariant a clear owner and enforce it at the boundaries that need it. Express correctness in types, schemas, data, state transitions, and behavior tests. Reuse suitable mechanics, isolate effects and independently varying concerns, design failure explicitly, and validate at the cheapest layer capable of falsifying the risk and at the closest faithful consumer boundary available in the validation environment.

## Engineering principles

### 1. Find the causal owner

- **ED-1.1** Establish evidence for the behavior and reproduce it when feasible.
- **ED-1.2** Trace far enough to identify the owning invariant, material downstream effects, the evidenced failure class, and remaining uncertainty.
- **ED-1.3** Make the smallest coherent change: address the affected owner, enforcement points, projections, failure behavior, and supported consumers without unrelated redesign. The smallest textual diff is not necessarily the smallest correct change.
- **ED-1.4** Do not disguise lifecycle or ownership bugs with longer timeouts, blind retries, manual reloads, broad exception handling, UI-only patches, or symptom cleanup.
- **ED-1.5** A workaround or mitigation must have explicit behavior, limits, and intended lifetime. Never represent it as a root-cause fix.

### 2. Minimize accidental complexity, preserve essential complexity

- **ED-2.1** Remove boilerplate, duplicated declarations, unnecessary wrappers, conversions, indirection, and machinery. Do not optimize for line count or code golf.
- **ED-2.2** Preserve meaningful distinctions in identity, ownership, lifecycle, state, failure, authorization, and public behavior. Audit large reductions for erased cases and overcompression.
- **ED-2.3** Optimize for local reasoning. A reader should find an invariant and its enforcement near its owner.
- **ED-2.4** **Choose implementation sources by required semantics and total ownership cost.** Establish the supported behavior, failure and lifecycle requirements, environment constraints, and expected lifetime before selecting a solution. Search proportionately across repository helpers, the language's standard library, runtime and platform facilities, existing dependencies, SDKs, generators, and third-party packages. Inspect suitable existing capabilities early; discovery order is not a mandatory ranking.

  Exclude choices that cannot preserve the required contracts. Compare plausible remaining choices by semantic fit, correctness and security assurance, clarity, stability, supported environments, licensing, and total ownership cost. Include implementation, integration and adaptation, validation, operational footprint, transitive dependencies, provenance, upgrades, and eventual migration or replacement. Custom code also incurs testing, documentation, expertise, and ongoing maintenance costs; copied or vendored code and forks retain ownership obligations. Package count, popularity, existing installation, and local line count do not establish suitability.

  Reuse an adequate capability when it keeps the model simpler and reduces total ownership cost. Add a dependency when its supported behavior and maintained expertise justify its integration and lifecycle costs. Implement locally when the behavior is bounded, understandable, verifiable, and cheaper to own without recreating subtle generic machinery. These choices can compose: reuse mechanics and implement the remaining domain policy. Adopt the smallest sufficient capability and integration surface; do not expand a bounded task into a framework, generalized library, or speculative platform. Introduce wrappers only for a boundary justified by ED-2.5.

  For security-sensitive algorithms or subtle standardized behavior, such as cryptography, structured formats, Unicode, or calendar and time-zone rules, prefer a suitable maintained implementation with relevant correctness evidence. A short implementation can conceal a large contract. Neither standard-library status nor package reputation proves adequacy: verify the required behavior in the supported version and environment. A custom substitute needs a concrete unmet requirement and proportionate expertise and validation; dependency avoidance alone is not justification. This does not prohibit bounded operations whose contracts do not require those broader semantics.

  Scale discovery, comparison, and recorded rationale to uncertainty, failure impact, expected lifetime, and reversal cost. Stop when the material requirements are covered and no unresolved risk warrants further investigation. For a material choice, record the required capability, selected source, strongest plausible alternative, decisive tradeoff, and relevant validation evidence or remaining uncertainty in an existing durable location. Routine use of an established capability needs no package survey or separate design record. Revisit the choice when requirements or evidence materially change; this principle alone does not justify replacing working code.
- **ED-2.5** Introduce an abstraction when it centralizes a rule that must stay consistent, isolates a volatile or effectful boundary, or serves multiple uses with the same semantics. Thin adapters are justified for versioning, lifecycle, test seams, observability, or error normalization. Do not abstract merely to move lines, rename an API, or speculate about future reuse.
- **ED-2.6** Own domain invariants and stable internal meaning; reuse generic mechanics. Owning an invariant means owning its policy and enforcement, not necessarily implementing its underlying algorithms. Keep provider vocabulary and translation at the interop seam unless the domain intentionally adopts them.
- **ED-2.7** Do not destructure by reflex. Preserve qualified access when the qualifier communicates ownership, provenance, or field relationships. Destructure trusted or validated objects only when repeated access is materially noisier and the resulting local names remain unambiguous.
- **ED-2.8** **Evaluate factoring explicitly.** Before choosing an implementation and again before declaring implementation or review complete, assess whether the affected code is optimally factored for its supported requirements. Examine the changed code and relevant callers, helpers, representations, tests, and build configuration. Do not assume the existing decomposition is appropriate.

  Look for duplicated semantic rules, reconstructed derived state, custom mechanics already supplied by suitable existing capabilities, unnecessary wrappers or conversions, misplaced responsibilities, and concerns that vary independently but are coupled together. For a representative supported change, trace which locations would need coordinated edits and whether that coordination has a necessary semantic reason.

  Where a material structural choice exists, compare the current structure with the simplest plausible alternative. Centralize rules that must remain consistent; separate independently varying concerns; preserve the owner of atomicity and resource lifecycle. Judge improvement by clearer ownership, local reasoning, and lower maintenance cost while preserving required behavior.

  During implementation, resolve material factoring defects within the authorized scope. During read-only reviews, report them. Complete the bounded pass when no concrete, material factoring defect remains, or identify the specific unresolved defect and constraint. Passing tests, reducing line counts, splitting files, or adding abstractions does not by itself establish good factoring.

### 3. Make invariants executable in types and schemas

- **ED-3.1** Make invalid and ambiguous states hard to represent with inference, generics, branded identities where useful, discriminated unions, exhaustive handling, and structured protocols.
- **ED-3.2** Avoid `any`, implicit widening, stringly state, scattered casts, and unrefined `unknown` in internal logic.
- **ED-3.3** Validate data at each trust transition that materially changes assurance. Decode once for that transition and do not repeatedly validate unchanged trusted values.
- **ED-3.4** Choose the appropriate canonical representation, such as a runtime schema, IDL, database schema, or static type, and verify projections for conformance.
- **ED-3.5** Localize unavoidable casts, conversions, and compatibility behavior at the interop seam.
- **ED-3.6** Handle malformed values, unknown extensions, and unsupported semantics under an explicit compatibility and harm policy. Never silently broaden capability, corrupt meaning, or degrade to permissive types.

### 4. Give each concept one semantic authority

- **ED-4.1** Give each concept a clear semantic owner or canonical specification. Enforce it at every boundary needed for integrity, safety, or useful diagnostics.
- **ED-4.2** Derive types, bindings, projections, repetitive source, checks, and contract-bearing documentation when generation reliably removes drift. Otherwise use conformance tests between representations.
- **ED-4.3** Treat repository-designated generated artifacts as derived. Change their authority or generator and regenerate them; reconcile any unavoidable emergency edit with the generator.
- **ED-4.4** DRY semantic authority, not coincidental syntax. Keep concepts separate when they vary independently or change for different reasons, even if their present shapes match.
- **ED-4.5** Use schemas, ASTs, typed intermediate representations, and parsers when syntax or structure affects meaning. Text operations are appropriate for genuinely textual, bounded contracts. Consume the minimum context required by the grammar, including suffixes or neighboring structure when they can change the result.
- **ED-4.6** Keep transformations at the representation level whose semantics they change. Do not mix AST changes with post-print text processing or test one language's semantics through another language's string representation when a native harness exists.

### 5. Prefer a data-oriented core with explicit effects

- **ED-5.1** Favor pure functions for deterministic decision logic, using declarative composition, typed tables, maps, sets, and explicit state transitions where they clarify the model.
- **ED-5.2** Organize around cohesive domain ownership and vertical modules with small semantic ports. Related polyglot sources may share a source directory; language alone does not require separate directories or packages. Preserve toolchain requirements and generated or vendored layout contracts.
- **ED-5.3** Keep effects explicit, but do not extract I/O from the transaction, stream, resource lifecycle, or workflow that owns atomicity, cancellation, backpressure, or temporal behavior.
- **ED-5.4** Use an effect, workflow, resource, or state-machine abstraction when the boundary owns lifecycle, cancellation, retry policy, concurrency, or durable orchestration that ordinary return types do not express adequately. Keep ordinary transformations and simple asynchronous calls direct.
- **ED-5.5** Use imperative, stateful, point-free, or compact code only where that form makes the behavior clearer, safer, or measurably better.

### 6. Model independent capabilities independently

- **ED-6.1** Separate concerns that can vary independently, such as configuration from credentials, values from routing, generic infrastructure from integrations, references from expansion, and acknowledgement from durable work.
- **ED-6.2** Reunify those axes in the aggregate or operation that owns cross-axis validation, atomicity, and lifecycle. Composability does not outrank transactional integrity.
- **ED-6.3** Keep storage identity separate from ownership, lifecycle, traversal, authorization, joins, cascades, and multi-model writes. Richer behavior must be explicit policy, not inferred from a convenient schema shape.
- **ED-6.4** Keep public APIs small, semantic, and composable. Make them deterministic where possible; expose or inject time, randomness, network outcomes, and other nondeterminism.
- **ED-6.5** Define invalid invocation, successful no-op, idempotency, partial success, and unsupported behavior explicitly.

### 7. Design failure, concurrency, and recovery as part of the API

- **ED-7.1** Enumerate failure points and define what each does to state. Make writes atomic or explicitly recoverable.
- **ED-7.2** Make retryable operations idempotent and safe after partial completion. Classify errors before retrying, bound attempts or elapsed time, respect cancellation, and use backoff or jitter where shared transient dependencies require it. Do not retry deterministic invalid input.
- **ED-7.3** Preserve the primary causal error and report cleanup state separately. Elevate cleanup failure when integrity, safety, or recovery state becomes unknown.
- **ED-7.4** Distinguish expected absence from corruption, missing dependencies, and internal failure.
- **ED-7.5** Choose failure behavior from the harm model. Fail closed when proceeding could grant capability, corrupt authoritative state, or make an unsafe mutation. For availability, read, ingestion, and forward-compatible paths, explicitly choose reject, quarantine, skip, preserve unknown data, or degrade, and make that choice observable.
- **ED-7.6** Scope locks, compare-and-swap, deduplication, ownership checks, rate limits, and concurrency bounds to the exact invariant they protect.
- **ED-7.7** Design long-lived work with stable identities, durable checkpoints, ordered writes, explicit resume rules, integrity checks, and specified rollback and reconciliation semantics.

### 8. Test semantics at the cheapest faithful layer

- **ED-8.1** Protect public behavior, contracts, invariants, failure semantics, and the failed contract demonstrated by each regression. Do not freeze incidental source spelling, authored layout, dependency versions, or implementation structure unless that is the contract.
- **ED-8.2** Select the cheapest layer capable of falsifying each material risk: pure behavior, parsed structure, boundary contracts, integration or real-runtime behavior, and end-to-end consumer journeys. This is a risk map, not a mandatory order.
- **ED-8.3** Keep each language's semantics in its native harness. Mocks do not prove provider or runtime compatibility; intentional overlap is appropriate when layers detect different failures.
- **ED-8.4** Seek risk-weighted coverage rather than 100 percent by reflex. Minimize redundant tests, not protection of important behavior.
- **ED-8.5** Exercise the closest faithful consumer boundary available in the validation environment whenever integration is the contract or the risk is high. Otherwise identify the omitted risk and why broader validation is unnecessary.
- **ED-8.6** Challenge corrections with likely counterexamples and rerun affected validation.

### 9. Measure and optimize causally

- **ED-9.1** Establish a reproducible baseline before claiming an optimization result. Urgent mitigation may precede a complete baseline, but its effect remains provisional until measured.
- **ED-9.2** Use controlled A/B comparisons, steady-state measurements, and component instrumentation. Bound or serialize concurrency when measurement perturbs the system.
- **ED-9.3** Record the representative workload, environment, sample count or variance, and acceptance threshold when they affect interpretation.
- **ED-9.4** Distinguish cold-start spikes from persistent cost, and component improvement from whole-system and end-to-end results.
- **ED-9.5** Do not infer system performance from a micro-result or claim percentages without a reproducible benchmark, defined denominator, and stated scope. Optimize the causal class, not only the first hot path.

### 10. Treat maintainability and observability as design properties

- **ED-10.1** Keep contract-bearing documentation, public examples, durable design decisions, diagrams used for reasoning, and generated artifacts synchronized with implemented semantics.
- **ED-10.2** Document purpose, rationale, contracts, invariants, and non-obvious behavior. Do not narrate syntax.
- **ED-10.3** Make observability bounded, structured, queryable, and safe. Keep stable filter fields separate from arbitrary diagnostic detail, and preserve redaction boundaries.
- **ED-10.4** Honor published version and support guarantees even when consumers are not observable. Compatibility changes require explicit criteria covering the support window, known and unknown consumer risk, stored data, mixed-version behavior, migration evidence, and rollback.
- **ED-10.5** Treat code size, abstraction count, coverage, and benchmark numbers as evidence, not quality objectives.

## Conditional engineering defaults

Apply these only in their stated context. They are subordinate to repository authority and are not reasons to add or replace dependencies by themselves.

- **ED-C.1** **Cross-model references:** Reference and write contracts use IDs by default. Read contracts may expose purpose-built projections for consistency, authorization, or latency. Expansion, traversal, ownership, lifecycle, joins, cascades, and multi-model writes require explicit policy.
- **ED-C.2** **Command-line interfaces:** Prefer small action-oriented commands and flags for orthogonal modes. Default to deterministic non-interactive behavior; add interactive flows only where the CLI's UX contract supports them. Distinguish no requested operation from an already-satisfied request, which is a successful no-op.
- **ED-C.3** **George-owned greenfield TypeScript:** When stack selection is in scope, no repository choice exists, and constraints permit, evaluate these independently: strict inference, `const` arrow functions, compact expression returns when readable, typed data tables, Zod at runtime trust boundaries, Vitest, Bun with Node-compatible APIs, and strict Oxfmt and Oxlint. Prefer Optique for suitable CLIs, XState for genuine state machines, and Effect only for boundaries that own orchestration, resources, retries, or durable workflows. Do not apply these tools ceremonially.
- **ED-C.4** **Nix or source-first repositories:** Choose maintained upstream derivations, source builds, or vendor binaries according to reproducibility, provenance, licensing, platform support, security, caching, customization, and maintenance constraints.
- **ED-C.5** **Implementation source:** When supported contracts and repository conventions leave a choice, and semantic fit and total ownership cost are comparable, prefer suitable standard-library or runtime facilities, or capabilities already supported by the repository. This is a tie-breaker, not a reason to force a poor fit, preserve a problematic dependency, or replace working code. Apply ED-2.4 when the tradeoffs are material.

## Final engineering check

Apply the questions relevant to the affected risks. Mark any material item that is not applicable.

1. **ED-F.1** What invariant owns the change, and are all necessary enforcement points addressed?
2. **ED-F.2** Does the design cover affected projections, failure behavior, and supported consumers without unrelated complexity?
3. **ED-F.3** Are invalid states constrained and trust transitions validated without redundant ceremony?
4. **ED-F.4** Does each concept have a clear owner without collapsing independent concerns or splitting cross-concern invariants?
5. **ED-F.5** Are effects, failure, retry, concurrency, cleanup, and recovery behavior explicit where relevant?
6. **ED-F.6** Can the selected tests falsify each material risk, including integration behavior when it is part of the contract?
7. **ED-F.7** Was the closest faithful consumer boundary exercised when required by the risk?
8. **ED-F.8** Where performance matters, are measurements causal, reproducible, and scoped?
9. **ED-F.9** Can a future reader locate the domain meaning, rationale, and enforcement without reconstructing the whole system?
10. **ED-F.10** Is the affected code optimally factored for its supported requirements and scope? What concrete ownership, call-site, or capability evidence supports that conclusion? Where a material alternative exists, why was it adopted or rejected? Identify any remaining material concern.
11. **ED-F.11** Where implementation-source selection is material, does the chosen combination of standard facilities, dependencies, and custom code cover the required semantics with justified total ownership cost? What evidence supports it over the strongest plausible alternative, and have both dependency and custom-code obligations been considered without expanding the task's scope?
