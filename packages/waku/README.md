# Waku source provenance and packaging policy

This package builds Waku from public GPL-3.0-only source. Vendor `.dmg` and
`.zip` artifacts are release evidence only and are never package inputs.

## Pinned 0.1.2 identity

- Git commit: `fb820a6217072e51549b7d5da3dcc7ee473de085`
- Git tree: `aae07041b829fdfe7c4d2fe0b2ba66f2c392e421`
- Commit timestamp: `2026-08-16T22:17:58+08:00`
- Commit signature status: unsigned
- Extracted-tree NAR hash:
  `sha256-CaMAKRCzdMCq/cVxMRL5ApUVC1WYuFHxBaWnYa6tujk=`
- GitHub archive observed size: `7,979,923` bytes
- GitHub archive SHA-256:
  `c3f2b5592168bdb639b7161d0d917f8ee79281fa4f1eed17c8d0e50cf8fde9cf`

The Git checkout and two independently downloaded GitHub archives produced
the same tree. The commit's parsed root `Cargo.toml` declares `0.1.2`. Its
`CHANGELOG.md` section exactly matches the published
`https://releases.waku.sh/Waku-0.1.2.md` notes.

This establishes an evidence-backed relationship between release `0.1.2` and
the source commit, not a cryptographic source-to-binary attestation. The old
`v0.1.2` tag and historical appcast item no longer survive on the live release
surfaces, so the immutable commit/tree/hash are the package authority.
`updater.py` applies the stricter rule available for future releases: the live
appcast identity, immutable version tag commit, parsed Cargo version, and
published/source notes must all agree or resolution fails closed.

## Build and runtime policy

- `gpui_platform/runtime_shaders` avoids GPUI's build-time dependency on
  Xcode's optional proprietary Metal toolchain.
- Waku, `waku_js_repl`, and `waku-daemon` are built from the pinned Cargo
  workspace. The Computer Use helper is compiled from its pinned Swift source.
- Sparkle is not embedded. `SUFeedURL` and `SUPublicEDKey` are removed, so the
  source updater finds no framework and Nix remains the only update owner.
- The helper retains the Accessibility and Screen Recording usage strings and
  a deterministic `.waku-helper-fingerprint`, which drives Waku's stable
  per-user helper-copy refresh.
- The helper, REPL, and daemon are ad-hoc signed before the outer app. No
  Developer ID, Team ID, hardened-runtime, notarization, or vendor identity is
  claimed. Initial TCC prompts remain expected, and peer validation is weaker
  than the vendor-signed distribution because an ad-hoc signature has no Team
  ID to compare.

## Validation boundary

The fixed-output Cargo vendor derivation was materialized through the expected
fake-hash mismatch and reported
`sha256-h4wFTHMgG62ztKlpgLdVX1D5K0EEjHEgZo/cNgvGUiA=`. That exact SRI is pinned
in `sources.json`. This proves dependency acquisition, not the application
build: do not represent the package as built or verified until the final Waku
derivation and installed bundle checks complete.
