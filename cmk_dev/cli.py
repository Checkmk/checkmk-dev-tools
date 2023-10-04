#!/usr/bin/env python3

"""Checkmk DevOps tools - CLI
"""

import importlib.metadata
import logging
import sys
from argparse import ArgumentParser
from argparse import Namespace as Args
from contextlib import suppress
from pathlib import Path
from typing import Sequence, Union


def parse_args(argv: Union[Sequence[str], None] = None) -> Args:
    """Cool git like multi command argument parser"""
    parser = ArgumentParser(__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")

    parser.set_defaults(func=lambda *_: parser.print_usage())
    subparsers = parser.add_subparsers(help="available commands", metavar="CMD")

    parser_info = subparsers.add_parser("help")
    parser_info.set_defaults(func=lambda *_: parser.print_help())

    parser_info = subparsers.add_parser("info")
    parser_info.set_defaults(func=fn_info, help="Prints information about checkmk-dev-tools")

    parser_dia = subparsers.add_parser("image-alias", aliases=["dia"])
    parser_dia.set_defaults(func=fn_dia, help="Operate on docker image aliases (DIA)")

    parser_howto = subparsers.add_parser("howto")
    parser_howto.set_defaults(func=fn_howto)
    parser_howto.add_argument(
        "topic", nargs="?", type=str, help="Provides HowTos to specific topics"
    )

    parser_rpath = subparsers.add_parser("rpath")
    parser_rpath.set_defaults(
        func=fn_rpath,
        help="Checks and sets RPATH information of ELF binaries found recursively at provided directory",
    )

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


def shorten_home(path: Union[Path, str]) -> Path:
    """Reverse of expanduser"""
    return Path(Path(path).as_posix().replace(str(Path.home()), "~"))


def fn_info(_args: Args) -> None:
    """Entry point `info`"""
    print(f"Version: {__version__} (at {shorten_home(Path(__file__).parent)})")
    print(
        f"Python: {'.'.join(map(str, sys.version_info[:3]))}"
        f" (at {shorten_home(sys.executable)})"
    )


def fn_howto(args: Args) -> None:
    topics = {
        "new-distro": """
        Please look here for now:
        https://wiki.lan.tribe29.com/books/how-to/page/how-to-integrate-a-new-linux-distribution-in-9-simple-steps
        """,
        "testing": """
        Please look here for now:
        https://wiki.lan.tribe29.com/books/how-to/page/how-to-test-checkmk
        """,
        "setup-system": """
        Please look here for now:
        https://wiki.lan.tribe29.com/books/how-to/page/how-to-install-and-manage-multiple-python-versions
        """,
        "werkflow": """
        - werk fetch
        - review
        - test locally
        - pre-commit
        - format
        """,
        "setup-git": """
        https://wiki.lan.tribe29.com/books/how-to/page/how-to-work-with-git-worktree
        """,
        "docker": """
        https://wiki.lan.tribe29.com/books/how-to/page/how-to-work-locally-with-our-build-containers
        """,
        "Pipfile.lock": """
        scripts/run-in-docker.sh make --what-if Pipfile Pipfile.lock
        """,
    }
    print(
        topics.get(
            args.topic, f"Please choose one of the available topics: {', '.join(topics.keys())}"
        )
    )


def fn_rpath(args: Args) -> None:
    print("Noch nix")


def fn_dia(args: Args) -> None:
    print("Noch nix")


def main() -> int:
    """Entry point for everything else"""
    (args := parse_args()).func(args)
    return 0


if __name__ == "__main__":
    main()
