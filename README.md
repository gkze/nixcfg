# nixcfg: Nix configuration

Unified configuration for macOS and Linux systems from a single point of control

* **Darwin (macOS)**

  Currently being used actively on my personal MacBook Pro

* **Linux (NixOS)**
  
  In development, targeting [ThinkPad X1 Carbon (Gen 10)](https://psref.lenovo.com/Detail/ThinkPad/ThinkPad_X1_Carbon_Gen_10?M=21CB00F7US)

## Installation

* **Darwin (macOS)**

  Use the [Determinate Systems Nix Installer](https://github.com/DeterminateSystems/nix-installer)
  to get Nix 

* **Linux (nixOS)**

  WIP

## Usage

After changes are made:

```
nix run .#rebuild -- --flake . switch
```

This app will pick the right command to run depending on the host system.

## Roadmap

| Feature :arrow_down: / OS :arrow_right: | macOS | NixOS | Debian | Any Linux distribution |
|-----------------------------------------|-------|-------|--------|------------------------|
| Automatic setup                         | :x:   | :x:   | :x:    | :x:                    |
| Automatic backups                       | :x:   | :x:   | :x:    | :x:                    |
| Storage encryption                      | :x:   | :x:   | :x:    | :x:                    |
| Secret management                       | :x:   | :x:   | :x:    | :x:                    |

## License

[MIT](LICENSE)
