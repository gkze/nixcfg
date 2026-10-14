# System appearance

macOS uses automatic appearance. An application with native system-theme
support selects [Catppuccin](https://github.com/catppuccin/catppuccin) Latte for
light mode and Frappé for dark mode, where the application offers those themes.

Read [architecture.md](architecture.md) for the Home Manager module layers.

## Appearance bridge

`home/george/appearance.nix` bridges terminal tools that have no native
paired-theme setting. It uses the `dark-mode-notify` launch agent. The agent
initializes on login and updates files in `$XDG_STATE_HOME/nixcfg/appearance` on
macOS appearance events. The bridge replaces a file atomically and leaves an
unchanged file alone.

| Tool      | Mechanism                                                              |
| --------- | ---------------------------------------------------------------------- |
| Alacritty | Imports the current palette and uses its configuration watcher         |
| Helix     | Uses the current configuration; the agent requests reload with SIGUSR1 |
| git-delta | Includes the current palette on each invocation                        |
| ptpython  | Uses a dynamic style, refreshed once per second while open             |
| Starship  | Reads the current Catppuccin palette on each new prompt                |
| Superfile | Reads the current palette when launched                                |

Starship preserves the existing prompt layout. An already printed prompt keeps
its original colors. For Superfile, reopen an existing instance to pick up a
palette change.

## Applications with their own theme logic

Element's system mode selects its built-in light and dark themes. Both
Catppuccin palettes remain available as manual alternatives. Element does not
pair custom themes in system mode. Its device preference can override
configuration defaults.

The inspected Wispr Flow version exposes no supported system-theme setting. Do
not write an unsupported `system` value to its light or dark theme preference.

## Validation history

Validation on 2026-09-26 covered actual macOS Light and Dark events, the runtime
files, Git's effective palette, and ptpython's real `DynamicStyle`. macOS Auto
was restored afterwards.

That validation was an appearance-only live apply. It was not a full nix-darwin
activation. The Element config default changed, but app control timed out during
validation, so the live UI stayed unchecked. An existing device override remains
unverified.

## Related

- [architecture.md](architecture.md): module layers and host entrypoints
- [development.md](development.md): checks and validation ladder
- [`home/george/appearance.nix`](../home/george/appearance.nix): the bridge module
