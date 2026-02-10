"""Regression tests for parsing Nix hash mismatch output."""

# ruff: noqa: D102, S101

from __future__ import annotations

import asyncio

from libnix.commands.base import CommandResult, HashMismatchError


class TestHashMismatchError:
    """Extraction of hashes and derivation paths from Nix output.

    Tests cover every known format from Nix source code:
    - derivation-check.cc: FOD hash mismatch (SRI format)
    - local-store.cc: NAR import hash mismatch (Nix32 format)
    - local-store.cc: CA hash mismatch importing path (Nix32 format)
    """

    def _make_result(self, stderr: str = "", stdout: str = "") -> CommandResult:
        return CommandResult(
            args=["nix", "build"],
            returncode=1,
            stdout=stdout,
            stderr=stderr,
        )

    SAMPLE_FOD_STDERR = (
        "error: hash mismatch in fixed-output derivation "
        "'/nix/store/g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-source.drv':\n"
        "  specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
        "     got:    sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=\n"
    )

    SAMPLE_NAR_MISMATCH = (
        "error: hash mismatch importing path '/nix/store/abc-foo';\n"
        "  specified: 0c5b8vw40d1178xlpddw65q9gf1h2186jcc3p4swinwggbllv8mk\n"
        "  got:       1d6b9xw51a1289ymqaax76ra2gi2i3297kdd4q5sxjaxhicnmwal\n"
    )

    SAMPLE_CA_MISMATCH = (
        "error: ca hash mismatch importing path '/nix/store/def-bar';\n"
        "  specified: 0c5b8vw40d1178xlpddw65q9gf1h2186jcc3p4swinwggbllv8mk\n"
        "  got:       1d6b9xw51a1289ymqaax76ra2gi2i3297kdd4q5sxjaxhicnmwal\n"
    )

    SAMPLE_HEX_MISMATCH = (
        "hash mismatch in fixed-output derivation '/nix/store/xyz-baz.drv':\n"
        "  specified: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n"
        "     got:    sha256:a948904f2f0f479b8f8564e9c95a6c9d2db76a5a4b1c3d8ef6c2a4e6f1a7d3e0\n"
    )

    def test_fod_sha256_sri(self) -> None:
        result = self._make_result(self.SAMPLE_FOD_STDERR)
        err = HashMismatchError.from_output(self.SAMPLE_FOD_STDERR, result)
        assert err is not None
        assert err.hash == "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="
        assert err.specified == "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        assert err.drv_path == "/nix/store/g1w7hy3qg1w7hy3qg1w7hy3qg1w7hy3q-source.drv"

    def test_fod_sha512_sri(self) -> None:
        stderr = (
            "hash mismatch in fixed-output derivation '/nix/store/abc-foo.drv':\n"
            "  specified: sha512-AAAA=\n"
            "     got:    sha512-BBBB=\n"
        )
        err = HashMismatchError.from_output(stderr, self._make_result(stderr))
        assert err is not None
        assert err.hash == "sha512-BBBB="
        assert err.specified == "sha512-AAAA="

    def test_fod_sha1_sri(self) -> None:
        stderr = (
            "hash mismatch in fixed-output derivation '/nix/store/abc-bar.drv':\n"
            "  specified: sha1-AAAA=\n"
            "     got:    sha1-BBBB=\n"
        )
        err = HashMismatchError.from_output(stderr, self._make_result(stderr))
        assert err is not None
        assert err.hash == "sha1-BBBB="
        assert err.specified == "sha1-AAAA="

    def test_nar_import_nix32(self) -> None:
        err = HashMismatchError.from_output(
            self.SAMPLE_NAR_MISMATCH,
            self._make_result(self.SAMPLE_NAR_MISMATCH),
        )
        assert err is not None
        assert err.hash == "1d6b9xw51a1289ymqaax76ra2gi2i3297kdd4q5sxjaxhicnmwal"
        assert err.specified == "0c5b8vw40d1178xlpddw65q9gf1h2186jcc3p4swinwggbllv8mk"
        assert err.drv_path == "/nix/store/abc-foo"

    def test_ca_import_nix32(self) -> None:
        err = HashMismatchError.from_output(
            self.SAMPLE_CA_MISMATCH,
            self._make_result(self.SAMPLE_CA_MISMATCH),
        )
        assert err is not None
        assert err.hash == "1d6b9xw51a1289ymqaax76ra2gi2i3297kdd4q5sxjaxhicnmwal"
        assert err.drv_path == "/nix/store/def-bar"

    def test_prefixed_hex(self) -> None:
        err = HashMismatchError.from_output(
            self.SAMPLE_HEX_MISMATCH,
            self._make_result(self.SAMPLE_HEX_MISMATCH),
        )
        assert err is not None
        assert (
            err.hash
            == "sha256:a948904f2f0f479b8f8564e9c95a6c9d2db76a5a4b1c3d8ef6c2a4e6f1a7d3e0"
        )
        assert (
            err.specified
            == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_nested_derivation_takes_last_match(self) -> None:
        stderr = (
            "hash mismatch in fixed-output derivation '/nix/store/aaa-outer.drv':\n"
            "  specified: sha256-OUTERspecOUTERspecOUTERspecOUTERspecOUTERspe0=\n"
            "     got:    sha256-OUTERgotxOUTERgotxOUTERgotxOUTERgotxOUTERgo0=\n"
            "\n"
            "hash mismatch in fixed-output derivation '/nix/store/bbb-inner.drv':\n"
            "  specified: sha256-INNERspecINNERspecINNERspecINNERspecINNERspe0=\n"
            "     got:    sha256-INNERgotxINNERgotxINNERgotxINNERgotxINNERgo0=\n"
        )
        err = HashMismatchError.from_output(stderr, self._make_result(stderr))
        assert err is not None
        assert err.hash == "sha256-INNERgotxINNERgotxINNERgotxINNERgotxINNERgo0="
        assert err.specified == "sha256-INNERspecINNERspecINNERspecINNERspecINNERspe0="

    def test_hash_in_stdout_not_stderr(self) -> None:
        stdout = (
            "hash mismatch in fixed-output derivation '/nix/store/abc-source.drv':\n"
            "  specified: sha256-AAA=\n"
            "     got:    sha256-BBB=\n"
        )
        err = HashMismatchError.from_output(
            stdout,
            self._make_result(stdout="", stderr=""),
        )
        assert err is not None
        assert err.hash == "sha256-BBB="

    def test_whitespace_variations(self) -> None:
        stderr = (
            "hash mismatch in fixed-output derivation '/nix/store/x-y.drv':\n"
            "  specified: sha256-AAAA=\n"
            "     got:    sha256-BBBB=\n"
        )
        err = HashMismatchError.from_output(stderr, self._make_result(stderr))
        assert err is not None
        assert err.hash == "sha256-BBBB="
        assert err.specified == "sha256-AAAA="

    def test_no_match_returns_none(self) -> None:
        unrelated = "error: attribute 'foo' missing\n"
        err = HashMismatchError.from_output(unrelated, self._make_result(unrelated))
        assert err is None

    def test_from_stderr_compat_alias(self) -> None:
        result = self._make_result(self.SAMPLE_FOD_STDERR)
        err = HashMismatchError.from_stderr(self.SAMPLE_FOD_STDERR, result)
        assert err is not None
        assert err.hash == "sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="

    def test_base64_with_plus_and_slash(self) -> None:
        stderr = (
            "hash mismatch in fixed-output derivation '/nix/store/abc-foo.drv':\n"
            "  specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
            "     got:    sha256-a+b/c+D/eF+gH/iJ+kL/mN+oP/qR+sT/uV+wX/yZ0=\n"
        )
        err = HashMismatchError.from_output(stderr, self._make_result(stderr))
        assert err is not None
        assert err.hash == "sha256-a+b/c+D/eF+gH/iJ+kL/mN+oP/qR+sT/uV+wX/yZ0="

    def test_no_drv_path_still_extracts_hash(self) -> None:
        stderr = (
            "some other error context\n"
            "  specified: sha256-OLD=\n"
            "     got:    sha256-NEW=\n"
        )
        err = HashMismatchError.from_output(stderr, self._make_result(stderr))
        assert err is not None
        assert err.hash == "sha256-NEW="
        assert err.specified == "sha256-OLD="
        assert err.drv_path is None

    def test_is_sri_true_for_sri_hash(self) -> None:
        result = self._make_result(self.SAMPLE_FOD_STDERR)
        err = HashMismatchError.from_output(self.SAMPLE_FOD_STDERR, result)
        assert err is not None
        assert err.is_sri is True

    def test_is_sri_false_for_nix32(self) -> None:
        err = HashMismatchError.from_output(
            self.SAMPLE_NAR_MISMATCH,
            self._make_result(self.SAMPLE_NAR_MISMATCH),
        )
        assert err is not None
        assert err.is_sri is False

    def test_is_sri_false_for_prefixed_hex(self) -> None:
        err = HashMismatchError.from_output(
            self.SAMPLE_HEX_MISMATCH,
            self._make_result(self.SAMPLE_HEX_MISMATCH),
        )
        assert err is not None
        assert err.is_sri is False

    def test_to_sri_noop_for_sri_hash(self) -> None:
        result = self._make_result(self.SAMPLE_FOD_STDERR)
        err = HashMismatchError.from_output(self.SAMPLE_FOD_STDERR, result)
        assert err is not None
        sri = asyncio.run(err.to_sri())
        assert sri == err.hash
