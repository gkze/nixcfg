"""Explicit authorization for bounded Nix semantic tests."""

from typing import TYPE_CHECKING

import pytest

from lib.tests._nix_eval import allow_nix_evaluation

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _nix_evaluation_scope(request: pytest.FixtureRequest) -> Iterator[None]:
    marker = request.node.get_closest_marker("nix_eval")
    if marker is None:
        yield
    else:
        with allow_nix_evaluation(marker.kwargs.get("reason")):
            yield
