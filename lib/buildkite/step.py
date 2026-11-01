"""Command line for one generated Buildkite step.

``python -m lib.buildkite.step run <workflow> <job>`` executes that Actions
job. ``fanout``, ``aggregate``, and ``skip`` implement dynamic matrices.
"""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from lib.buildkite.execute import (
    BuildkiteAgent,
    aggregate,
    fanout,
    run_job,
    skip_job,
    subprocess_runner,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_COMMAND_ARGC = 3
_COMMANDS = {
    "run": run_job,
    "fanout": fanout,
    "aggregate": aggregate,
    "skip": skip_job,
}


def main(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> int:
    """Run one step command. Return 2 when the arguments are not a command."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != _COMMAND_ARGC or args[0] not in _COMMANDS:
        return 2
    values = dict(os.environ if env is None else env)
    root = Path(values.get("BUILDKITE_BUILD_CHECKOUT_PATH", Path.cwd()))
    agent = BuildkiteAgent(values, subprocess_runner)
    command, filename, job_id = args
    return _COMMANDS[command](root, filename, job_id, agent=agent, env=values)


if __name__ == "__main__":  # pragma: no cover -- delegate to the tested main
    sys.exit(main())
