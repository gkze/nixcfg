"""Warmup receipts retain only successfully realized, present store outputs."""

import json
import subprocess
from pathlib import Path

import pytest

from lib.update.ci.warmup import _remember_realized_warmup_outputs
from lib.update.derivation_validation import DerivationValidationFailure


def test_warmup_receipt_excludes_failed_and_missing_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed_drv = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-failed.drv"
    failed_output = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-failed"
    missing_drv = "/nix/store/cccccccccccccccccccccccccccccccc-missing.drv"
    successful_drv = "/nix/store/dddddddddddddddddddddddddddddddd-successful.drv"
    realized = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-realized"
    missing_output = "/nix/store/ffffffffffffffffffffffffffffffff-missing"
    direct_output = "/nix/store/gggggggggggggggggggggggggggggggg-direct"
    present = {failed_drv, failed_output, successful_drv, realized, direct_output}
    original_exists = Path.exists
    queries: list[list[str]] = []

    def exists(path: Path) -> bool:
        if str(path).startswith("/nix/store/"):
            return str(path) in present
        return original_exists(path)

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args == ["nix-store", "--query", "--outputs", successful_drv]
        queries.append(args)
        return subprocess.CompletedProcess(
            args, 0, f"{realized}\n{missing_output}\nrelative-output\n{realized}\n", ""
        )

    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(Path, "exists", exists)
    failures = tuple(
        DerivationValidationFailure(
            source="root-warmup", installable=installable, message="build failed"
        )
        for installable in (f"{failed_drv}^*", failed_output)
    )

    _remember_realized_warmup_outputs(
        (
            failed_drv,
            failed_output,
            missing_drv,
            successful_drv,
            direct_output,
            realized,
        ),
        failures,
        run=run,
    )

    assert len(queries) == 1
    receipt = tmp_path / "update-artifacts" / "realized-root-paths.json"
    assert json.loads(receipt.read_text()) == {"storePaths": [realized, direct_output]}


@pytest.mark.parametrize(
    "window",
    [
        (),
        (
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-missing.drv",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-missing",
        ),
    ],
)
def test_empty_or_unrealized_window_preserves_receipt(
    window: tuple[str, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = tmp_path / "update-artifacts" / "realized-root-paths.json"
    receipt.parent.mkdir()
    before = (
        b'{"storePaths": ["/nix/store/cccccccccccccccccccccccccccccccc-earlier"]}\n'
    )
    receipt.write_bytes(before)
    original_exists = Path.exists

    def exists(path: Path) -> bool:
        if str(path).startswith("/nix/store/"):
            return False
        return original_exists(path)

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail(f"An absent derivation must not query outputs: {args}")

    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(Path, "exists", exists)

    _remember_realized_warmup_outputs(window, (), run=run)

    assert receipt.read_bytes() == before
