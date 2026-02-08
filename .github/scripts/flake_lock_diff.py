#!/usr/bin/env python3
"""Compatibility wrapper for tools.flake_lock_diff."""

from tools.flake_lock_diff import main

if __name__ == "__main__":
    raise SystemExit(main())
