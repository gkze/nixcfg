"""Tests for higher-level nix command wrapper modules."""

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

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

    async def _run_nix(args: list[str], *, command_timeout: float) -> CommandResult:
        assert args == ["nix", "eval", "--json"]
        assert command_timeout == _THREE_SECONDS
        return CommandResult(args=args, returncode=0, stdout='{"x": 1}', stderr="")

    monkeypatch.setattr(json_mod, "run_nix", _run_nix)
    parsed = asyncio.run(
        json_mod.run_nix_json(["nix", "eval", "--json"], command_timeout=_THREE_SECONDS)
    )
    assert parsed == {"x": 1}

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
        build_mod.HashMismatchError, "from_output", lambda _output, _result: hash_err
    )
    with pytest.raises(HashMismatchError):
        asyncio.run(build_mod.nix_build(expr="1"))

    monkeypatch.setattr(
        build_mod.HashMismatchError, "from_output", lambda _output, _result: None
    )
    with pytest.raises(NixCommandError):
        asyncio.run(build_mod.nix_build(expr="1"))


def test_nix_build_parses_hash_mismatch_from_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stdout-only Nix diagnostic must retain its typed hash error."""
    output = (
        "error: hash mismatch in fixed-output derivation '/nix/store/stdout.drv'\n"
        "specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
        "got: sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=\n"
    )

    async def _run_nix(*_a: object, **_k: object) -> CommandResult:
        return CommandResult(args=["nix"], returncode=1, stdout=output, stderr="")

    monkeypatch.setattr(build_mod, "run_nix", _run_nix)
    with pytest.raises(HashMismatchError) as caught:
        asyncio.run(build_mod.nix_build(expr="1"))
    assert caught.value.drv_path == "/nix/store/stdout.drv"
    assert caught.value.hash.startswith("sha256-")


def test_nix_build_dry_run_respects_section_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    seen_args: list[str] = []
    seen_kwargs: dict[str, object] = {}
    combined = (
        "these derivations will be built:\n"
        "  note: this line is not a derivation\n"
        "  /nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-foo.drv\n"
        "section complete\n"
        "  /nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-bar.drv\n"
    )

    async def _run_nix(args: list[str], **kwargs: object) -> CommandResult:
        seen_args.extend(args)
        seen_kwargs.update(kwargs)
        return CommandResult(args=args, returncode=0, stdout=combined, stderr="")

    monkeypatch.setattr(build_mod, "run_nix", _run_nix)

    env = {"NIXPKGS_ALLOW_UNFREE": "1"}
    drvs = asyncio.run(build_mod.nix_build_dry_run(".#pkg", impure=False, env=env))
    assert "--impure" not in seen_args
    assert seen_kwargs["env"] == env
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
        timeout = kwargs.get("command_timeout")
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
        if args[:3] == ["nix", "store", "prefetch-file"]:
            prefetch_args.append(args)
            return CommandResult(
                args=args,
                returncode=0,
                stdout='{"hash":"sha256-AAA=","storePath":"/nix/store/example"}',
                stderr="",
            )
        return CommandResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hash_mod, "run_nix", _run_nix)
    assert asyncio.run(hash_mod.nix_hash_convert("abcd")) == "sha256-AAA="

    assert (
        asyncio.run(hash_mod.nix_prefetch_url("https://example.com")) == "sha256-AAA="
    )
    assert (
        asyncio.run(
            hash_mod.nix_prefetch_url(
                "https://example.com",
                name="safe-name.dmg",
            )
        )
        == "sha256-AAA="
    )
    assert prefetch_args == [
        [
            "nix",
            "store",
            "prefetch-file",
            "--json",
            "--hash-type",
            "sha256",
            "https://example.com",
        ],
        [
            "nix",
            "store",
            "prefetch-file",
            "--json",
            "--hash-type",
            "sha256",
            "--name",
            "safe-name.dmg",
            "https://example.com",
        ],
    ]
    seen_path_info_args: list[list[str]] = []

    async def _run_nix_json(args: list[str], **kwargs: object) -> object:
        timeout = kwargs.get("command_timeout")
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


def test_nix_path_info_reconciles_map_keys_and_explicit_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map keys fill absent or null paths and must agree with explicit paths."""
    list_payload = [{"path": "/nix/store/list", "narHash": "sha256-AAA=", "narSize": 1}]
    map_payload = {
        "/nix/store/key": {"narHash": "sha256-AAA=", "narSize": 1},
        "/nix/store/null-key": {
            "path": None,
            "narHash": "sha256-AAA=",
            "narSize": 1,
        },
        "/nix/store/equal": {
            "path": "/nix/store/equal",
            "narHash": "sha256-AAA=",
            "narSize": 1,
        },
    }
    payload = list_payload

    async def _run_nix_json(*_a: object, **_k: object) -> object:
        return payload

    monkeypatch.setattr(path_info_mod, "run_nix_json", _run_nix_json)
    listed = asyncio.run(path_info_mod.nix_path_info([]))
    assert [item.path for item in listed] == ["/nix/store/list"]

    payload = map_payload
    mapped = asyncio.run(path_info_mod.nix_path_info([]))
    assert [item.path for item in mapped] == [
        "/nix/store/key",
        "/nix/store/null-key",
        "/nix/store/equal",
    ]

    payload = {
        "/nix/store/conflicting-key": {
            "path": "/nix/store/conflicting-value",
            "narHash": "sha256-AAA=",
            "narSize": 1,
        }
    }
    with pytest.raises(ValueError, match="conflicts with explicit path") as caught:
        asyncio.run(path_info_mod.nix_path_info([]))
    assert "/nix/store/conflicting-key" in str(caught.value)
    assert "/nix/store/conflicting-value" in str(caught.value)


