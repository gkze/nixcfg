"""Nix boundary adapter for updater lifecycle tests using synthetic overlays.

The real batch preparation/build contract is exercised in test_update_prepared_probes.
These lifecycle tests control platform results independently of Nix evaluation.
"""

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from lib.update import nix as update_nix
from lib.update.nix import PreparedProbe


def install_prepared_probe_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    original_compute = update_nix.compute_fixed_output_hash
    monkeypatch.setattr(
        update_nix,
        "compute_drv_fingerprint",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="drv"),
    )

    async def prepare(source, expressions, *, config, **_kwargs):
        return {
            key: PreparedProbe(
                f"/nix/store/{key}.drv",
                await update_nix.compute_drv_fingerprint(
                    source, system=key or None, config=config
                ),
                expression,
            )
            for key, expression in expressions.items()
        }

    async def build(source, expr, **kwargs):
        if isinstance(expr, PreparedProbe):
            system = (
                expr.drv_path.removeprefix("/nix/store/").removesuffix(".drv") or None
            )
            return await update_nix.compute_overlay_hash(
                source, system=system, **kwargs
            )
        return await original_compute(source, expr, **kwargs)

    monkeypatch.setattr(update_nix, "prepare_fixed_output_probes", prepare)
    monkeypatch.setattr(update_nix, "compute_fixed_output_hash", build)
