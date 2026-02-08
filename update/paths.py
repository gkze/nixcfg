import os
from pathlib import Path


def _resolve_repo_root() -> Path:
    env_root = os.environ.get("REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    script_path = Path(__file__).resolve()
    if "/nix/store" not in str(script_path):
        return script_path.parents[1]

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "flake.nix").exists() or (candidate / "sources.json").exists():
            return candidate
    return cwd


def get_repo_file(filename: str) -> Path:
    return _resolve_repo_root() / filename


SOURCES_FILE = get_repo_file("sources.json")
FLAKE_LOCK_FILE = get_repo_file("flake.lock")
