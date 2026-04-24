**[Workflow run](https://github.com/gkze/nixcfg/actions/runs/1234567890)**

**[Compare](https://github.com/gkze/nixcfg/compare/main...update_flake_lock_action)**

## flake.lock

- `opencode`: `2a857eb14050436f9020d6f9b5b60320a4e0cd70` -> `6742eefe43e7d90e8a8d6d4403d61c71ef971c8c`
- `t3code`: `b8305afa29309e52045987caab91db9b7e481ac0` -> `ada410bccff144ce4cfed0e2c6e18974b045f968`

### Per-package sources.json changes

<details>
<summary><a href="https://github.com/gkze/nixcfg/blob/update_flake_lock_action/packages/opencode-desktop/sources.json"><code>packages/opencode-desktop/sources.json</code></a></summary>

```diff
-  "commit": "2a857eb14050436f9020d6f9b5b60320a4e0cd70"
+  "commit": "6742eefe43e7d90e8a8d6d4403d61c71ef971c8c"
```
</details>

<!-- update-certification:start -->
## Certification

Latest certification: [workflow run](https://github.com/gkze/nixcfg/actions/runs/1234567999)
Updated: `2026-04-24 17:15 UTC`
Elapsed: `2h 14m`

Closures pushed to Cachix (`gkze`):
- `.#pkgs.aarch64-darwin.zed-editor-nightly`
- `.#pkgs.aarch64-darwin.opencode`
- `.#pkgs.aarch64-darwin.opencode-desktop`
- `.#pkgs.aarch64-darwin.codex`
- `.#pkgs.aarch64-darwin.gemini-cli`
- `.#pkgs.aarch64-darwin.element-desktop`
- `.#pkgs.aarch64-darwin.linear-cli`
- `.#pkgs.aarch64-darwin.neovide`
- `.#pkgs.aarch64-darwin.goose-cli`
- `.#pkgs.aarch64-darwin.superset`
- `.#pkgs.aarch64-darwin.scratch`
- `.#pkgs.aarch64-darwin.czkawka`
- `.#pkgs.aarch64-darwin.lumen`
- `.#pkgs.aarch64-darwin.mountpoint-s3`
- `.#pkgs.aarch64-darwin.mux`
- `.#pkgs.aarch64-darwin.rust-analyzer-unwrapped`
- Shared Darwin closure for `.#darwinConfigurations.argus.system` and `.#darwinConfigurations.rocinante.system` excluding 16 heavy package closures
- `.#darwinConfigurations.argus.system`
- `.#darwinConfigurations.rocinante.system`
- `.#pkgs.x86_64-linux.nixcfg`
<!-- update-certification:end -->
