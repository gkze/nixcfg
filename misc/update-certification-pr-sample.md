**[Workflow run](https://github.com/gkze/nixcfg/actions/runs/24889034410)**

**[Compare](https://github.com/gkze/nixcfg/compare/main...update_flake_lock_action)**

### Updated flake inputs

| Input | Source | From | To | Diff |
| --- | --- | --- | --- | --- |
| catppuccin | [catppuccin/nix](https://github.com/catppuccin/nix) | [ba73719](https://github.com/catppuccin/nix/commit/ba73719e673e7c2d89ac2f8df0bc0d48983e4907) | [f41cc1c](https://github.com/catppuccin/nix/commit/f41cc1cf13647e482b7317396f749840ef715e16) | [Diff](https://github.com/catppuccin/nix/compare/ba73719e673e7c2d89ac2f8df0bc0d48983e4907...f41cc1cf13647e482b7317396f749840ef715e16) |
| nixpkgs | [NixOS/nixpkgs](https://github.com/NixOS/nixpkgs) | [6201e20](https://github.com/NixOS/nixpkgs/commit/6201e203d09599479a3b3450ed24fa81537ebc4e) | [b12141e](https://github.com/NixOS/nixpkgs/commit/b12141ef619e0a9c1c84dc8c684040326f27cdcc) | [Diff](https://github.com/NixOS/nixpkgs/compare/6201e203d09599479a3b3450ed24fa81537ebc4e...b12141ef619e0a9c1c84dc8c684040326f27cdcc) |
| opencode | [gkze/opencode](https://github.com/gkze/opencode) | [2a857eb](https://github.com/gkze/opencode/commit/2a857eb14050436f9020d6f9b5b60320a4e0cd70) | [6742eef](https://github.com/gkze/opencode/commit/6742eefe43e7d90e8a8d6d4403d61c71ef971c8c) | [Diff](https://github.com/gkze/opencode/compare/2a857eb14050436f9020d6f9b5b60320a4e0cd70...6742eefe43e7d90e8a8d6d4403d61c71ef971c8c) |
| t3code | [pingdotgg/t3code](https://github.com/pingdotgg/t3code) | [b8305af](https://github.com/pingdotgg/t3code/commit/b8305afa29309e52045987caab91db9b7e481ac0) | [ada410b](https://github.com/pingdotgg/t3code/commit/ada410bccff144ce4cfed0e2c6e18974b045f968) | [Diff](https://github.com/pingdotgg/t3code/compare/b8305afa29309e52045987caab91db9b7e481ac0...ada410bccff144ce4cfed0e2c6e18974b045f968) |

### Per-package sources.json changes

<details>
<summary><a href="https://github.com/gkze/nixcfg/blob/update_flake_lock_action/overlays/opencode/sources.json"><code>overlays/opencode/sources.json</code></a></summary>

```diff
@ ["drvHash"]
- "z6jrrq3gr8gxwzlprnxdrkhihccx97qn"
@ ["hashes", 0, "hash"]
- "sha256-uWJBX190NR7aYNnmnXxkdANbANPULye8xWk0FyHfSO0="
+ "sha256-oR+jJ3Kvc5fpBTyut1UaVmVOJD+bRLJCY2iNzAdcpfM="
@ ["hashes", 1, "hash"]
- "sha256-k5yxAmMK0RdBo600Hvt5vRsl+RCoPwwaSOV0M39KCXQ="
+ "sha256-dCjnHnPqbwO5r+5sijR0UDXegHEhMYCAKGrMFOOmOxE="
@ ["hashes", 2, "hash"]
- "sha256-h+2uuiX47+xJ3m3mYMfyDVjwMhOjlcHJYXVOx7r1L+I="
+ "sha256-KxreXSlw7pFmUEpyPHPUTTPmuIQCV6SKqvRYG6mgvfQ="
```

</details>

<details>
<summary><a href="https://github.com/gkze/nixcfg/blob/update_flake_lock_action/packages/opencode-desktop/sources.json"><code>packages/opencode-desktop/sources.json</code></a></summary>

```diff
@ ["commit"]
- "2a857eb14050436f9020d6f9b5b60320a4e0cd70"
+ "6742eefe43e7d90e8a8d6d4403d61c71ef971c8c"
```

</details>

<details>
<summary><a href="https://github.com/gkze/nixcfg/blob/update_flake_lock_action/packages/t3code/sources.json"><code>packages/t3code/sources.json</code></a></summary>

```diff
@ ["hashes", 0, "hash"]
- "sha256-zIlR3YJHckUrv8ky0dB83xbBx05Vn3l0MND67L28NfE="
+ "sha256-+houprN8ySGS3sSt5yC48NxuoOIzCg2DzK92l55wFd4="
```

</details>

<!-- update-certification:start -->

## Certification

Latest certification: [workflow run](https://github.com/gkze/nixcfg/actions/runs/24899999999)
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
