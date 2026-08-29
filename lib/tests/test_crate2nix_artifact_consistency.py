"""Pure consistency contracts for crate2nix generated artifacts."""

import subprocess
from pathlib import Path

import pytest

from lib.update import crate2nix


def _target(*, crate_sources: Path | None) -> crate2nix.Crate2NixTarget:
    return crate2nix.Crate2NixTarget(
        name="demo",
        patched_src_installable="path:.#demo-crate2nix-src",
        cargo_nix=Path("demo/Cargo.nix"),
        crate_hashes=Path("demo/crate-hashes.json"),
        normalizer_path=Path("demo/normalize.py"),
        supported_platforms=("test-system",),
        source_input="demo" if crate_sources is not None else None,
        crate_sources=crate_sources,
    )


def _write_current_artifacts(
    root: Path,
    target: crate2nix.Crate2NixTarget,
) -> None:
    (root / target.cargo_nix).parent.mkdir(parents=True, exist_ok=True)
    (root / target.cargo_nix).write_text("cargo\n", encoding="utf-8")
    (root / target.crate_hashes).write_text("{}\n", encoding="utf-8")


def test_refresh_requires_a_manifest_for_generated_local_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A normalized local source must not escape without its hash manifest."""
    target = _target(crate_sources=None)
    patched_src = tmp_path / "patched-src"
    patched_src.mkdir()
    monkeypatch.setattr(crate2nix, "_build_patched_src", lambda _target: patched_src)
    monkeypatch.setattr(
        crate2nix,
        "_crate2nix_cargo_home",
        lambda: tmp_path / "cargo-home",
    )
    monkeypatch.setattr(
        crate2nix,
        "load_normalizer",
        lambda _path: lambda text: (text, 0, False),
    )

    def generate(
        args: list[str],
        *,
        env: dict[str, str],
        generated_outputs: tuple[Path, ...],
        seeded_outputs: dict[Path, bytes] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del env, seeded_outputs
        cargo_nix, crate_hashes = generated_outputs
        cargo_nix.write_text(
            """{ lib
, rootSrc ? ./.
}:
{
  demo.src = lib.cleanSourceWith {
    filter = sourceFilter;
    src = "${rootSrc}/crates/demo";
  };
}
""",
            encoding="utf-8",
        )
        crate_hashes.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(crate2nix, "_run_crate2nix_generate", generate)

    with pytest.raises(
        RuntimeError,
        match="Missing crate source artifact path for demo",
    ):
        crate2nix._refresh_target(target)


@pytest.mark.parametrize(
    ("crate_sources", "rendered_sources"),
    [
        (Path("demo/crate-sources.json"), None),
        (None, "{}\n"),
    ],
    ids=("missing-rendered-manifest", "undeclared-rendered-manifest"),
)
def test_artifact_updates_reject_incomplete_manifest_metadata(
    monkeypatch: pytest.MonkeyPatch,
    crate_sources: Path | None,
    rendered_sources: str | None,
) -> None:
    """Checked-in artifact updates require both sides of the manifest contract."""
    target = _target(crate_sources=crate_sources)
    monkeypatch.setitem(crate2nix.TARGETS, target.name, target)
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "test-system")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target: crate2nix.RefreshResult(
            cargo_nix="cargo\n",
            crate_hashes="{}\n",
            crate_sources=rendered_sources,
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="Incomplete crate source artifact metadata for demo",
    ):
        crate2nix.crate2nix_artifact_updates(target.name)


def test_artifact_updates_accept_a_target_without_source_manifests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Targets with no local source slices should emit only their two core artifacts."""
    target = _target(crate_sources=None)
    monkeypatch.setitem(crate2nix.TARGETS, target.name, target)
    monkeypatch.setattr(crate2nix, "_current_platform", lambda: "test-system")
    monkeypatch.setattr(
        crate2nix,
        "_refresh_target",
        lambda _target: crate2nix.RefreshResult(
            cargo_nix="cargo\n",
            crate_hashes="{}\n",
        ),
    )

    updates = crate2nix.crate2nix_artifact_updates(target.name)

    assert tuple(artifact.path for artifact in updates) == (
        target.cargo_nix,
        target.crate_hashes,
    )


def test_target_change_detection_tracks_the_manifest_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing, matching, and changed manifests participate in drift detection."""
    target = _target(crate_sources=Path("demo/crate-sources.json"))
    refreshed = crate2nix.RefreshResult(
        cargo_nix="cargo\n",
        crate_hashes="{}\n",
        crate_sources="manifest\n",
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)
    _write_current_artifacts(tmp_path, target)
    manifest = tmp_path / target.crate_sources

    assert crate2nix._target_has_changes(target, refreshed)

    manifest.write_text("manifest\n", encoding="utf-8")
    assert not crate2nix._target_has_changes(target, refreshed)

    manifest.write_text("stale\n", encoding="utf-8")
    assert crate2nix._target_has_changes(target, refreshed)


@pytest.mark.parametrize(
    ("crate_sources", "rendered_sources"),
    [
        (Path("demo/crate-sources.json"), None),
        (None, "manifest\n"),
    ],
    ids=("missing-rendered-manifest", "undeclared-rendered-manifest"),
)
def test_target_change_detection_rejects_incomplete_manifest_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    crate_sources: Path | None,
    rendered_sources: str | None,
) -> None:
    """Drift checks must not silently discard one side of the manifest contract."""
    target = _target(crate_sources=crate_sources)
    refreshed = crate2nix.RefreshResult(
        cargo_nix="cargo\n",
        crate_hashes="{}\n",
        crate_sources=rendered_sources,
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)
    _write_current_artifacts(tmp_path, target)

    with pytest.raises(
        RuntimeError,
        match="Incomplete crate source artifact metadata for demo",
    ):
        crate2nix._target_has_changes(target, refreshed)


def test_write_target_persists_a_complete_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A complete refresh writes Cargo, hashes, and source identity artifacts."""
    target = _target(crate_sources=Path("demo/crate-sources.json"))
    refreshed = crate2nix.RefreshResult(
        cargo_nix="cargo\n",
        crate_hashes="{}\n",
        crate_sources="manifest\n",
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)
    (tmp_path / "demo").mkdir()

    crate2nix._write_target(target, refreshed)

    assert (tmp_path / target.cargo_nix).read_text(encoding="utf-8") == "cargo\n"
    assert (tmp_path / target.crate_hashes).read_text(encoding="utf-8") == "{}\n"
    assert (tmp_path / target.crate_sources).read_text(encoding="utf-8") == (
        "manifest\n"
    )


@pytest.mark.parametrize(
    ("crate_sources", "rendered_sources"),
    [
        (Path("demo/crate-sources.json"), None),
        (None, "manifest\n"),
    ],
    ids=("missing-rendered-manifest", "undeclared-rendered-manifest"),
)
def test_write_target_rejects_incomplete_manifest_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    crate_sources: Path | None,
    rendered_sources: str | None,
) -> None:
    """Persistence must fail closed when manifest path and content disagree."""
    target = _target(crate_sources=crate_sources)
    refreshed = crate2nix.RefreshResult(
        cargo_nix="cargo\n",
        crate_hashes="{}\n",
        crate_sources=rendered_sources,
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)
    (tmp_path / "demo").mkdir()

    with pytest.raises(
        RuntimeError,
        match="Incomplete crate source artifact metadata for demo",
    ):
        crate2nix._write_target(target, refreshed)
