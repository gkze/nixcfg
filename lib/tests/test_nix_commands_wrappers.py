"""Tests for higher-level nix command wrapper modules."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from lib.nix.commands import _json as json_mod
from lib.nix.commands import build as build_mod
from lib.nix.commands import derivation as derivation_mod
from lib.nix.commands import eval as eval_mod
from lib.nix.commands import flake as flake_mod
from lib.nix.commands import hash as hash_mod
from lib.nix.commands import path_info as path_info_mod
from lib.nix.commands import store as store_mod
from lib.nix.commands.base import CommandResult, HashMismatchError, NixCommandError

if TYPE_CHECKING:
    from pathlib import Path


_THREE_SECONDS = 3.0


class _Model:
    @classmethod
    def model_validate(cls, obj: object) -> dict[str, object]:
        """Run this test case."""
        return {"wrapped": obj}


def test_json_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _run_nix(args: list[str], **kwargs: object) -> CommandResult:
        timeout = kwargs.get("timeout")
        assert args == ["nix", "eval", "--json"]
        assert timeout == _THREE_SECONDS
        return CommandResult(args=args, returncode=0, stdout='{"x": 1}', stderr="")

    monkeypatch.setattr(json_mod, "run_nix", _run_nix)
    parsed = asyncio.run(
        json_mod.run_nix_json(["nix", "eval", "--json"], timeout=_THREE_SECONDS)
    )
    assert parsed == {"x": 1}

    with pytest.raises(TypeError, match="command_timeout is required"):
        asyncio.run(json_mod.run_nix_json(["nix", "eval", "--json"]))

    assert json_mod.as_model_mapping({"a": 1}, _Model) == {"a": {"wrapped": 1}}
    with pytest.raises(TypeError, match="Expected JSON object"):
        json_mod.as_model_mapping([], _Model)
    with pytest.raises(TypeError, match="string keys"):
        json_mod.as_model_mapping({1: "x"}, _Model)

    assert json_mod.as_model_list([1, 2], _Model) == [{"wrapped": 1}, {"wrapped": 2}]
    assert json_mod.as_model_list({"a": 1}, _Model) == [{"wrapped": 1}]
    with pytest.raises(TypeError, match="Expected JSON list or object"):
        json_mod.as_model_list("bad", _Model)


def test_build_command_args() -> None:
    """Run this test case."""
    build_options = object.__getattribute__(build_mod, "_BuildCommandOptions")

    args = object.__getattribute__(build_mod, "_build_command_args")(
        expr="1",
        installable=None,
        options=build_options(
            impure=True,
            no_link=True,
            json_output=True,
            extra_args=["--print-out-paths"],
        ),
    )
    assert args == [
        "nix",
        "build",
        "--json",
        "--impure",
        "--no-link",
        "--expr",
        "1",
        "--print-out-paths",
    ]
    installable_args = object.__getattribute__(build_mod, "_build_command_args")(
        expr=None,
        installable=".#pkg",
        options=build_options(
            impure=False,
            no_link=False,
            json_output=False,
            extra_args=None,
        ),
    )
    assert installable_args == ["nix", "build", ".#pkg"]

    with pytest.raises(ValueError, match="exactly one"):
        object.__getattribute__(build_mod, "_build_command_args")(
            expr=None,
            installable=None,
        )
    with pytest.raises(ValueError, match="exactly one"):
        object.__getattribute__(build_mod, "_build_command_args")(
            expr="1",
            installable=".#x",
        )


def test_nix_build_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _ok_run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(
            args=["nix"],
            returncode=0,
            stdout='[{"success": true, "status": "Built", "builtOutputs": {}}]',
            stderr="",
        )

    class _Adapter:
        def validate_python(self, payload: object) -> list[object]:
            """Run this test case."""
            return [payload]

    monkeypatch.setattr(build_mod, "run_nix", _ok_run_nix)
    monkeypatch.setattr(build_mod, "_BUILD_RESULT_LIST", _Adapter())

    built = asyncio.run(build_mod.nix_build(expr="1"))
    assert len(built) == 1

    empty = asyncio.run(build_mod.nix_build(expr="1", json_output=False))
    assert empty == []

    async def _fail_run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=1, stdout="", stderr="err")

    monkeypatch.setattr(build_mod, "run_nix", _fail_run_nix)

    result = CommandResult(args=["nix"], returncode=1, stdout="", stderr="err")
    hash_err = HashMismatchError(result, got_hash="sha256:abc")
    monkeypatch.setattr(
        build_mod.HashMismatchError, "from_stderr", lambda _stderr, _result: hash_err
    )
    with pytest.raises(HashMismatchError):
        asyncio.run(build_mod.nix_build(expr="1"))

    monkeypatch.setattr(
        build_mod.HashMismatchError, "from_stderr", lambda _stderr, _result: None
    )
    with pytest.raises(NixCommandError):
        asyncio.run(build_mod.nix_build(expr="1"))


def test_nix_build_rejects_invalid_option_types() -> None:
    """Run this test case."""
    with pytest.raises(TypeError, match="impure must be a boolean"):
        asyncio.run(build_mod.nix_build(expr="1", impure="yes"))

    with pytest.raises(TypeError, match="no_link must be a boolean"):
        asyncio.run(build_mod.nix_build(expr="1", no_link="yes"))

    with pytest.raises(TypeError, match="json_output must be a boolean"):
        asyncio.run(build_mod.nix_build(expr="1", json_output="yes"))

    with pytest.raises(TypeError, match="extra_args must be a list of strings"):
        asyncio.run(build_mod.nix_build(expr="1", extra_args=["--flag", 1]))


def test_nix_build_dry_run_respects_section_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    seen_args: list[str] = []
    combined = (
        "these derivations will be built:\n"
        "  note: this line is not a derivation\n"
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-foo.drv\n"
        "section complete\n"
        "  /nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-bar.drv\n"
    )

    async def _run_nix(args: list[str], **_kwargs: object) -> CommandResult:
        seen_args.extend(args)
        return CommandResult(args=args, returncode=0, stdout=combined, stderr="")

    monkeypatch.setattr(build_mod, "run_nix", _run_nix)

    drvs = asyncio.run(build_mod.nix_build_dry_run(".#pkg", impure=False))
    assert "--impure" not in seen_args
    assert drvs == {"/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-foo.drv"}


def test_nix_build_dry_run_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    combined = (
        "these derivations will be built:\n"
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-foo.drv\n"
        "  /nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-bar.drv\n"
        "\n"
        "this path is not in section\n"
    )

    async def _run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=0, stdout=combined, stderr="")

    monkeypatch.setattr(build_mod, "run_nix", _run_nix)
    drvs = asyncio.run(build_mod.nix_build_dry_run(".#pkg"))
    assert drvs == {
        "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-foo.drv",
        "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-bar.drv",
    }


def test_derivation_eval_flake_wrappers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run this test case."""

    async def _run_nix_json(args: list[str], **kwargs: object) -> object:
        timeout = kwargs.get("timeout")
        if args[:3] == ["nix", "derivation", "show"]:
            return {"/nix/store/x.drv": {"x": 1}}
        if args[:2] == ["nix", "eval"]:
            return {"value": 1}
        if args[:3] == ["nix", "flake", "metadata"]:
            return {"locked": True}
        if args[:3] == ["nix", "flake", "show"]:
            return {"packages": {}}
        msg = f"unexpected args: {args}, timeout={timeout}"
        raise AssertionError(msg)

    monkeypatch.setattr(derivation_mod, "run_nix_json", _run_nix_json)
    monkeypatch.setattr(
        derivation_mod, "as_model_mapping", lambda raw, _model: {"ok": raw}
    )
    shown = asyncio.run(derivation_mod.nix_derivation_show(".#x"))
    assert "ok" in shown

    monkeypatch.setattr(eval_mod, "run_nix_json", _run_nix_json)
    val = asyncio.run(eval_mod.nix_eval_json("1"))
    assert val == {"value": 1}

    typed = asyncio.run(eval_mod.nix_eval_typed("1", dict[str, int]))
    assert typed == {"value": 1}

    async def _run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=0, stdout="raw-output", stderr="")

    monkeypatch.setattr(eval_mod, "run_nix", _run_nix)
    assert asyncio.run(eval_mod.nix_eval_raw('"x"')) == "raw-output"

    monkeypatch.setattr(flake_mod, "run_nix_json", _run_nix_json)
    monkeypatch.setattr(flake_mod, "run_nix", _run_nix)
    meta = asyncio.run(flake_mod.nix_flake_metadata("."))
    show = asyncio.run(flake_mod.nix_flake_show("."))
    assert meta["locked"] is True
    assert "packages" in show

    asyncio.run(flake_mod.nix_flake_lock_update("demo"))
    flake_path = tmp_path / "flake"
    asyncio.run(flake_mod.nix_flake_lock_update("demo", flake_ref=str(flake_path)))


