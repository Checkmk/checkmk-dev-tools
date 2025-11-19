#!/usr/bin/env python3
"""
Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
"""

import logging
import os
from argparse import ArgumentParser, Namespace
from datetime import datetime, timedelta
from pathlib import Path

from trickkiste.logging_helper import apply_common_logging_cli_args, setup_logging
from trickkiste.misc import parse_age


def log() -> logging.Logger:
    """Convenience function retrieves 'our' logger"""
    return logging.getLogger("trickkiste.last-access")


def apply_cli_arguments(parser: ArgumentParser) -> ArgumentParser:
    """Adds binreplace CLI arguments to @parser (for reusability)"""
    parser.add_argument("--filter-from", type=parse_age)
    parser.add_argument("--filter-until", type=parse_age)
    parser.add_argument("path", type=Path, nargs="+")
    return parser


def get_lastest_access(directory: Path) -> tuple[datetime, Path] | None:
    """Returns date of last access to a file in provided directory or None if it's empty"""
    # print(directory)
    list_of_files = [path for path in Path(directory).rglob("*") if path.is_file()]
    if not list_of_files:
        # print(directory, "empty")
        return None
    latest_file = max(list_of_files, key=os.path.getctime)
    latest_access = datetime.fromtimestamp(os.path.getctime(latest_file))
    log().debug(":(%s %s %s)", latest_access, directory, latest_file.relative_to(directory))
    return latest_access, latest_file


def main_fn(args: Namespace) -> None:
    """Prints <date> <path> <path-to-youngest-file> for each @path provided on args"""
    now = datetime.now()
    latest_access = {
        path: access_data
        for path in map(Path, args.path)
        if (access_data := get_lastest_access(path))
        for timestamp, most_recent_file in (access_data,)
        if args.filter_until is None or timestamp < (now - timedelta(seconds=args.filter_until))
        if args.filter_from is None or timestamp >= (now - timedelta(seconds=args.filter_from))
    }

    for path, (timestamp, most_recent_file) in sorted(latest_access.items(), key=lambda e: e[1]):
        print(f"{timestamp} {path} {most_recent_file.relative_to(path)}")


def main() -> None:
    """Generic entry point for main_fn"""
    setup_logging(log())
    apply_common_logging_cli_args(arg_parser := apply_cli_arguments(ArgumentParser()))
    main_fn(arg_parser.parse_args())


if __name__ == "__main__":
    main()
