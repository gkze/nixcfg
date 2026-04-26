"""Tests for structured update PR body rendering."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lib.update.ci import pr_body


def _model() -> pr_body.PRBodyModel:
    return pr_body.PRBodyModel(
        workflow_run_url="https://example.test/actions/runs/1",
        compare_url="https://example.test/compare/main...update_flake_lock_action",
        updated_flake_inputs=(
            pr_body.FlakeInputUpdate(
                input_name="nixpkgs",
                source=pr_body.LinkValue(
                    label="NixOS/nixpkgs",
                    url="https://github.com/NixOS/nixpkgs",
                ),
                previous=pr_body.LinkValue(
                    label="6201e20",
                    url="https://github.com/NixOS/nixpkgs/commit/6201e203d09599479a3b3450ed24fa81537ebc4e",
                ),
                current=pr_body.LinkValue(
                    label="b12141e",
                    url="https://github.com/NixOS/nixpkgs/commit/b12141ef619e0a9c1c84dc8c684040326f27cdcc",
                ),
                diff=pr_body.LinkValue(
                    label="Diff",
                    url="https://github.com/NixOS/nixpkgs/compare/6201e203d09599479a3b3450ed24fa81537ebc4e...b12141ef619e0a9c1c84dc8c684040326f27cdcc",
                ),
            ),
        ),
        added_flake_inputs=(
            pr_body.FlakeInputSnapshot(
                input_name="demo",
                source=pr_body.LinkValue(label="acme/demo"),
                revision=pr_body.LinkValue(label="abc123"),
            ),
        ),
        removed_flake_inputs=(
            pr_body.FlakeInputSnapshot(
                input_name="old-demo",
                source=pr_body.LinkValue(label="acme/old-demo"),
                revision=pr_body.LinkValue(label="deadbee"),
            ),
        ),
        source_changes=(
            pr_body.SourceChange(
                path="packages/demo/sources.json",
                url="https://example.test/blob/update_flake_lock_action/packages/demo/sources.json",
                diff='@@ ["version"]\n- "1.0.0"\n+ "2.0.0"',
            ),
        ),
        certification=pr_body.CertificationSection(
            workflow_url="https://example.test/actions/runs/2",
            updated_at=datetime(2026, 4, 24, 17, 15, tzinfo=UTC),
            elapsed_seconds=8100.0,
            cachix_name="gkze",
            closures=(
                pr_body.CertificationTarget(ref=".#pkgs.aarch64-darwin.opencode"),
                pr_body.CertificationSharedClosure(
                    refs=(
                        ".#darwinConfigurations.argus.system",
                        ".#darwinConfigurations.rocinante.system",
                    ),
                    excluded_heavy_closure_count=2,
                ),
                pr_body.CertificationTarget(ref=".#pkgs.x86_64-linux.nixcfg"),
            ),
        ),
    )


def test_render_pr_body_round_trips_serialized_model() -> None:
    """Embed the structured model in markdown and recover it without loss."""
    model = _model()

    rendered = pr_body.render_pr_body(model)

    assert pr_body.extract_pr_body_model(rendered) == model


def test_render_pr_body_includes_expected_markdown_sections() -> None:
    """Render visible Markdown sections from structured PR body data."""
    rendered = pr_body.render_pr_body(_model())

    assert "**[Workflow run](https://example.test/actions/runs/1)**" in rendered
    assert (
        "**[Compare](https://example.test/compare/main...update_flake_lock_action)**"
        in rendered
    )
    assert "### Updated flake inputs" in rendered
    assert "### Added flake inputs" in rendered
    assert "### Removed flake inputs" in rendered
    assert "### Per-package sources.json changes" in rendered
    assert "## Certification" in rendered
    assert "| Input | Source | From | To | Diff |" in rendered
    assert "<details>" in rendered
    assert "```diff" in rendered
    assert "Closures pushed to Cachix (`gkze`):" in rendered
    assert "nixcfg-pr-body-model:start" in rendered


def test_pr_body_helpers_reject_invalid_internal_shapes() -> None:
    """Keep defensive rendering and hidden-comment errors explicit."""
    with pytest.raises(ValueError, match="at least one table row"):
        pr_body._render_table([])

    with pytest.raises(ValueError, match="at least one code-formatted value"):
        pr_body._code_list_text(())

    assert pr_body._code_list_text(("one", "two", "three")) == (
        "`one`, `two`, and `three`"
    )

    with pytest.raises(ValueError, match="does not contain"):
        pr_body.extract_pr_body_model("No hidden model here\n")

    rendered = pr_body.render_pr_body(_model())
    with pytest.raises(ValueError, match="multiple serialized"):
        pr_body.extract_pr_body_model(f"{rendered}\n{rendered}")
