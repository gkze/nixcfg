# nixcfg: Nix configuration

Unified configuration for macOS and Linux systems from a single point of control

- **Darwin (macOS)**

  - 2021 M1 Max MacBook Pro (16")
  - 2024 M4 Max MacBook Pro (16") (WIP)

- **Linux (NixOS)**

  - HP ZBook Firefly 14 G7 (WIP)

## Installation

- **Darwin (macOS)**

  - Use the [Determinate Systems Nix Installer](https://github.com/DeterminateSystems/nix-installer) to install Nix on macOS
  - For the first run, use `nix -v run`

- **Linux (nixOS)**

  For an existing system, use `sudo nixos-rebuild switch --flake .`

  (fresh install instruction wip)

## Usage

After changes are made:

```
nh (os|darwin) switch -a . # os for NixOS, darwin for macOS
```

## Development

- Enter the dev environment with `nix develop` (or `direnv allow` if you use direnv).
- Run `uv sync` once to create `.venv`; `.envrc` will auto-activate it when present.
- Repo CLI (update/CI helpers): `nix run .#nixcfg -- update --help` and `nix run .#nixcfg -- ci --help`.

## Roadmap

| Feature :arrow_down: / OS :arrow_right: | macOS | NixOS | Debian | Any Linux distribution |
| --------------------------------------- | ----- | ----- | ------ | ---------------------- |
| Automatic setup | :x: | :x: | :x: | :x: |
| Automatic backups | :x: | :x: | :x: | :x: |
| Storage encryption | :x: | :x: | :x: | :x: |
| Secret management | :x: | :x: | :x: | :x: |

## License

[MIT](LICENSE)
