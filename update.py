#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "aiohttp>=3.13.3",
#   "aiohttp-retry>=2.9.1",
#   "filelock>=3.20.3",
#   "lz4>=4.4.4",
#   "packaging>=25.0",
#   "pydantic>=2.12.5",
#   "pydantic-settings>=2.11.0",
#   "pyyaml>=6.0.2",
#   "rich>=14.3.1",
# ]
# ///

"""Entry-point script for the update CLI."""

from update.cli import main

if __name__ == "__main__":
    main()