def test_flake_wrappers_require_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _run_nix_json(_args: list[str], **kwargs: object) -> object:
        _ = kwargs
        return []

    monkeypatch.setattr(flake_mod, "run_nix_json", _run_nix_json)

    with pytest.raises(TypeError, match="Expected JSON object"):
        asyncio.run(flake_mod.nix_flake_metadata("."))

    with pytest.raises(TypeError, match="Expected JSON object"):
        asyncio.run(flake_mod.nix_flake_show("."))


def test_hash_and_path_info_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    prefetch_args: list[list[str]] = []

    async def _run_nix(args: list[str], **_kwargs: object) -> CommandResult:
        if args[:3] == ["nix", "hash", "convert"]:
            return CommandResult(
                args=args, returncode=0, stdout="sha256-AAA=\n", stderr=""
            )
        if args[:1] == ["nix-prefetch-url"]:
            prefetch_args.append(args)
            return CommandResult(
                args=args, returncode=0, stdout="log\nabc\n", stderr=""
            )
        return CommandResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hash_mod, "run_nix", _run_nix)
    assert asyncio.run(hash_mod.nix_hash_convert("abcd")) == "sha256-AAA="

    async def _convert(raw_hash: str, *, hash_algo: str = "sha256") -> str:
        return f"converted:{hash_algo}:{raw_hash}"

    monkeypatch.setattr(hash_mod, "nix_hash_convert", _convert)
    assert (
        asyncio.run(hash_mod.nix_prefetch_url("https://example.com"))
        == "converted:sha256:abc"
    )
    assert (
        asyncio.run(
            hash_mod.nix_prefetch_url(
                "https://example.com",
                name="safe-name.dmg",
            )
        )
        == "converted:sha256:abc"
    )
    assert prefetch_args == [
        ["nix-prefetch-url", "--type", "sha256", "https://example.com"],
        [
            "nix-prefetch-url",
            "--type",
            "sha256",
            "--name",
            "safe-name.dmg",
            "https://example.com",
        ],
    ]
    seen_path_info_args: list[list[str]] = []

    async def _run_nix_json(args: list[str], **kwargs: object) -> object:
        timeout = kwargs.get("timeout")
        if args[:2] == ["nix", "path-info"]:
            seen_path_info_args.append(args)
            return [{"path": "/nix/store/x"}]
        msg = f"unexpected args: {args}, timeout={timeout}"
        raise AssertionError(msg)

    monkeypatch.setattr(path_info_mod, "run_nix_json", _run_nix_json)
    monkeypatch.setattr(path_info_mod, "as_model_list", lambda raw, _model: [raw])
    infos = asyncio.run(
        path_info_mod.nix_path_info(["/nix/store/x"], closure_size=True)
    )
    assert infos
    first = infos[0]
    assert isinstance(first, list)
    checked_first = cast("list[dict[str, str]]", first)
    assert checked_first
    head = checked_first[0]
    assert isinstance(head, dict)
    assert head["path"] == "/nix/store/x"
    assert "--closure-size" in seen_path_info_args[0]

    infos_no_closure = asyncio.run(path_info_mod.nix_path_info(["/nix/store/x"]))
    assert infos_no_closure
    assert "--closure-size" not in seen_path_info_args[1]


