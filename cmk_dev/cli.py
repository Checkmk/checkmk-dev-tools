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

import cmk_dev.binreplace
import cmk_dev.check_rpath
import cmk_dev.cpumon

# import cmk_dev.procmon
import cmk_dev.pycinfo


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
        help="Prints information about checkmk-dev-tools",
    )

    parser_howto = subparsers.add_parser("howto")
    parser_howto.set_defaults(func=fn_howto)
    parser_howto.add_argument(
        "topic", nargs="?", type=str, help="Provides HowTos to specific topics"
    )

    parser_dia = subparsers.add_parser("image-alias", aliases=["dia"])
    parser_dia.set_defaults(
        func=fn_dia,
        help="Operate on docker image aliases (DIA)",
    )

    parser_rpath = subparsers.add_parser("check-rpath", aliases=["rpath"])
    parser_rpath.set_defaults(
        func=fn_rpath,
        help="Checks and sets RPATH information of ELF specified binaries",
    )
    parser_rpath.add_argument(
        "path", nargs="?", type=Path, help="File or directory to check (recursively)"
    )

    parser_pycinfo = subparsers.add_parser("pycinfo")
    parser_pycinfo.set_defaults(
        func=fn_pycinfo,
        help="Shows content of pyc files",
    )
    parser_pycinfo.add_argument(
        "paths", nargs="*", type=Path, help="File(s) or directory(ies) to check (recursively)"
    )

    # parser_procmon = subparsers.add_parser("procmon")
    # parser_procmon.set_defaults(
    #     func=fn_procmon,
    #     help="Shows content of pyc files",
    # )

    parser_cpumon = subparsers.add_parser("cpumon")
    parser_cpumon.set_defaults(
        func=fn_cpumon,
        help="Shows content of pyc files",
    )
    parser_cpumon.add_argument(
        "cpus", type=str, help="Comma separated list of CPUs to monitor", nargs="?"
    )

    parser_laccess = subparsers.add_parser("last-access")
    parser_laccess.set_defaults(
        func=fn_laccess,
        help="Shows content of pyc files",
    )

    parser_npicked = subparsers.add_parser("not-picked")
    parser_npicked.set_defaults(
        func=fn_npicked,
        help="Shows content of pyc files",
    )

    parser_alisten = subparsers.add_parser("active-listen")
    parser_alisten.set_defaults(
        func=fn_alisten,
        help="Shows output of provided command only if needed",
    )

    parser_binreplace = subparsers.add_parser("binreplace")
    parser_binreplace.set_defaults(
        func=fn_binreplace,
        help="Replaces strings in files binary-awarely",
    )
    cmk_dev.binreplace.apply_cli_arguments(parser_binreplace)

    subparsers.help = f"[{' '.join(str(c) for c in subparsers.choices)}]"

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
    """Entry function for howto"""
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
    """Entry function for check-rpath"""
    cmk_dev.check_rpath.check_rpath(args.path)


def fn_pycinfo(args: Args) -> None:
    """Entry function for pycinfo"""
    cmk_dev.pycinfo.pycinfo(args.paths)


def fn_cpumon(args: Args) -> None:
    """Entry function for cpumon"""
    cmk_dev.cpumon.cpumon(args.cpus)


def fn_binreplace(args: Args) -> None:
    """Entry function for cpumon"""
    cmk_dev.binreplace.main(args)


def fn_dia(_args: Args) -> None:
    """Entry function for image-alias"""
    print("Noch nix")


def fn_procmon(_args: Args) -> None:
    """Entry function for procmon"""
    print("Noch nix")


def fn_laccess(_args: Args) -> None:
    """Entry function for last-access"""
    print("Noch nix")


def fn_npicked(_args: Args) -> None:
    """Entry function for not-picked"""
    print("Noch nix")


def fn_alisten(_args: Args) -> None:
    """Entry function for active-listen"""
    print("Noch nix")


def main() -> int:
    """Entry point for everything else"""
    (args := parse_args()).func(args)
    return 0


if __name__ == "__main__":
    main()
