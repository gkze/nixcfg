#!/usr/bin/env python3
"""Update VS Code Insiders version and hashes.

Fetches the latest version info from Microsoft's update API and updates
data/vscode-insiders.json with new version and SHA256 hashes for all platforms.
"""

import json
import urllib.request
from pathlib import Path

# When run via `nix run`, use current directory; otherwise use script location
if "nix/store" in str(Path(__file__)):
    DATA_FILE = Path.cwd() / "vscode.lock.json"
else:
    SCRIPT_DIR = Path(__file__).parent
    DATA_FILE = SCRIPT_DIR.parent / "vscode.lock.json"

# Map Nix platform names to Microsoft's API platform names
PLATFORMS = {
    "aarch64-darwin": "darwin-arm64",
    "aarch64-linux": "linux-arm64",
    "x86_64-darwin": "darwin",
    "x86_64-linux": "linux-x64",
}


def fetch_platform_info(api_platform: str) -> dict:
    """Fetch version info for a platform from Microsoft's update API."""
    url = (
        f"https://update.code.visualstudio.com/api/update/{api_platform}/insider/latest"
    )
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode())


def main():
    print("Fetching latest VS Code Insiders version info...")

    # Fetch version from first platform (they're all the same)
    first_info = fetch_platform_info(next(iter(PLATFORMS.values())))
    version = first_info["productVersion"]
    print(f"Latest version: {version}")

    # Build hashes dict for all platforms
    hashes = {}
    for nix_platform, api_platform in PLATFORMS.items():
        info = fetch_platform_info(api_platform)
        sha256 = info["sha256hash"]
        hashes[nix_platform] = f"sha256:{sha256}"
        print(f"  {nix_platform}: sha256:{sha256}")

    # Write updated data
    data = {"version": version, "hashes": hashes}
    DATA_FILE.write_text(json.dumps(data, indent=2) + "\n")

    print(f"\nUpdated {DATA_FILE}")
    print("Run: nh darwin switch --no-nom .")


if __name__ == "__main__":
    main()
