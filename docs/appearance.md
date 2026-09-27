# System appearance

macOS uses automatic appearance. Applications with native system-theme support
select Catppuccin Latte for light and Frappé for dark where available.

`home/george/appearance.nix` bridges terminal tools without native paired-theme
settings, or whose native switching does not track the live appearance, using
the `dark-mode-notify` launch agent. It initializes on login and
updates files in `$XDG_STATE_HOME/nixcfg/appearance` on macOS appearance events,
and a poll agent re-checks the live appearance every 30 seconds because manual
overrides such as the Control Center dark-mode toggle change the live
appearance without writing the persisted preference or raising an event.
Files are replaced atomically and unchanged files are left alone.

- Alacritty imports the current palette and uses its configuration watcher.
- bat re-reads its bridged config on every invocation. Its native `auto:system`
  mode tracks the persisted macOS preference, which does not follow live
  appearance events while Auto scheduling is active.
- Helix uses the current configuration; the agent requests reload with SIGUSR1.
- git-delta includes the current palette on each invocation.
- ptpython uses a dynamic style, refreshed once per second while open.
- Superfile reads the current palette when launched; reopen existing instances.

Starship retains its static Stylix palette and existing prompt layout; it does not
follow system appearance.

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
