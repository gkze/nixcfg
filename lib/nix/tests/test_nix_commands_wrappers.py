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
from lib.nix.tests._assertions import check

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
        check(args == ["nix", "eval", "--json"])
        check(timeout == _THREE_SECONDS)
        return CommandResult(args=args, returncode=0, stdout='{"x": 1}', stderr="")

    monkeypatch.setattr(json_mod, "run_nix", _run_nix)
    parsed = asyncio.run(
        json_mod.run_nix_json(["nix", "eval", "--json"], timeout=_THREE_SECONDS)
    )
    check(parsed == {"x": 1})

    check(json_mod.as_model_mapping({"a": 1}, _Model) == {"a": {"wrapped": 1}})
    with pytest.raises(TypeError, match="Expected JSON object"):
        json_mod.as_model_mapping([], _Model)
    with pytest.raises(TypeError, match="string keys"):
        json_mod.as_model_mapping({1: "x"}, _Model)

    check(json_mod.as_model_list([1, 2], _Model) == [{"wrapped": 1}, {"wrapped": 2}])
    check(json_mod.as_model_list({"a": 1}, _Model) == [{"wrapped": 1}])
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
    check(
        args
        == [
            "nix",
            "build",
            "--json",
            "--impure",
            "--no-link",
            "--expr",
            "1",
            "--print-out-paths",
        ]
    )

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
    check(installable_args == ["nix", "build", ".#pkg"])

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
    check(len(built) == 1)

    empty = asyncio.run(build_mod.nix_build(expr="1", json_output=False))
    check(empty == [])

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
    check(
        drvs
        == {
            "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-foo.drv",
            "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-bar.drv",
        }
    )


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
    check("ok" in shown)

    monkeypatch.setattr(eval_mod, "run_nix_json", _run_nix_json)
    val = asyncio.run(eval_mod.nix_eval_json("1"))
    check(val == {"value": 1})

    typed = asyncio.run(eval_mod.nix_eval_typed("1", dict[str, int]))
    check(typed == {"value": 1})

    async def _run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=0, stdout="raw-output", stderr="")

    monkeypatch.setattr(eval_mod, "run_nix", _run_nix)
    check(asyncio.run(eval_mod.nix_eval_raw('"x"')) == "raw-output")

    monkeypatch.setattr(flake_mod, "run_nix_json", _run_nix_json)
    monkeypatch.setattr(flake_mod, "run_nix", _run_nix)
    meta = asyncio.run(flake_mod.nix_flake_metadata("."))
    show = asyncio.run(flake_mod.nix_flake_show("."))
    check(meta["locked"] is True)
    check("packages" in show)

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


def test_hash_path_info_store_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _run_nix(args: list[str], **_kwargs: object) -> CommandResult:
        if args[:3] == ["nix", "hash", "convert"]:
            return CommandResult(
                args=args, returncode=0, stdout="sha256-AAA=\n", stderr=""
            )
        if args[:1] == ["nix-prefetch-url"]:
            return CommandResult(
                args=args, returncode=0, stdout="log\nabc\n", stderr=""
            )
        if args[:3] == ["nix-store", "--query", "--references"]:
            return CommandResult(
                args=args,
                returncode=0,
                stdout="/nix/store/a\n\n/nix/store/b\n",
                stderr="",
            )
        return CommandResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hash_mod, "run_nix", _run_nix)
    check(asyncio.run(hash_mod.nix_hash_convert("abcd")) == "sha256-AAA=")

    async def _convert(raw_hash: str, *, hash_algo: str = "sha256") -> str:
        return f"converted:{hash_algo}:{raw_hash}"

    monkeypatch.setattr(hash_mod, "nix_hash_convert", _convert)
    check(
        asyncio.run(hash_mod.nix_prefetch_url("https://example.com"))
        == "converted:sha256:abc"
    )

    async def _run_nix_json(args: list[str], **kwargs: object) -> object:
        timeout = kwargs.get("timeout")
        if args[:2] == ["nix", "path-info"]:
            return [{"path": "/nix/store/x"}]
        msg = f"unexpected args: {args}, timeout={timeout}"
        raise AssertionError(msg)

    monkeypatch.setattr(path_info_mod, "run_nix_json", _run_nix_json)
    monkeypatch.setattr(path_info_mod, "as_model_list", lambda raw, _model: [raw])
    infos = asyncio.run(
        path_info_mod.nix_path_info(["/nix/store/x"], closure_size=True)
    )
    check(infos)
    first = infos[0]
    check(isinstance(first, list))
    checked_first = cast("list[dict[str, str]]", first)
    check(checked_first)
    head = checked_first[0]
    check(isinstance(head, dict))
    check(head["path"] == "/nix/store/x")

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
    check(seen == ["l1", "l2"])
    check(result.returncode == 0)

    direct = asyncio.run(
        store_mod.nix_store_realise(["/nix/store/demo.drv"], on_line=None)
    )
    check(isinstance(direct, CommandResult))

    refs = asyncio.run(store_mod.nix_store_query_references("/nix/store/pkg"))
    check(refs == ["/nix/store/a", "/nix/store/b"])
