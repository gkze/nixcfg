# Deterministic codegen lockfile specification

This document defines the intended cross-language generation semantics for
`version: 1` lockfiles that conform to `codegen-lock.schema.json`.

The goal is that independent implementations in Python, Go, or other languages
can materialize the same lockfile from the same manifest and source contents
without relying on a hosted schema registry.

## Scope

This specification covers:

- schema validation inputs
- canonical lockfile serialization
- path normalization
- hashing rules
- the distinction between semantic and informational fields

It does **not** yet standardize every manifest evaluation rule. In particular,
feature growth in manifest loading, source discovery, and generator execution is
expected to continue separately from the lockfile wire format.

## Schema identity and validation

Implementations should validate against the checked-in local schema files:

- `schemas/codegen/codegen.schema.json`
- `schemas/codegen/codegen-lock.schema.json`

The schema `$id` values are stable `urn:uuid:` identifiers. They identify the
schema documents, but they are not fetch URLs and do not require network
resolution.

## Recommended file names and placement

The recommended v1 layout is:

- manifest: `codegen.yaml` or `codegen.json`
- lockfile: `codegen.lock.json`
- both files stored in the same directory

Other layouts may be supported by implementations, but the normalization rules
below still apply.

## Canonical output format

Lockfiles are machine-written **JSON** documents.

For byte-for-byte stable output across languages, writers should:

1. serialize the lockfile as UTF-8 JSON using RFC 8785 JCS (JSON
   Canonicalization Scheme)
1. append a single trailing `\n`
1. omit any UTF-8 BOM

This gives a shared cross-language normalization target without requiring one
language runtime's pretty-printer conventions to match another's.

## Path normalization

### General rules

All path-like fields stored in the lockfile use POSIX conventions:

- separator is `/`
- no leading `./`
- redundant separators are collapsed
- `.` path segments are removed
- paths must not escape their logical base via unresolved `..`

### Field-specific bases

- `manifest_path`
  - normalized relative path from the lockfile directory to the manifest file
- `LockedDirectorySource.path`
  - normalized relative path from the manifest directory to the source root on
    disk
- `LockedGitHubRawSource.path`
  - normalized repository-relative POSIX path inside the upstream repository

## Semantic vs informational fields

### Semantic fields

These fields participate in the actual locked meaning of the file and should be
used for equality/change detection:

- top level: `version`, `manifest_path`, `sources`
- directory sources: `kind`, `path`, `content_sha256`
- URL sources: `kind`, `uri`, `sha256`
- GitHub raw sources: `kind`, `owner`, `repo`, `ref`, `path`, `uri`, `sha256`

### Informational fields

These fields are metadata for debugging, provenance, or observability and should
**not** affect semantic equality:

- top level: `generated_at`
- directory sources: `generated_at`
- URL sources: `fetched_at`, `etag`, `last_modified`
- GitHub raw sources: `fetched_at`, `tag`, `package`, `package_version`

Writers should omit informational fields by default in reproducible mode.
Consumers should ignore informational-field differences when deciding whether
locked content changed.

## Hashing rules

### Scalar fetched content

For `LockedUrlSource.sha256` and `LockedGitHubRawSource.sha256`:

- hash the exact fetched response body bytes
- encode the digest as lowercase hexadecimal SHA-256
- do not hash decoded text, transformed JSON, or normalized line endings

### Directory content hash

`LockedDirectorySource.content_sha256` is optional, but when present it should be
computed deterministically from the materialized file set.

Algorithm:

1. materialize the directory source's matched regular files
1. compute each file's normalized relative POSIX path from the source root
1. compute each file's lowercase hex SHA-256 over its raw bytes
1. sort files lexicographically by normalized relative path
1. build UTF-8 records of the form:
   - `<path>\0<file-sha256>\n`
1. concatenate those records in sorted order
1. SHA-256 the concatenated bytes and encode as lowercase hex

Notes:

- symlinks, device files, sockets, and other non-regular files are out of scope
  for v1 and should cause the hash step to fail rather than silently diverge
- empty materialized file sets hash as the SHA-256 of the empty byte string

## Directory source matching

For deterministic hashing, directory source matching should use POSIX-style glob
semantics relative to the source root:

- matching is case-sensitive
- `*` does not cross `/`
- `**` may cross `/`
- matched paths are normalized before sorting and hashing

## URL source rules

For locked URL sources:

- `uri` is the canonical configured source URI, not a transient redirect target
- `sha256` is over the fetched response body bytes for that source
- `etag` and `last_modified` are optional response metadata only

## GitHub raw source rules

For locked GitHub raw sources:

- `ref` should be the immutable commit SHA actually fetched
- `uri` should be the canonical raw-content URL derived from
  `owner` / `repo` / resolved `ref` / `path`
- `tag` may preserve the original human-facing tag name when a tag resolved to
  the stored commit SHA
- `package` and `package_version` are provenance fields only

## Reproducible mode

Reproducible mode should be the default behavior for lockfile writers.

In reproducible mode, implementations should:

- omit `generated_at`
- omit `fetched_at`
- omit `etag`
- omit `last_modified`
- omit GitHub provenance fields like `tag`, `package`, and `package_version`
- omit any other non-semantic metadata unless explicitly requested
- emit canonical JSON as described above

A non-reproducible "annotated" mode can still exist for debugging or audit
workflows, but it should be opt-in.

## Conformance testing

The practical compatibility test for multiple implementations is:

1. load the same manifest
1. resolve the same source contents
1. materialize the lockfile object
1. serialize with the canonical rules above
1. compare the resulting bytes

If byte-for-byte equality is too strict during development, compare parsed JSON
after stripping informational fields, then tighten to canonical bytes once both
implementations support the same serializer.
