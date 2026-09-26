# System appearance

macOS uses automatic appearance. Applications with native system-theme support
select Catppuccin Latte for light and Frappé for dark where available.

`home/george/appearance.nix` bridges terminal tools without native paired-theme
settings using the `dark-mode-notify` launch agent. It initializes on login and
updates files in `$XDG_STATE_HOME/nixcfg/appearance` on macOS appearance events.
Files are replaced atomically and unchanged files are left alone.

- Alacritty imports the current palette and uses its configuration watcher.
- Helix uses the current configuration; the agent requests reload with SIGUSR1.
- git-delta includes the current palette on each invocation.
- ptpython uses a dynamic style, refreshed once per second while open.
- Starship reads the current Catppuccin palette on each new prompt, preserving
  the existing prompt layout. Already printed prompts keep their original colors.
- Superfile reads the current palette when launched; reopen existing instances.

Element's system mode selects its built-in light/dark themes. Both Catppuccin
palettes remain available as manual alternatives; Element does not pair custom
themes in system mode. Its device preference can override configuration defaults.

The inspected Wispr Flow version exposes no supported system-theme setting.
Do not write an unsupported `system` value to its light/dark theme preference.

Validation on 2026-09-26 covered actual macOS Light and Dark events, runtime files,
Git's effective palette, and ptpython's real DynamicStyle. macOS Auto was restored.
This was an appearance-only live apply, not a full nix-darwin activation. Element's
config default was updated, but its live UI could not be inspected because app
control timed out; an existing device override remains unverified.
