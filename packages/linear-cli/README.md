# Linear CLI

The package installs the publisher's stable native archives for Darwin and
Linux. `updater.py` resolves GitHub release assets and generates the per-platform
URLs and hashes in `sources.json`. There is no separate flake source pin or
Deno runtime manifest.

The native archive was selected over a new local Rust build because upstream
publishes the exact supported target binaries, release checksums and bundled
third-party license notices. It avoids owning a new crate graph for this small
standalone CLI. Darwin keeps the publisher binary unchanged; Linux uses
`autoPatchelfHook` and the toolchain runtime for its documented ELF dependencies.

The upstream command is `linear`; `linear-cli` remains an alias for existing
Nix users. Both names have freshly generated Bash, Fish and Zsh completions.
The package's install checks verify both versions, help, an actual Bash
completion request, and Zsh's first autoload invocation. Completions are generated
after fixup so Linux can execute the patched binary. The Zsh site-functions also dispatch on their first autoload
call, adapting upstream's script for the standard completion directory.

## Version 3 compatibility

Upstream documents the migration in its bundled changelog. Existing credential
file formats, keyring service/account names and `.linear.toml` settings carry
over. No login or credential migration is part of installation.

Scripts should account for these intentional upstream changes:

- Version output is `linear 3.0.0` rather than a bare number.
- JSON lists are arrays, views are objects, and mutations return the entity.
- Exit codes distinguish usage (2), not found (3), authentication (4), and
  unavailable (5) from other failures (1).
- Use `--limit all` instead of `--limit 0`.
- Saved 2.x completion scripts must be regenerated; the former
  `completions complete` subcommand no longer exists.

The maintained Nix configuration selects the package but has no scripts that
parse its JSON or invoke the removed completion subcommand.
