"""Building blocks for ``multicats`` subcommands."""

import argparse
from collections.abc import Callable
from dataclasses import dataclass


class UsageError(Exception):
    """The command-line options are invalid or incomplete.

    ``multicats`` reports it like an argparse error: usage line, message,
    exit status 2.
    """


@dataclass(frozen=True)
class Command:
    """One ``multicats`` subcommand."""

    name: str
    help: str  # one line, shown in ``multicats --help``
    add_arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], None]
