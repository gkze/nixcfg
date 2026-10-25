"""Assemble a macOS ``.icns`` bundle from channel artwork.

Upstream stages its macOS app icon with ``sips`` and ``iconutil``
(``scripts/build-desktop-artifact.ts``). Those tools only exist on macOS hosts
with Xcode installed, so the Nix package scales the channel artwork with
ImageMagick and assembles the container here instead. The container is a plain
concatenation of typed PNG chunks, which keeps the result byte-identical for
identical inputs.
"""

import argparse
import struct
import subprocess  # ImageMagick is a declared build input.
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

ICNS_MAGIC: Final = b"icns"
MAGICK: Final = "magick"
_PNG_MAGIC: Final = b"\x89PNG\r\n\x1a\n"
_PNG_HEADER_SIZE: Final = 33
_CHUNK_HEADER_SIZE: Final = 8
# ICNS and PNG chunks both identify their type with a four-character code.
_CHUNK_TYPE_LENGTH: Final = 4

# ``.iconset`` file name -> (iconset pixel size, ICNS chunk type).
ICONSET_ENTRIES: Final[dict[str, tuple[int, str]]] = {
    "icon_16x16.png": (16, "icp4"),
    "icon_16x16@2x.png": (32, "ic11"),
    "icon_32x32.png": (32, "icp5"),
    "icon_32x32@2x.png": (64, "ic12"),
    "icon_128x128.png": (128, "ic07"),
    "icon_128x128@2x.png": (256, "ic13"),
    "icon_256x256.png": (256, "ic08"),
    "icon_256x256@2x.png": (512, "ic14"),
    "icon_512x512.png": (512, "ic09"),
    "icon_512x512@2x.png": (1024, "ic10"),
}


class IconsError(RuntimeError):
    """User-facing icon staging error."""


def _png_size(payload: bytes, *, name: str) -> int:
    """Return the square pixel size declared in a PNG ``IHDR`` chunk."""
    if not payload.startswith(_PNG_MAGIC) or len(payload) < _PNG_HEADER_SIZE:
        msg = f"{name} is not a PNG file"
        raise IconsError(msg)
    if payload[12:16] != b"IHDR":
        msg = f"{name} is missing its leading IHDR chunk"
        raise IconsError(msg)
    width, height = struct.unpack(">II", payload[16:24])
    if width != height:
        msg = f"{name} is not square ({width}x{height})"
        raise IconsError(msg)
    return int(width)


def scale_iconset(
    png: Path,
    iconset: Path,
    *,
    run: Callable[..., object] = subprocess.run,
) -> None:
    """Write every required iconset PNG for *png* into *iconset*.

    Upstream's artwork is 16-bit per channel; ImageMagick is the tool that
    reliably converts it to the 8-bit PNGs an ``.icns`` container must hold.
    """
    iconset.mkdir(parents=True, exist_ok=True)
    for name, (size, _chunk_type) in ICONSET_ENTRIES.items():
        run(
            [
                MAGICK,
                str(png),
                "-background",
                "none",
                "-resize",
                f"{size}x{size}",
                "-depth",
                "8",
                f"PNG32:{iconset / name}",
            ],
            check=True,
        )


def collect_chunks(iconset: Path) -> list[tuple[str, bytes]]:
    """Read every required ``.iconset`` entry and validate its pixel size."""
    chunks: list[tuple[str, bytes]] = []
    missing = [name for name in ICONSET_ENTRIES if not (iconset / name).is_file()]
    if missing:
        msg = f"iconset {iconset} is missing {', '.join(sorted(missing))}"
        raise IconsError(msg)
    for name, (expected_size, chunk_type) in ICONSET_ENTRIES.items():
        payload = (iconset / name).read_bytes()
        size = _png_size(payload, name=name)
        if size != expected_size:
            msg = f"{name} is {size}x{size}, expected {expected_size}x{expected_size}"
            raise IconsError(msg)
        chunks.append((chunk_type, payload))
    return chunks