def test_nix_path_info_still_validates_malformed_map_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Key injection must not make non-object or malformed entries acceptable."""
    payload: object = {"/nix/store/bad": "not-an-object"}

    async def _run_nix_json(*_a: object, **_k: object) -> object:
        return payload

    monkeypatch.setattr(path_info_mod, "run_nix_json", _run_nix_json)
    with pytest.raises(ValidationError):
        asyncio.run(path_info_mod.nix_path_info([]))

    payload = {"/nix/store/bad": {"path": 42, "narHash": "sha256-AAA=", "narSize": 1}}
    with pytest.raises(ValidationError):
        asyncio.run(path_info_mod.nix_path_info([]))


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


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_store_callback_failure_closes_stream_and_preserves_primary_error(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cleanup_failure: bool,
) -> None:
    """Callback consumers close their stream without masking the callback error."""

    class _Stream:
        def __init__(self) -> None:
            self.closed = False
            self.sent = False

        def __aiter__(self) -> _Stream:
            return self

        async def __anext__(self) -> str:
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return "line"

        async def aclose(self) -> None:
            self.closed = True
            if cleanup_failure:
                raise RuntimeError("cleanup failed")

    stream = _Stream()
    monkeypatch.setattr(store_mod, "stream_nix", lambda *_a, **_k: stream)
    callback_error = RuntimeError("callback failed")

    def _on_line(_line: str) -> None:
        raise callback_error

    with pytest.raises(RuntimeError, match="callback failed") as caught:
        asyncio.run(
            store_mod.nix_store_realise(["/nix/store/demo.drv"], on_line=_on_line)
        )
    assert caught.value is callback_error
    assert stream.closed
    if cleanup_failure:
        assert any("cleanup failed" in note for note in caught.value.__notes__)


def test_store_cancellation_during_callback_cleanup_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation wins when it arrives while callback cleanup is in progress."""
    close_started = asyncio.Event()

    class _Stream:
        def __init__(self) -> None:
            self.closed = False
            self.sent = False

        def __aiter__(self) -> _Stream:
            return self

        async def __anext__(self) -> str:
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return "line"

        async def aclose(self) -> None:
            self.closed = True
            close_started.set()
            await asyncio.Event().wait()

    stream = _Stream()
    monkeypatch.setattr(store_mod, "stream_nix", lambda *_a, **_k: stream)

    def _on_line(_line: str) -> None:
        raise RuntimeError("callback failed")

    async def _exercise() -> None:
        task = asyncio.create_task(
            store_mod.nix_store_realise(["/nix/store/demo.drv"], on_line=_on_line)
        )
        await close_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(_exercise())
    assert stream.closed


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


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "{}",
        '{"hash":"bad"}',
        '{"hash":123,"storePath":"/nix/store/example"}',
        '{"hash":"sha256-AAA="}',
    ],
)
def test_prefetch_rejects_malformed_hash_result(
    monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    """Structured prefetch output must include an SRI hash before it can be saved."""

    async def _run_nix(args: list[str], **_kwargs: object) -> CommandResult:
        return CommandResult(args=args, returncode=0, stdout=payload, stderr="")

    monkeypatch.setattr(hash_mod, "run_nix", _run_nix)
    with pytest.raises(ValidationError):
        asyncio.run(hash_mod.nix_prefetch_url("https://example.invalid/archive"))
