"""Parse an exact stable channel from a ``rust-toolchain.toml`` manifest."""

import re
import tomllib

EXACT_STABLE_CHANNEL = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def rust_toolchain_channel(manifest: bytes, *, context: str) -> str:
    """Return the exact stable Rust release a rust-toolchain.toml pins.

    ``rust-bin.stable.<channel>`` only exists for an exact ``major.minor.patch``
    release. A floating channel (``stable``, ``nightly``, ``1.99``) has no
    single manifest to select.
    """
    try:
        payload = tomllib.loads(manifest.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        msg = f"{context} is not a valid rust-toolchain.toml"
        raise ValueError(msg) from exc
    toolchain = payload.get("toolchain")
    channel = toolchain.get("channel") if isinstance(toolchain, dict) else None
    if not isinstance(channel, str) or EXACT_STABLE_CHANNEL.fullmatch(channel) is None:
        msg = (
            f"{context} must pin an exact stable toolchain.channel "
            f"(for example 1.99.1), got {channel!r}"
        )
        raise ValueError(msg)
    return channel


__all__ = ["EXACT_STABLE_CHANNEL", "rust_toolchain_channel"]