def build_icns(chunks: Sequence[tuple[str, bytes]]) -> bytes:
    """Render an ICNS container from ``(chunk type, PNG payload)`` pairs."""
    if not chunks:
        msg = "an ICNS container needs at least one chunk"
        raise IconsError(msg)
    body = bytearray()
    for chunk_type, payload in chunks:
        if len(chunk_type) != _CHUNK_TYPE_LENGTH:
            msg = f"ICNS chunk type {chunk_type!r} must be four characters"
            raise IconsError(msg)
        body.extend(chunk_type.encode("ascii"))
        body.extend(struct.pack(">I", _CHUNK_HEADER_SIZE + len(payload)))
        body.extend(payload)
    return ICNS_MAGIC + struct.pack(">I", _CHUNK_HEADER_SIZE + len(body)) + bytes(body)


def read_icns_chunk_types(payload: bytes) -> list[str]:
    """Return the chunk types of an ICNS container in file order."""
    if not payload.startswith(ICNS_MAGIC):
        msg = "not an ICNS container"
        raise IconsError(msg)
    total = struct.unpack(">I", payload[4:8])[0]
    if total != len(payload):
        msg = f"ICNS length {total} does not match the {len(payload)} bytes read"
        raise IconsError(msg)
    types: list[str] = []
    offset = _CHUNK_HEADER_SIZE
    while offset < len(payload):
        chunk_type = payload[offset : offset + 4].decode("ascii")
        (chunk_length,) = struct.unpack(">I", payload[offset + 4 : offset + 8])
        types.append(chunk_type)
        offset += chunk_length
    return types


def expected_chunk_types() -> list[str]:
    """Return the ICNS chunk types the staged iconset must produce."""
    return [chunk_type for _size, chunk_type in ICONSET_ENTRIES.values()]


def verify_icns(payload: bytes, *, reference: bytes | None = None) -> None:
    """Fail unless *payload* is a complete icon bundle for this channel.

    ``reference`` is the stock Electron icon: shipping it unchanged is exactly
    the packaging regression this check exists to catch.
    """
    chunk_types = read_icns_chunk_types(payload)
    expected = expected_chunk_types()
    if chunk_types != expected:
        msg = f"ICNS chunks are {chunk_types}, expected {expected}"
        raise IconsError(msg)
    if reference is not None and payload == reference:
        msg = "the packaged icon is identical to Electron's stock icon"
        raise IconsError(msg)


def _render(source: Path, output: Path, iconset: Path | None) -> None:
    if iconset is not None:
        scale_iconset(source, iconset)
        output.write_bytes(build_icns(collect_chunks(iconset)))
        return
    with tempfile.TemporaryDirectory(prefix="t3code-iconset.") as scratch:
        staged = Path(scratch) / "icon.iconset"
        scale_iconset(source, staged)
        output.write_bytes(build_icns(collect_chunks(staged)))


def main() -> None:
    """Render channel artwork into an ``.icns``, or verify a packaged one."""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    render = subcommands.add_parser("render", help="Assemble an .icns bundle.")
    render.add_argument("source", type=Path, help="Source artwork (typically 1024px).")
    render.add_argument("output", type=Path, help="Path of the .icns file to write.")
    render.add_argument(
        "--iconset",
        type=Path,
        help="Directory for the intermediate iconset (defaults to a temporary one).",
    )
    verify = subcommands.add_parser("verify", help="Check a packaged .icns bundle.")
    verify.add_argument("path", type=Path, help="Path of the .icns file to check.")
    verify.add_argument(
        "--reference",
        type=Path,
        help="Optional stock icon that must differ from the packaged one.",
    )
    args = parser.parse_args()

    if args.command == "render":
        _render(args.source, args.output, args.iconset)
        return
    reference = args.reference.read_bytes() if args.reference is not None else None
    verify_icns(args.path.read_bytes(), reference=reference)


if __name__ == "__main__":
    main()
