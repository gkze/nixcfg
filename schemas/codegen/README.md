# Codegen manifest schemas

These JSON Schemas define a language-agnostic configuration format for
cross-language code generation.

Files:

- `codegen.schema.json` — human-authored manifest format
- `codegen-lock.schema.json` — machine-written lockfile format
- `LOCKFILE_SPEC.md` — deterministic cross-language lockfile generation rules

Current intended workflow:

1. author a manifest in YAML or JSON
1. validate it against `codegen.schema.json`
1. materialize a lockfile that conforms to `codegen-lock.schema.json`
1. generate language bindings for the schemas themselves

Current lockfile CLIs:

- Python reproducible default output:
  - `uv run python nixcfg.py schema lock path/to/codegen.yaml`
- Python include informational timestamps / provenance metadata:
  - `uv run python nixcfg.py schema lock path/to/codegen.yaml --include-metadata`
- Go reproducible default output:
  - `cd ghawfr && go run ./cmd/ghawfr-dev codegen lock ../path/to/codegen.yaml`
- Go include informational timestamps / provenance metadata:
  - `cd ghawfr && go run ./cmd/ghawfr-dev codegen lock ../path/to/codegen.yaml --include-metadata`

Current generated bindings in this repo:

- Python: `lib/schema_codegen/models/_generated.py`
- Go manifest: `ghawfr/devtools/codegenmanifest/types.gen.go`
- Go lockfile: `ghawfr/devtools/codegenlock/types.gen.go`

Shared cross-language golden fixtures live under:

- `schemas/codegen/testdata/`

The schema `$id` values currently use stable `urn:uuid:` identifiers rather
than hosted URLs, so they do not imply a public domain or publication contract.

The schemas are designed to stay language-neutral so the format can be reused if
`ghawfr` is later extracted into its own repository.
