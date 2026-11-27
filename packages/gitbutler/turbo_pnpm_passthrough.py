"""Keep turbo tasks on the Nix pnpm.

pnpm 12 reads ``packageManager`` as a request to download that release and
verify its npm registry signature. GitButler pins ``pnpm@12.5.1`` while the
Nix build runs ``pnpm.version`` (currently 12.9.0). The sandbox cannot reach
``registry.npmjs.org``, so that signature check fails closed with
``ERR_PNPM_PNPM_ENGINE_IDENTITY_UNVERIFIABLE``. The derivation sets
``manage-package-manager-versions`` false, which tells pnpm to keep the Nix
binary. The derivation still checks ``pnpm --version`` against
``pnpm.version``. turbo's ``package`` task does not forward the variable, so
the pnpm it spawns tries the download. Listing the variables in
``globalPassThroughEnv`` gives every task the same setting.
"""

from __future__ import annotations

import json
from pathlib import Path

PASSTHROUGH_ENV: tuple[str, ...] = (
    "npm_config_manage_package_manager_versions",
    "pnpm_config_manage_package_manager_versions",
)


def align_turbo_package_manager_env(turbo: dict[str, object]) -> None:
    """Record the Nix package-manager switch settings on a turbo config."""
    current = turbo.get("globalPassThroughEnv")
    if current is None:
        entries: list[object] = []
    elif isinstance(current, list):
        entries = list(current)
    else:
        message = "turbo.json globalPassThroughEnv must be a list of environment names"
        raise ValueError(message)
    for name in PASSTHROUGH_ENV:
        if name not in entries:
            entries.append(name)
    turbo["globalPassThroughEnv"] = entries


def main() -> None:
    """Rewrite ``turbo.json`` in the current directory."""
    path = Path("turbo.json")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        message = "turbo.json must be a JSON object"
        raise SystemExit(message)
    align_turbo_package_manager_env(parsed)
    path.write_text(json.dumps(parsed, indent="\t") + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()  # pragma: no cover -- script entrypoint
