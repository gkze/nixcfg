# ghawfr

`ghawfr` is an incubating Go project for a local GitHub Actions workflow
runner and scheduler with pluggable execution backends.

Current goals:

- read workflow YAML
- plan and schedule the job graph locally
- dispatch jobs to backend workers/VMs
- manage file-backed run state and artifacts
- stay extractable into its own OSS repository

## Layout

Runtime-oriented packages live under public package paths like:

- `workflow`
- `planner`
- `controller`
- `backend`
- `artifacts`
- `cache`
- `state`

Maintainer-only tooling lives separately under:

- `cmd/ghawfr-dev`

Guest-side runtime entrypoints live under:

- `cmd/ghawfr-worker`
- `devtools/workflowschema`
- `devtools/codegenmanifest`
- `devtools/codegenlock`
- `devtools/codegenlockfile`

## Runtime implementation direction

The runtime is intentionally reusing third-party libraries where that is the
clear win:

- `github.com/rhysd/actionlint` is the parser frontend for workflow YAML and
  GitHub Actions expressions, pinned at an exact version because its library
  API does not promise semver stability
- `gonum.org/v1/gonum/graph/topo` provides DAG ordering and cycle detection for
  the pure planner
- `github.com/nektos/act/pkg/exprparser` is reused for GitHub Actions
  expression evaluation during matrix expansion
- `nektos/act` is still treated as a reference for planning/execution shape,
  not as the runtime chassis

The current runtime CLI is intentionally tiny:

```bash
go run ./cmd/ghawfr inspect .github/workflows/ci.yml
go run ./cmd/ghawfr plan .github/workflows/ci.yml
go run ./cmd/ghawfr route .github/workflows/update.yml crate2nix-darwin
go run ./cmd/ghawfr prepare .github/workflows/update.yml 'compute-hashes[platform=x86_64-linux,runner=ubuntu-24.04]'
go run ./cmd/ghawfr start .github/workflows/update.yml 'compute-hashes[platform=x86_64-linux,runner=ubuntu-24.04]'
go run ./cmd/ghawfr probe .github/workflows/update.yml 'compute-hashes[platform=x86_64-linux,runner=ubuntu-24.04]' 'uname -a'
go run ./cmd/ghawfr stop .github/workflows/update.yml 'compute-hashes[platform=x86_64-linux,runner=ubuntu-24.04]'
go run ./cmd/ghawfr run .github/workflows/ci.yml 'quality[name=ghawfr-go]'
```

Runtime provider selection is currently environment-driven, and `route` shows
what provider/image/transport path a job would take before execution:

```bash
GHAWFR_PROVIDER=auto go run ./cmd/ghawfr run .github/workflows/update.yml crate2nix-darwin
GHAWFR_PROVIDER=smoke-local go run ./cmd/ghawfr run .github/workflows/update.yml resolve-versions
GHAWFR_PROVIDER=auto GHAWFR_UNSAFE_LOCAL_FALLBACK=1 \
  go run ./cmd/ghawfr run .github/workflows/update.yml 'compute-hashes[platform=x86_64-linux,runner=ubuntu-24.04]'
```

Modes:

- `auto` — keep GitHub-hosted labels on the isolated-provider path by default
  (`vz` for macOS / Apple-Silicon Linux, `qemu` for x86_64 Linux); only use the
  host-local worker for explicit `local` jobs or unlabeled execution
- `local` — force the host-scoped local worker only
- `smoke-local` — force the broad smoke-test local worker, ignoring isolation
  goals
- `GHAWFR_UNSAFE_LOCAL_FALLBACK=1` — in `auto` mode, allow the broad
  smoke-local worker as a last resort

Useful current QEMU-specific overrides:

- `GHAWFR_WORKER_REMOTE_COMMAND=/path/to/ghawfr-worker-wrapper` — override the
  guest worker command used by the SSH bootstrap path
- `GHAWFR_QEMU_GUEST_WORKSPACE=/some/path` — override the planned guest
  workspace mount target (primarily useful for local/fake smoke testing; the
  default remains `/workspace`)

Current provider planning, preparation, and first boot/worker control scaffolding now produce concrete worker plans with:

- instance directories under `.ghawfr/workers/<provider>/...`
- generic `plan.json` and `host-checks.json` artifacts
- provider-specific launch artifacts such as `qemu-launch.json`, `launch.sh`,
  `fetch-base-image.sh`, `prepare-runtime-disk.sh`, `build-ghawfr-worker.sh`,
  a materialized guest `ghawfr-worker` binary, `cloud-init/*`, generated SSH
  keys and guest SSH helper scripts, persisted QEMU process state, and
  `vz-machine.json`
- guest workspace mount targets such as `/workspace`
- expected control transports (`host`, `ssh`, `vsock`)
- a first ghawfr-owned guest-worker protocol (`ghawfr-worker-v1`) over stdio,
  currently bootstrapped through SSH for QEMU guests
- host prerequisite checks for cheaply verifiable tools like `qemu-system-*`
  and `qemu-img`

The current guest-worker split is:

- `ghawfr` — host-side controller / planner / VM orchestration CLI
- `ghawfr-worker` — guest-side worker process serving the ghawfr worker
  protocol over stdin/stdout

For the current QEMU path, `probe` launches `ghawfr-worker serve --stdio`
through the generated SSH helper and then speaks the ghawfr worker protocol
instead of running ad-hoc step commands directly over SSH.

The `run` path now uses the same provider/lease boundary, so a selected Linux
job routed to QEMU executes its `run:` steps through the guest worker protocol
rather than only through the standalone `probe` command.

