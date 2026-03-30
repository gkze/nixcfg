"""Shared constants and tiny helpers for update workflows."""

REQUIRED_TOOLS = ("nix",)
ALL_TOOLS = ("nix", "nix-prefetch-url", "uv")

FIXED_OUTPUT_NOISE = (
    "error: hash mismatch in fixed-output derivation",
    "specified:",
    "got:",
    "error: Cannot build",
    "Reason:",
    "Output paths:",
    "error: Build failed due to failed dependency",
)

NIX_BUILD_FAILURE_TAIL_LINES = 20

FAKE_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def resolve_timeout_alias(
    *,
    named_timeout: float | None,
    named_timeout_label: str,
    kwargs: dict[str, object],
) -> float | None:
    """Extract and validate a legacy ``timeout`` kwarg.

    Both :func:`~lib.update.net.fetch_url` and
    :func:`~lib.update.process.stream_command` accept a primary timeout
    parameter *and* a legacy ``timeout`` keyword.  This function pops the
    legacy key from *kwargs*, validates that only one was provided, and
    returns the resolved timeout value.  Any remaining unknown keys in
    *kwargs* raise :class:`TypeError`.
    """
    timeout_alias = kwargs.pop("timeout", None)
    if timeout_alias is not None:
        if named_timeout is not None:
            msg = f"Pass only one of '{named_timeout_label}' or legacy 'timeout'"
            raise TypeError(msg)
        if not isinstance(timeout_alias, int | float):
            msg = "timeout must be a number"
            raise TypeError(msg)
        named_timeout = float(timeout_alias)
    if kwargs:
        unknown = ", ".join(sorted(kwargs))
        msg = f"Unexpected keyword argument(s): {unknown}"
        raise TypeError(msg)
    return named_timeout
