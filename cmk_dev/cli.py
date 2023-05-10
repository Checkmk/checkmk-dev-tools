#!/usr/bin/env python3

"""Checkmk DevOps tools - CLI
"""

import importlib.metadata
import logging
import sys
from argparse import ArgumentParser
from argparse import Namespace as Args
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path


def parse_args(argv: Sequence[str] | None = None) -> Args:
    """Cool git like multi command argument parser"""
    parser = ArgumentParser(__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")

    parser.set_defaults(func=lambda *_: parser.print_usage())
    subparsers = parser.add_subparsers(help="available commands", metavar="CMD")

    parser_info = subparsers.add_parser("help")
    parser_info.set_defaults(func=lambda *_: parser.print_help())

    parser_info = subparsers.add_parser("info")
    parser_info.set_defaults(func=fn_info)

    return parser.parse_args(argv)


def logger() -> logging.Logger:
    """Named logger"""
    return logging.getLogger("cmk-dev")


def extract_version() -> str:
    """Returns either the version of installed package or the one
    found in nearby pyproject.toml"""
    with suppress(FileNotFoundError, StopIteration):
        with open(
            Path(__file__).parent.parent / "pyproject.toml", encoding="utf-8"
        ) as pyproject_toml:
            version = (
                next(line for line in pyproject_toml if line.startswith("version"))
                .split("=")[1]
                .strip("'\"\n ")
            )
            return f"{version}-dev"
    return importlib.metadata.version("checkmk_dev_tools")


__version__ = extract_version()


def shorten_home(path: Path | str) -> Path:
    """Reverse of expanduser"""
    return Path(Path(path).as_posix().replace(str(Path.home()), "~"))


def fn_info(_args: Args) -> None:
    """Entry point `info`"""
    print(f"Version: {__version__} (at {shorten_home(Path(__file__).parent)})")
    print(
        f"Python: {'.'.join(map(str, sys.version_info[:3]))}"
        f" (at {shorten_home(sys.executable)})"
    )


def main() -> int:
    """Entry point for everything else"""
    (args := parse_args()).func(args)
    return 0


if __name__ == "__main__":
    main()
