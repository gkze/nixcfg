"""Own Mesh's ordered llama.cpp patch inventory and its offline application."""

import argparse
import os
import re
import subprocess
from pathlib import Path


def patch_queue(source_root: Path) -> list[Path]:
    """Return base patches followed by the exact generated family series."""
    directory = source_root / "third_party/llama.cpp/patches"
    if directory.is_symlink() or not directory.is_dir():
        message = "Mesh source has no regular llama.cpp patch directory"
        raise SystemExit(message)
    entries = sorted(directory.iterdir())
    generated = directory / "generated"
    base_entries = [path for path in entries if path != generated]
    if any(
        path.is_symlink()
        or not path.is_file()
        or (path.suffix != ".patch" and path.name != ".gitattributes")
        for path in base_entries
    ):
        message = "Mesh source patch inventory contains an unsupported entry"
        raise SystemExit(message)
    # Git attributes affect the vendor checkout, not the target patch queue.
    patches = [path for path in base_entries if path.suffix == ".patch"]
    if not patches:
        message = "Mesh source contains no llama.cpp patches"
        raise SystemExit(message)
    if generated in entries:
        if generated.is_symlink() or not generated.is_dir():
            message = "Mesh generated patch directory is not a regular directory"
            raise SystemExit(message)
        series = generated / "series"
        if series.is_symlink() or not series.is_file():
            message = "Mesh generated patch series is not a regular file"
            raise SystemExit(message)
        names = series.read_text(encoding="utf-8").splitlines()
        if not names or any(
            re.fullmatch(
                rf"{index:04d}-family-[a-z0-9.-]+(?:--[a-z0-9.-]+)*\.patch", name
            )
            is None
            for index, name in enumerate(names, start=1)
        ):
            message = "Mesh generated patch series has invalid ordering or names"
            raise SystemExit(message)
        selected = [generated / name for name in names]
        entries = set(generated.iterdir())
        metadata = generated / "series.json"
        if metadata in entries and metadata.is_file() and not metadata.is_symlink():
            entries.remove(metadata)
        if entries != {series, *selected} or any(
            path.is_symlink() or not path.is_file() for path in selected
        ):
            message = "Mesh generated patch series does not exactly cover its directory"
            raise SystemExit(message)
        patches.extend(selected)
    if any(path.stat().st_size == 0 for path in patches):
        message = "Mesh llama.cpp patch is empty"
        raise SystemExit(message)
    return patches


def apply_patches(source_root: Path, commit: str, destination: Path) -> None:
    """Apply the complete validated queue to a pristine, committed source tree."""
    pin = source_root / "third_party/llama.cpp/upstream.txt"
    if (
        pin.is_symlink()
        or not pin.is_file()
        or pin.read_text(encoding="utf-8") != f"{commit}\n"
    ):
        message = "Mesh llama.cpp upstream pin does not match the selected commit"
        raise SystemExit(message)
    patches = patch_queue(source_root)
    commands = [
        ["init", "--quiet", "."],
        ["config", "user.name", "Buzz Nix Build"],
        ["config", "user.email", "buzz-llama-cpp@nix-managed.invalid"],
        ["add", "--all", "--force"],
        ["commit", "--quiet", "--no-gpg-sign", "-m", "buzz-llama-cpp upstream base"],
    ]
    commands.extend(
        ["am", "--3way", "--committer-date-is-author-date", "--no-gpg-sign", str(path)]
        for path in patches
    )
    for command in commands:
        subprocess.run(  # noqa: S603 -- validated patch paths, no shell
            ["git", *command],  # noqa: S607 -- supplied by the Nix build
            cwd=destination,
            check=True,
            env=os.environ
            | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        )


def main() -> None:
    """Run the Nix patch phase without shell orchestration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("commit")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    apply_patches(args.source, args.commit, args.destination)


if __name__ == "__main__":  # pragma: no cover -- Nix build entry point
    main()
