#!/usr/bin/env python3

"""Docker Shaper command line interface
"""

from argparse import ArgumentParser
from argparse import Namespace as Args
from pathlib import Path

from docker_shaper import server
from docker_shaper.utils import setup_logging


def parse_args() -> Args:
    """Cool git like multi command argument parser"""
    parser = ArgumentParser()
    parser.add_argument(
        "--log-level",
        "-l",
        choices=["ALL_DEBUG", "DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"],
        help="Sets the logging level - ALL_DEBUG sets all other loggers to DEBUG, too",
        type=str.upper,
        default="INFO",
    )
    parser.set_defaults(func=lambda *_: parser.print_usage())
    subparsers = parser.add_subparsers(help="available commands", metavar="CMD")

    parser_serve = subparsers.add_parser("serve")
    parser_serve.set_defaults(func=fn_serve)

    parser_no_serve = subparsers.add_parser("no-serve")
    parser_no_serve.set_defaults(func=fn_no_serve)

    return parser.parse_args()


def fn_serve(args: Args) -> None:
    """Entry point for event consistency check"""
    setup_logging(args.log_level)
    server.serve()


def fn_no_serve(args: Args) -> None:
    """Entry point for event consistency check"""
    setup_logging(args.log_level)
    server.no_serve()


def main() -> int:
    """Entry point for everything else"""
    (args := parse_args()).func(args)
    return 0


if __name__ == "__main__":
    main()
