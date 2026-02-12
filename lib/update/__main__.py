# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "aiohttp>=3.13.3",
#   "filelock>=3.20.3",
#   "pydantic>=2.12.5",
#   "rich>=14.3.1",
# ]
# ///

"""Run the update package as a module."""

from lib.update.cli import main

if __name__ == "__main__":
    main()
