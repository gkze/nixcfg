# Public lib entrypoint. Keep the implementation in one place to avoid
# drift between the root export and the internal lib implementation.
args: import ./lib/lib.nix args
