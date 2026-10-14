"""Tests for auto-generated Pydantic models in lib.nix.models._generated."""

import pytest
from pydantic import ValidationError

import lib.nix.models._generated as generated_models


class TestGeneratedModels:
    """Smoke tests for generated schema-backed model types."""

    def test_generated_module_exposes_core_model_types(self) -> None:
        """The generated module publishes the expected top-level model classes."""
        assert hasattr(generated_models, "BuildResult")
        assert hasattr(generated_models, "ContentAddress")
        assert hasattr(generated_models, "FileSystemObject")
        assert hasattr(generated_models, "Hash")
        assert hasattr(generated_models, "StorePath")

    def test_generated_build_result_validates_success_and_failure_variants(
        self,
    ) -> None:
        """BuildResult parses both successful and failed payload variants."""
        success = generated_models.BuildResult.model_validate(
            {
                "success": True,
                "status": "Built",
                "builtOutputs": {
                    "out": {
                        # Wire-shaped values: the schema constrains all three of
                        # these fields, so only a fully typed model accepts them.
                        "id": f"sha256:{'a' * 64}!out",
                        "outPath": f"{'0' * 32}-hello",
                        "signatures": ["cache.example-1:AAAA"],
                    },
                },
            },
        )
        assert success.root.success is True
        assert success.root.status == generated_models.Status.BUILT

        # Regression guard: an unconstrained model used to accept these
        # placeholder shapes, which is how the allOf merge silently degraded
        # required wire fields to `Any`.
        with pytest.raises(ValidationError):
            generated_models.BuildResult.model_validate(
                {
                    "success": True,
                    "status": "Built",
                    "builtOutputs": {
                        "out": {
                            "id": {"drv": "hello"},
                            "outPath": {"output": "nix/store/path"},
                            "signatures": [],
                        },
                    },
                },
            )

        failure = generated_models.BuildResult.model_validate(
            {
                "success": False,
                "status": "HashMismatch",
                "errorMsg": "hash mismatch in fixed-output derivation",
            },
        )
        assert failure.root.success is False
        assert failure.root.status == generated_models.Status1.HASH_MISMATCH

    def test_generated_recursive_file_system_object_parses_directory_entries(
        self,
    ) -> None:
        """Recursive file-system objects parse nested regular and symlink entries."""
        fs_object = generated_models.FileSystemObject.model_validate(
            {
                "type": "directory",
                "entries": {
                    "hello.txt": {
                        "type": "regular",
                        "contents": "hello",
                        "executable": False,
                    },
                    "hello-link": {
                        "type": "symlink",
                        "target": "hello.txt",
                    },
                },
            },
        )
        assert fs_object.root.type == "directory"
        entries = fs_object.root.model_dump().get("entries")
        assert isinstance(entries, dict)
        assert sorted(entries) == ["hello-link", "hello.txt"]

    def test_generated_hash_rejects_invalid_sri(self) -> None:
        """Hash root model rejects non-SRI formatted input."""
        with pytest.raises(ValidationError):
            generated_models.Hash.model_validate("not-a-valid-sri")
