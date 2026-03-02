"""Tests for auto-generated Pydantic models in lib.nix.models._generated."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import lib.nix.models._generated as generated_models
from lib.tests._assertions import check


class TestGeneratedModels:
    """Smoke tests for generated schema-backed model types."""

    def test_generated_module_exposes_core_model_types(self) -> None:
        """The generated module publishes the expected top-level model classes."""
        check(hasattr(generated_models, "BuildResult"))
        check(hasattr(generated_models, "ContentAddress"))
        check(hasattr(generated_models, "FileSystemObject"))
        check(hasattr(generated_models, "Hash"))
        check(hasattr(generated_models, "StorePath"))

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
                        "id": {"drv": "hello"},
                        "outPath": {"output": "nix/store/path"},
                        "signatures": [],
                    },
                },
            },
        )
        check(success.root.success is True)
        check(success.root.status == generated_models.Status.BUILT)

        failure = generated_models.BuildResult.model_validate(
            {
                "success": False,
                "status": "HashMismatch",
                "errorMsg": "hash mismatch in fixed-output derivation",
            },
        )
        check(failure.root.success is False)
        check(failure.root.status == generated_models.Status1.HASH_MISMATCH)

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
        check(fs_object.root.type == "directory")
        entries = fs_object.root.model_dump().get("entries")
        check(isinstance(entries, dict))
        check(sorted(entries) == ["hello-link", "hello.txt"])

    def test_generated_hash_rejects_invalid_sri(self) -> None:
        """Hash root model rejects non-SRI formatted input."""
        with pytest.raises(ValidationError):
            generated_models.Hash.model_validate("not-a-valid-sri")