By default, the QEMU materialization path now also writes and attempts to build
an architecture-matched guest `ghawfr-worker` binary into the shared instance
workspace, so the SSH bootstrap path no longer has to assume that
`ghawfr-worker` is preinstalled in the guest PATH. `GHAWFR_WORKER_REMOTE_COMMAND`
remains available as an override seam for smoke tests and alternate bootstrap
strategies.

Curated `uses:` behavior now lives in the separate `actionadapter` package
instead of the generic `backend` package. Those adapters let setup actions such
as `determinate-nix-action`, `cachix-action`, `setup-python`, and `setup-uv`
validate the actual guest environment instead of accidentally consulting the
controller host. `cachix-action` propagates resolved `CACHIX_NAME` /
`CACHIX_AUTH_TOKEN` into later step environment. `setup-python` now verifies
requested `python-version` values against the discovered interpreter, supports
multiline version fallback matching, honors `update-environment: false`, and
emits a useful subset of upstream-style outputs such as `python-version`,
`python-path`, and `cache-hit=false`. `setup-uv` now emits `uv-version`,
`uv-path`, optional `uvx-path`, `python-version`, cache hit booleans, rejects
unsupported version-selection inputs instead of silently ignoring them, and when
`activate-environment: true` is requested it runs `uv venv --clear` to create a
real workspace-local environment, exports `VIRTUAL_ENV`, prepends the venv `bin`
directory ahead of the tool-cache path entries, and sets the `venv` output.
Both setup actions materialize runner-style locations under
`.ghawfr/runner/tool-cache`, prepend those tool-cache `bin` paths to later-step
`PATH`, and export `pythonLocation` / `UV_CACHE_DIR` accordingly. `actions/cache`
path creation now runs on the guest
side too, so cache directories line up with where later guest-executed steps
actually run. Remote workers now get a deterministic shared `HOME` under
`${{ github.workspace }}/.ghawfr/runner/home`, which makes home-relative cache
paths such as `~/.cache/nix` persistable as long as `HOME` is not overridden to
an unshared guest location.
Host-managed actions like `checkout`, `upload-artifact`, and
`download-artifact` translate `${{ github.workspace }}`-style guest paths back
onto the shared host workspace before touching files. The runtime now also has a
first generic post-step hook, which is used by `actions/cache` to model the
combined restore-on-main / save-on-post shape instead of treating cache steps as
pure directory creation. A file-backed `cache` package now stores named caches
under `.ghawfr/cache`, supports exact-key restore plus ordered restore-key
prefix fallback, supports `lookup-only` and `fail-on-cache-miss`, and now also
backs split `actions/cache/restore` and `actions/cache/save` adapters with
`cache-primary-key` / `cache-matched-key` outputs on restore-only steps. Runner
expression context is also worker-scoped, so conditions and interpolations like
`runner.os` / `runner.arch` resolve against the actual host or guest execution
target instead of the controller process. Each worker materializes
`.ghawfr/runner/temp` and `.ghawfr/runner/tool-cache` and exposes them
consistently as `runner.temp`, `runner.tool_cache`, `RUNNER_TEMP`,
`RUNNER_TOOL_CACHE`, and `AGENT_TOOLSDIRECTORY`.

At the moment, `ghawfr` already:

- parses workflows through `actionlint`
- expands static and expression-driven job matrices into executable job instances
- supports matrix evaluation from literal expressions and supplied
  `vars`/`inputs`/`needs` contexts
- defers late-bound jobs when required expression context is not available yet
- rewrites logical `needs` edges onto expanded job instances
- builds a pure DAG/stage plan through `gonum`
- can re-materialize and diff snapshots as upstream job outputs become available
- persists run state and derives logical `needs` context from completed jobs
- executes `run:` steps locally, including `GITHUB_OUTPUT` capture and job outputs
- provides a file-backed cache store and a first generic post-step hook for
  post-run action behavior such as `actions/cache`
- supports a first smoke-test action adapter layer in `actionadapter` for
  common CI setup actions such as `actions/checkout`,
  `DeterminateSystems/determinate-nix-action`, `cachix/cachix-action`,
  `actions/cache`, `actions/cache/restore`, and `actions/cache/save`
- can target and execute one selected materialized job from a real workflow file

## Cross-language codegen manifest schemas

The repository now also carries canonical JSON Schemas for a language-agnostic
code generation manifest and its lockfile under `schemas/codegen/`.

Generated bindings currently live in:

- Python: `lib/schema_codegen/models/_generated.py`
- Go manifest: `ghawfr/devtools/codegenmanifest/types.gen.go`
- Go lockfile: `ghawfr/devtools/codegenlock/types.gen.go`

The current Go lockfile materializer lives in:

- `ghawfr/devtools/codegenlockfile`

Manual command:

```bash
go run ./cmd/ghawfr-dev codegen lock ../path/to/codegen.yaml --output /tmp/codegen.lock.json
```

This keeps the config contract neutral enough to move with `ghawfr` later if
that project is split into its own repository.

## Workflow schema maintainer tooling

`ghawfr-dev` treats the official
`actions/languageservices/workflow-parser/src/workflow-v1.0.json` DSL as the
only machine-readable workflow syntax source.

Artifacts live under `workflow/schema/`:

- `official/workflow-v1.0.json` — checked-in workflow schema snapshot
- `manifest.json` — fetch/provenance metadata for the snapshot
- `.cache/docs/` — cached GitHub docs snapshots used for schema review

Commands:

```bash
go run ./cmd/ghawfr-dev schema fetch
go run ./cmd/ghawfr-dev schema fetch --docs
go run ./cmd/ghawfr-dev schema inspect
go run ./cmd/ghawfr-dev schema update
```

`fetch --docs` refreshes the official schema snapshot and the curated docs cache
in one step. `update` is the same full refresh with a shorter command name.
