#!/usr/bin/env python3

"""Checkmk DevOps tools - CLI

Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
"""

import logging
import sys
from argparse import ArgumentParser
from argparse import Namespace as Args
from pathlib import Path
from typing import Sequence, Union

import cmk_devops.binreplace

from .version import __version__


def parse_args(argv: Union[Sequence[str], None] = None) -> Args:
    """Cool git like multi command argument parser"""
    parser = ArgumentParser(__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")

    parser.set_defaults(func=lambda *_: parser.print_usage())
    subparsers = parser.add_subparsers(help="available commands", metavar="CMD")

    parser_help = subparsers.add_parser("help")
    parser_help.set_defaults(func=lambda *_: parser.print_help())

    parser_info = subparsers.add_parser("info")
    parser_info.set_defaults(
        func=fn_info,
        help="Prints information about cmk-devops-tools",
    )

    parser_binreplace = subparsers.add_parser("binreplace")
    parser_binreplace.set_defaults(
        func=fn_binreplace,
        help="Replaces strings in files binary-awarely",
    )
    cmk_devops.binreplace.apply_cli_arguments(parser_binreplace)

    subparsers.help = f"[{' '.join(str(c) for c in subparsers.choices)}]"

    return parser.parse_args(argv)


def logger() -> logging.Logger:
    """Named logger"""
    return logging.getLogger("trickkiste.cmk-dev")


def shorten_home(path: Union[Path, str]) -> Path:
    """Reverse of expanduser"""
    return Path(Path(path).as_posix().replace(str(Path.home()), "~"))


def fn_info(_args: Args) -> None:
    """Entry point `info`"""
    print(f"Version: {__version__} (at {shorten_home(Path(__file__).parent)})")
    print(f"Python: {'.'.join(map(str, sys.version_info[:3]))} (at {shorten_home(sys.executable)})")


def fn_binreplace(args: Args) -> None:
    """Entry function for cpumon"""
    cmk_devops.binreplace.main(args)


def main() -> int:
    """Entry point for everything else"""
    (args := parse_args()).func(args)
    return 0


if __name__ == "__main__":
    main()
