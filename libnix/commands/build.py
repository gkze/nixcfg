"""High-level async wrappers for ``nix build``.

Provides :func:`nix_build` for full builds with JSON result parsing and
:func:`nix_build_dry_run` for discovering which derivations would be built.
"""

import json
import re

from pydantic import TypeAdapter

from libnix.models.build_result import BuildResult

from .base import HashMismatchError, NixCommandError, run_nix

_BUILD_RESULT_LIST = TypeAdapter(list[BuildResult])

_RE_DRV_PATH = re.compile(r"(/nix/store/[a-z0-9]{32}-[^\s]+\.drv)\b")


def _build_command_args(  # noqa: PLR0913
    *,
    expr: str | None,
    installable: str | None,
    impure: bool,
    no_link: bool,
    json_output: bool,
    extra_args: list[str] | None,
) -> list[str]:
    if (expr is None and installable is None) or (
        expr is not None and installable is not None
    ):
        msg = "Provide exactly one of expr or installable"
        raise ValueError(msg)

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

    if extra_args:
        args.extend(extra_args)

    return args


async def nix_build(  # noqa: PLR0913
    expr: str | None = None,
    installable: str | None = None,
    *,
    impure: bool = False,
    no_link: bool = True,
    json_output: bool = True,
    extra_args: list[str] | None = None,
    timeout: float = 1200.0,  # noqa: ASYNC109
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
    impure:
        Pass ``--impure`` to allow access to mutable paths.
    no_link:
        Pass ``--no-link`` to skip creating a ``./result`` symlink.
    json_output:
        Pass ``--json`` and parse the structured output. When ``False``
        the build still runs but an empty list is returned.
    extra_args:
        Additional CLI flags forwarded verbatim to ``nix build``.
    timeout:
        Maximum wall-clock seconds before the process is killed.

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
    args = _build_command_args(
        expr=expr,
        installable=installable,
        impure=impure,
        no_link=no_link,
        json_output=json_output,
        extra_args=extra_args,
    )

    result = await run_nix(args, check=False, timeout=timeout)

    if result.returncode != 0:
        hash_err = HashMismatchError.from_stderr(result.stderr, result)
        if hash_err is not None:
            raise hash_err
        raise NixCommandError(result)

    if not json_output:
        return []

    parsed = json.loads(result.stdout)
    return _BUILD_RESULT_LIST.validate_python(parsed)


async def nix_build_dry_run(
    installable: str,
    *,
    impure: bool = True,
    timeout: float = 300.0,  # noqa: ASYNC109
) -> set[str]:
    """Run ``nix build --dry-run`` and return derivations that would be built.

    Parameters
    ----------
    installable:
        A flake reference or store path to dry-run build.
    impure:
        Pass ``--impure`` to allow access to environment variables and mutable
        paths. Defaults to ``True`` since CI detection relies on ``getEnv``.
    timeout:
        Maximum wall-clock seconds before the process is killed.

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
    result = await run_nix(args, check=True, timeout=timeout)

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
