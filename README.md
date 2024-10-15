# nixcfg: Nix configuration

Unified configuration for macOS and Linux systems from a single point of control

- **Darwin (macOS)**

  - 2021 M1 MacBook Pro

- **Linux (NixOS)**

  - [ThinkPad X1 Carbon (Gen 10)](https://psref.lenovo.com/Detail/ThinkPad/ThinkPad_X1_Carbon_Gen_10?M=21CB00F7US)
  - [ThinkPad X1 Carbon (Gen 12)](https://psref.lenovo.com/Detail/ThinkPad_X1_Carbon_Gen_12?M=21KC009XUS)

## Installation

- **Darwin (macOS)**

  - Use the [Determinate Systems Nix Installer](https://github.com/DeterminateSystems/nix-installer) to install Nix on macOS
  - For the first run, use `nix -v run`

- **Linux (nixOS)**

  For an existing system, use `sudo nixos-rebuild switch --flake .` for the first run

## Usage

After changes are made:

```
nh os switch -a .
```

## Roadmap

| Feature :arrow_down: / OS :arrow_right: | macOS | NixOS | Debian | Any Linux distribution |
| --------------------------------------- | ----- | ----- | ------ | ---------------------- |
| Automatic setup                         | :x:   | :x:   | :x:    | :x:                    |
| Automatic backups                       | :x:   | :x:   | :x:    | :x:                    |
| Storage encryption                      | :x:   | :x:   | :x:    | :x:                    |
| Secret management                       | :x:   | :x:   | :x:    | :x:                    |

## License

[MIT](LICENSE)
