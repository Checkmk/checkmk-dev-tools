#!/usr/bin/env python3

"""Does string search and replace on binary files, assuming 0-terminated strings

Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
"""
# Still missing:
# - treat text files differently

import re

# pylint: disable=too-many-arguments
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path


def apply_cli_arguments(parser: ArgumentParser) -> ArgumentParser:
    """Adds binreplace CLI arguments to @parser (for reusability)"""
    parser.add_argument("--dry-run", "-d", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--inplace", "-p", action="store_true")
    parser.add_argument("--stop-after-first", action="store_true")
    parser.add_argument("--regular-expression", "-E", action="store_true")
    parser.add_argument("--max-strlen", type=int, default=1000)
    parser.add_argument("source", type=str)
    parser.add_argument("destination", type=str)
    parser.add_argument("target", type=Path, nargs="+")
    return parser


def binary_replace(
    buffer: bytes,
    src: bytes,
    dst: bytes,
    regular_expression: bool,
    replace_all: bool,
    max_strlen: int,
) -> None | bytes:
    """Searches @buffer for @src and replaces it with @dst, keeping the same length and
    taking null termination into account
    >>> sequence_in = b"replace: ABCDdef\\x00but not this, and this: ABCD\\x00but not this"
    >>> sequence_out = binary_replace(sequence_in, b"A.*?D", b"abc", True, True, 1000)
    >>> print(sequence_out)
    b'replace: abcdef\\x00\\x00but not this, and this: abc\\x00\\x00but not this'
    >>> assert len(sequence_in) == len(sequence_out)
    """
    something_replaced = None
    if not (matches := list(re.finditer(src if regular_expression else re.escape(src), buffer))):
        return None
    for match in matches:
        found_start, found_end = match.span()
        if found_end - found_start < len(dst):
            print(
                f"Found string is shorter than destination: {buffer[found_start:found_end]!r}",
                file=sys.stderr,
            )
            raise SystemExit(-1)
        nul_pos = buffer.find(b"\x00", found_end)
        if 0 < (nul_pos - found_start) < max_strlen:
            print(f" @at 0x{found_start:x}: {buffer[found_start:nul_pos]!r}", file=sys.stderr)
            nul_padding = b"\x00" * (found_end - found_start - len(dst))
            replaced = dst + buffer[found_end:nul_pos] + nul_padding
            buffer = buffer[:found_start] + replaced + buffer[nul_pos:]
            something_replaced = buffer

        if not replace_all:
            break
    return something_replaced


def binreplace_file(
    src: str,
    dst: str,
    path: Path,
    regular_expression: bool,
    replace_all: bool,
    inplace: bool,
    max_strlen: int,
    dry_run: bool,
) -> bool:
    """I/O and error handling wrapper for binary_replace"""
    try:
        with path.open("rb") as binfile_in:
            # print(f"search {path}", file=sys.stderr)
            if not (
                replaced := binary_replace(
                    binfile_in.read(),
                    src.encode(),
                    dst.encode(),
                    regular_expression,
                    replace_all,
                    max_strlen,
                )
            ):
                return False
            if not dry_run:
                out_path = path if inplace else path.parent / f"{path.name}.replaced"
                with out_path.open("bw") as binfile_out:
                    binfile_out.write(replaced)
                    print(f"wrote {out_path}")
            else:
                print("found search string but skip writing due to dry run", file=sys.stderr)
            return True
    except PermissionError as exc:
        print(f"Could not open: {exc}", file=sys.stderr)
    return False


def binreplace(
    src: str,
    dst: str,
    *paths: str | Path,
    regular_expression: bool,
    replace_all: bool,
    inplace: bool,
    strict: bool,
    max_strlen: int,
    dry_run: bool = False,
) -> None:
    """Traverse"""
    if strict and (src[-1] == "/") != (dst[-1] == "/"):
        print("Search and destination strings must have inconstient '/'-endings", file=sys.stderr)
        raise SystemExit(-1)

    for path in map(Path, paths):
        print(f"search {path}")
        if not any(
            binreplace_file(
                src,
                dst,
                file_path.absolute(),
                regular_expression,
                replace_all,
                inplace,
                max_strlen,
                dry_run,
            )
            for file_path in (filter(Path.is_file, path.glob("**/*")) if path.is_dir() else (path,))
        ):
            print(f"Search string not found in {path}", file=sys.stderr)
            if strict:
                raise SystemExit(1)


def main(args: None | Namespace = None) -> None:
    """Main entry point"""
    used_args = args or apply_cli_arguments(ArgumentParser()).parse_args()
    binreplace(
        used_args.source,
        used_args.destination,
        *used_args.target,
        regular_expression=used_args.regular_expression,
        replace_all=not used_args.stop_after_first,
        inplace=used_args.inplace,
        strict=used_args.strict,
        max_strlen=used_args.max_strlen,
        dry_run=used_args.dry_run,
    )


if __name__ == "__main__":
    main()