def test_store_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _run_nix(args: list[str], **_kwargs: object) -> CommandResult:
        if args[:3] == ["nix-store", "--query", "--deriver"]:
            return CommandResult(
                args=args,
                returncode=0,
                stdout="/nix/store/demo.drv\n",
                stderr="",
            )
        if args[:3] == ["nix-store", "--query", "--references"]:
            return CommandResult(
                args=args,
                returncode=0,
                stdout="/nix/store/a\n\n/nix/store/b\n",
                stderr="",
            )
        if args[:3] == ["nix-store", "--query", "--requisites"]:
            return CommandResult(
                args=args,
                returncode=0,
                stdout="/nix/store/src-a\n\n/nix/store/src-b\n",
                stderr="",
            )
        return CommandResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(store_mod, "run_nix", _run_nix)

    async def _stream_nix(_args: list[str], **kwargs: object) -> object:
        _ = kwargs
        for line in ("l1", "l2"):
            yield line

    monkeypatch.setattr(store_mod, "stream_nix", _stream_nix)
    seen: list[str] = []
    result = asyncio.run(
        store_mod.nix_store_realise(["/nix/store/demo.drv"], on_line=seen.append)
    )
    assert seen == ["l1", "l2"]
    assert result.returncode == 0

    direct = asyncio.run(
        store_mod.nix_store_realise(["/nix/store/demo.drv"], on_line=None)
    )
    assert isinstance(direct, CommandResult)

    deriver = asyncio.run(store_mod.nix_store_query_deriver("/nix/store/pkg"))
    assert deriver == "/nix/store/demo.drv"

    refs = asyncio.run(store_mod.nix_store_query_references("/nix/store/pkg"))
    assert refs == ["/nix/store/a", "/nix/store/b"]

    requisites = asyncio.run(store_mod.nix_store_query_requisites("/nix/store/pkg.drv"))
    assert requisites == ["/nix/store/src-a", "/nix/store/src-b"]


def test_store_deriver_wrapper_handles_unknown_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return None for unknown derivers and raise on command failure."""

    async def _unknown(args: list[str], **_kwargs: object) -> CommandResult:
        return CommandResult(
            args=args,
            returncode=0,
            stdout="unknown-deriver\n",
            stderr="",
        )

    monkeypatch.setattr(store_mod, "run_nix", _unknown)
    assert asyncio.run(store_mod.nix_store_query_deriver("/nix/store/pkg")) is None

    async def _blank(args: list[str], **_kwargs: object) -> CommandResult:
        return CommandResult(args=args, returncode=0, stdout="\n", stderr="")

    monkeypatch.setattr(store_mod, "run_nix", _blank)
    assert asyncio.run(store_mod.nix_store_query_deriver("/nix/store/pkg")) is None

    async def _bad(args: list[str], **_kwargs: object) -> CommandResult:
        return CommandResult(args=args, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(store_mod, "run_nix", _bad)
    with pytest.raises(NixCommandError):
        asyncio.run(store_mod.nix_store_query_deriver("/nix/store/pkg"))
