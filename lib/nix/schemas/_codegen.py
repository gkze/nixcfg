"""Compatibility module for declarative Nix schema model generation."""

from . import codegen_main as main

if __name__ == "__main__":  # pragma: no cover -- compatibility CLI entrypoint
    main()
