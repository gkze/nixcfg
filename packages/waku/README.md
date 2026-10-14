# Waku source provenance and packaging policy

This package builds Waku from public GPL-3.0-only source. Vendor `.dmg` and
`.zip` artifacts are release evidence only. The package never uses those
artifacts as inputs.

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

The Git checkout and two independently downloaded GitHub archives produced the
same tree. The parsed root `Cargo.toml` of the commit declares `0.1.2`. The
`CHANGELOG.md` section of the commit matches the published
`https://releases.waku.sh/Waku-0.1.2.md` notes exactly.

These records give an evidence-backed relationship between release `0.1.2` and
the source commit. They are not a cryptographic source-to-binary attestation.
The old `v0.1.2` tag and the historical appcast item no longer exist on the
live release surfaces. The immutable commit, tree, and hash are the package
authority.

`updater.py` applies the stricter rule for future releases. The live appcast
identity, the immutable version tag commit, the parsed Cargo version, and the
published notes and source notes must all agree. If they do not agree,
resolution fails closed.

## Build and runtime policy

- `gpui_platform/runtime_shaders` avoids the GPUI build-time dependency on the
  optional proprietary Xcode Metal toolchain.
- The pinned Cargo workspace builds Waku, `waku_js_repl`, and `waku-daemon`.
  The build compiles the Computer Use helper from its pinned Swift source.
- [Sparkle](https://sparkle-project.org/) is not embedded. The package removes
  `SUFeedURL` and `SUPublicEDKey`, so the source updater finds no framework.
  Nix remains the only update owner.
- The helper keeps the Accessibility and Screen Recording usage strings and a
  deterministic `.waku-helper-fingerprint`. That fingerprint drives the stable
  per-user helper-copy refresh of Waku.
- The build ad-hoc signs the helper, the REPL, and the daemon before the outer
  app. The build claims no Developer ID, Team ID, hardened runtime,
  notarization, or vendor identity. Initial TCC prompts remain expected. Peer
  validation is weaker than the vendor-signed distribution, because an ad-hoc
  signature has no Team ID to compare.

## Validation boundary

The fixed-output Cargo vendor derivation ran through the expected fake-hash
mismatch. It reported `sha256-h4wFTHMgG62ztKlpgLdVX1D5K0EEjHEgZo/cNgvGUiA=`.
The repository pins that exact SRI in `sources.json`. That result proves
dependency acquisition only.

Do not represent the package as built or verified before the final Waku
derivation and the installed bundle checks complete.

## Related

- [Packaging](../../docs/packaging.md)
- [Source modifications](../../docs/source-modifications.md)
- [Development](../../docs/development.md)
- [Update pipeline](../../docs/update/README.md)
- [Update safety](../../docs/update/safety.md)
