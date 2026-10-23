"""Running Slurm client commands.

Slurm's client commands read and print times without a time zone, in the
local time zone of the process that runs them. MultiCATS runs them with
``TZ=UTC``, so those times are always UTC, whatever zone the host is in.
"""

import os
import subprocess
from typing import Any


def slurm_env() -> dict[str, str]:
    """The environment for Slurm commands: this process's, with ``TZ=UTC``."""
    return {**os.environ, "TZ": "UTC"}


def run_slurm(
    args: list[str], *, check: bool = False, **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    """Run a Slurm command with :func:`slurm_env`; ``check`` and ``kwargs``
    go to :func:`subprocess.run`."""
    return subprocess.run(args, check=check, env=slurm_env(), text=True, **kwargs)
