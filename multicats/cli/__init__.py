"""The ``multicats`` command.

Each subcommand is a :class:`~multicats.cli.base.Command`. To add one, write
a module in this package that defines it, and register it in ``COMMANDS``.
"""

import argparse
from collections.abc import Sequence

from .base import Command, UsageError
from .schedule import COMMAND as SCHEDULE

COMMANDS: dict[str, Command] = {c.name: c for c in [SCHEDULE]}


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="multicats",
        description="Multiple-job optimisation for carbon intensity and green "
        + "scheduling on Slurm.",
    )
    subparsers = top.add_subparsers(
        title="commands", metavar="<command>", dest="command", required=True
    )
    for command in COMMANDS.values():
        sub = subparsers.add_parser(
            command.name, help=command.help, description=command.help
        )
        command.add_arguments(sub)
        sub.set_defaults(_command=command, _parser=sub)
    return top


def main(argv: Sequence[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        args._command.run(args)
    except UsageError as e:
        args._parser.error(str(e))


__all__ = ["COMMANDS", "Command", "UsageError", "main", "parser"]
