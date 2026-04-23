"""Pin the ghostty-web workspace dependency to the commit already locked in bun.lock."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_LOCKED_SPEC_RE = re.compile(
    r'"ghostty-web"\s*:\s*\[\s*"(?P<spec>ghostty-web@github:anomalyco/ghostty-web#[0-9a-f]+)"'
)


def _locked_ref(root: Path) -> str:
    match = _LOCKED_SPEC_RE.search((root / "bun.lock").read_text(encoding="utf-8"))
    if match is None:
        msg = "Could not find locked ghostty-web GitHub ref in bun.lock"
        raise RuntimeError(msg)
    return match.group("spec").split("#", maxsplit=1)[1]


def main(argv: list[str]) -> int:
    """Rewrite packages/app/package.json to the lockfile-pinned ghostty-web ref."""
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    package_json = root / "packages/app/package.json"

    locked_ref = _locked_ref(root)
    payload = json.loads(package_json.read_text(encoding="utf-8"))
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        msg = "packages/app/package.json is missing a dependencies mapping"
        raise TypeError(msg)

    spec = dependencies.get("ghostty-web")
    if not isinstance(spec, str):
        msg = "packages/app/package.json is missing the ghostty-web dependency"
        raise TypeError(msg)

    pinned_spec = f"github:anomalyco/ghostty-web#{locked_ref}"
    if spec == pinned_spec:
        return 0

    dependencies["ghostty-web"] = pinned_spec
    package_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
