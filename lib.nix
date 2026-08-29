# Public lib entrypoint. Keep the implementation in one place to avoid
# drift between the root export and the internal lib implementation.
args:
import ./lib/lib.nix (
  args
  // {
    # Keep the complete repository root and its store context. Coercing the
    # flakelight source accessor directly creates a second, unrealised path.
    src = builtins.path {
      name = "source";
      path = args.src;
    };
  }
)
