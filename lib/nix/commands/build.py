"""High-level async wrappers for ``nix build``.

Provides :func:`nix_build` for full builds with JSON result parsing and
:func:`nix_build_dry_run` for discovering which derivations would be built.
"""

import json
import re
from dataclasses import dataclass
from typing import cast

from pydantic import TypeAdapter

from lib.nix.models.build_result import BuildResult

from .base import HashMismatchError, NixCommandError, _resolve_timeout_alias, run_nix

_BUILD_RESULT_LIST = TypeAdapter(list[BuildResult])

_RE_DRV_PATH = re.compile(r"(/nix/store/[a-z0-9]{32}-[^\s]+\.drv)\b")


@dataclass(frozen=True)
class _BuildCommandOptions:
    impure: bool = False
    no_link: bool = True
    json_output: bool = True
    extra_args: list[str] | None = None


def _build_command_args(
    *,
    expr: str | None,
    installable: str | None,
    options: _BuildCommandOptions | None = None,
) -> list[str]:
    if (expr is None and installable is None) or (
        expr is not None and installable is not None
    ):
        msg = "Provide exactly one of expr or installable"
        raise ValueError(msg)

    effective_options = options or _BuildCommandOptions()

    impure = effective_options.impure
    no_link = effective_options.no_link
    json_output = effective_options.json_output
    extra_args_obj = effective_options.extra_args
    extra_cli_args: list[str] = []
    if isinstance(extra_args_obj, list):
        extra_cli_args.extend(item for item in extra_args_obj if isinstance(item, str))

    args: list[str] = ["nix", "build"]
    if json_output:
        args.append("--json")
    if impure:
        args.append("--impure")
    if no_link:
        args.append("--no-link")

    if expr is not None:
        args.extend(["--expr", expr])
    elif installable is not None:
        args.append(installable)

    args.extend(extra_cli_args)

    return args


async def nix_build(
    expr: str | None = None,
    installable: str | None = None,
    *,
    command_timeout: float = 2400.0,
    **kwargs: object,
) -> list[BuildResult]:
    """Run ``nix build`` and return parsed build results.

    Parameters
    ----------
    expr:
        A Nix expression to build (passed via ``--expr``).
        Mutually exclusive with *installable*.
    installable:
        A flake reference or store path to build (positional argument).
        Mutually exclusive with *expr*.
    command_timeout:
        Maximum wall-clock seconds before the process is killed.
    **kwargs:
        Supports ``impure=...``, ``no_link=...``, ``json_output=...``,
        ``extra_args=...`` and legacy ``timeout=...`` alias.

    Returns
    -------
    list[BuildResult]
        Parsed build results (empty when *json_output* is ``False``).

    Raises
    ------
    HashMismatchError
        A fixed-output derivation reported a hash mismatch.
    NixCommandError
        The build failed for any other reason.

    """
    remaining_kwargs = dict(kwargs)

    impure_obj = remaining_kwargs.pop("impure", False)
    if not isinstance(impure_obj, bool):
        msg = "impure must be a boolean"
        raise TypeError(msg)

    no_link_obj = remaining_kwargs.pop("no_link", True)
    if not isinstance(no_link_obj, bool):
        msg = "no_link must be a boolean"
        raise TypeError(msg)

    json_output_obj = remaining_kwargs.pop("json_output", True)
    if not isinstance(json_output_obj, bool):
        msg = "json_output must be a boolean"
        raise TypeError(msg)

    extra_args_obj = remaining_kwargs.pop("extra_args", None)
    if extra_args_obj is not None and (
        not isinstance(extra_args_obj, list)
        or not all(isinstance(item, str) for item in extra_args_obj)
    ):
        msg = "extra_args must be a list of strings"
        raise TypeError(msg)
    validated_extra_args = cast("list[str] | None", extra_args_obj)

    build_options = _BuildCommandOptions(
        impure=impure_obj,
        no_link=no_link_obj,
        json_output=json_output_obj,
        extra_args=validated_extra_args,
    )

    args = _build_command_args(
        expr=expr,
        installable=installable,
        options=build_options,
    )

    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=remaining_kwargs,
    )

    result = await run_nix(args, check=False, timeout=timeout_seconds)

    if result.returncode != 0:
        hash_err = HashMismatchError.from_stderr(result.stderr, result)
        if hash_err is not None:
            raise hash_err
        raise NixCommandError(result)

    if not build_options.json_output:
        return []

    parsed = json.loads(result.stdout)
    return _BUILD_RESULT_LIST.validate_python(parsed)


async def nix_build_dry_run(
    installable: str,
    *,
    impure: bool = True,
    command_timeout: float = 300.0,
    **kwargs: object,
) -> set[str]:
    """Run ``nix build --dry-run`` and return derivations that would be built.

    Parameters
    ----------
    installable:
        A flake reference or store path to dry-run build.
    impure:
        Pass ``--impure`` to allow access to environment variables and mutable
        paths. Defaults to ``True`` since CI detection relies on ``getEnv``.
    command_timeout:
        Maximum wall-clock seconds before the process is killed.
    **kwargs:
        Supports legacy ``timeout=...`` alias.

    Returns
    -------
    set[str]
        Store paths of ``.drv`` files that would be built.

    Raises
    ------
    NixCommandError
        The dry-run failed.

    """
    args = ["nix", "build", installable, "--dry-run"]
    if impure:
        args.append("--impure")
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    result = await run_nix(args, check=True, timeout=timeout_seconds)

    combined = result.stdout + result.stderr
    drvs: set[str] = set()

    in_build_section = False
    for line in combined.splitlines():
        if "will be built:" in line:
            in_build_section = True
            continue

        if in_build_section:
            match = _RE_DRV_PATH.search(line)
            if match:
                drvs.add(match.group(1))
            elif line.strip() == "" or not line.startswith(" "):
                # End of the "will be built" section.
                in_build_section = False

    return drvs
