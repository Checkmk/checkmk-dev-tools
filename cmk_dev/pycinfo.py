#!/usr/bin/env python3
"""
Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.

https://stackoverflow.com/questions/11141387/given-a-python-pyc-file-is-there-a-tool-that-let-me-view-the-bytecode

python3 -v src/main.py 2>&1 \
    | egrep 'code object from |bytecode is stale|No such file or directory' \
    | egrep --color 'code object from.*py$|No such file or directory|'

python3 -m compileall --invalidation-mode=checked-hash src
"""
# pylint: disable=too-many-locals
# pylint: disable=too-many-branches
# pylint: disable=too-many-statements


import binascii
import dis
import marshal
import struct
import sys
import time
from collections.abc import Iterable
from datetime import datetime
from importlib.util import source_hash
from pathlib import Path

try:
    from rich import print  # pylint: disable=redefined-builtin

    TAG_NONE = "[default]"
    TAG_OK = "[green]"
    TAG_WARNING = "[bold yellow]"
    TAG_ERROR = "[bold red]"
    TAG_CLOSE = "[/]"
except ModuleNotFoundError:
    TAG_NONE = ""
    TAG_OK = ""
    TAG_WARNING = ""
    TAG_ERROR = ""
    TAG_CLOSE = ""


def view_pyc_file(path: Path, strip: str = "", verbose: bool = False) -> None:
    """Read and display a content of the Python`s bytecode in a pyc-file."""

    assert sys.version_info.major == 3 and sys.version_info.minor >= 7  # noqa: PLR2004
    with path.open("rb") as file:
        _magic = binascii.hexlify(file.read(4)).decode("utf-8")
        timestamp = None
        file_hash = None
        size = None
        code = None
        error_str = None
        level = 0

        bit_field = int.from_bytes(file.read(4), byteorder=sys.byteorder)
        if 1 & bit_field == 1:
            file_hash = file.read(8)
        else:
            tstruct = time.localtime(struct.unpack("I", file.read(4))[0])
            timestamp = datetime(
                tstruct.tm_year,
                tstruct.tm_mon,
                tstruct.tm_mday,
                tstruct.tm_hour,
                tstruct.tm_min,
                tstruct.tm_sec,
                0,
            )
            size = struct.unpack("I", file.read(4))[0]
        try:
            code = marshal.load(file)
        except ValueError as exc:
            error_str = f"could not load code object: {exc}"
            level = max(level, 1)

    # assert path.parent.name == "__pycache__"
    source_dir = path.parent.parent.absolute()
    py_file_path = source_dir / f"{path.stem[:path.stem.find('.cpython')]}.py"

    if not py_file_path.exists():
        level = max(level, 2)

    co_filename = code and Path(code.co_filename)
    tag_co_filename = TAG_NONE
    if co_filename:
        path_from_co_filename = (
            co_filename
            if co_filename.is_absolute()
            else Path().joinpath(*py_file_path.parts[: -len(co_filename.parts)]) / co_filename
        )
        if path_from_co_filename != py_file_path:
            tag_co_filename = TAG_WARNING
            level = max(level, 1)
        if not path_from_co_filename.exists():
            tag_co_filename = TAG_WARNING
            level = max(level, 1)
    else:
        path_from_co_filename = None
        tag_co_filename = TAG_WARNING
        level = max(level, 1)

    if timestamp:
        timestamp_str = str(timestamp)
        if py_file_path.exists() and int(timestamp.timestamp()) == int(
            py_file_path.lstat().st_mtime
        ):
            tag_timestamp = TAG_OK
        else:
            tag_timestamp = TAG_WARNING
            level = max(level, 1)
    else:
        tag_timestamp = TAG_NONE
        timestamp_str = "-" * 19

    if size is not None:
        size_str = f"{size:7d}"
        if py_file_path.exists() and size == py_file_path.lstat().st_size:
            tag_size = TAG_OK
        else:
            tag_size = TAG_WARNING
            level = max(level, 1)
    else:
        tag_size = TAG_NONE
        size_str = "-------"

    tag_hashstr = TAG_NONE
    if file_hash is not None:
        with py_file_path.open("rb") as source_file:
            actual_hash = source_hash(source_file.read())
        tag_hashstr = TAG_OK if file_hash == actual_hash else TAG_WARNING
        hash_str = binascii.hexlify(file_hash).decode("utf-8")
    else:
        hash_str = "----------------"

    tag_level = TAG_NONE if level < 1 else TAG_WARNING if level < 2 else TAG_ERROR  # noqa: PLR2004

    # assert path.absolute().as_posix().startswith(strip)
    pyc_path_str = path.absolute().as_posix()[len(strip) :] if strip else path.as_posix()
    print(
        f"{pyc_path_str:110s}"
        f" | {tag_level}{'OK' if level < 1 else 'WARN' if level < 2 else 'ERROR':5s}{TAG_CLOSE}"  # noqa: PLR2004
        # f" | magic: {magic}"
        # f" | bitfield: {bit_field}"
        f" | timestamp: {tag_timestamp}{timestamp_str}{TAG_CLOSE}"
        # f" | {tag_timestamp}{timestamp.timestamp()}{TAG_CLOSE}"
        # f" | {tag_timestamp}{py_file_path.lstat().st_mtime}{TAG_CLOSE}"
        f" | size: {tag_size}{size_str}{TAG_CLOSE}"
        f" | hash: {tag_hashstr}{hash_str}{TAG_CLOSE}"
        f" | co_filename: {tag_co_filename}{co_filename or error_str}{TAG_CLOSE}"
    )

    if code and verbose:
        dis.disassemble(code)  # long output

        for attr_name in (
            "co_filename",
            "co_argcount",
            "co_firstlineno",
            "co_flags",
            "co_kwonlyargcount",
            "co_nlocals",
            "co_posonlyargcount",
            "co_stacksize",
            # "co_freevars",
            # "co_varnames",
            # "co_cellvars",
            # "co_consts",
            # "co_names",
        ):
            print(f"{attr_name}: {getattr(code, attr_name)}")


def pycinfo(paths: Iterable[str | Path]) -> None:
    """Iterates over provided paths and calls actual pyc visualization"""
    for path in map(Path, paths):
        if path.is_dir():
            for file_path in path.glob("**/*.pyc"):
                view_pyc_file(file_path, f"{path.absolute().as_posix()}/")
        else:
            view_pyc_file(path)


def main() -> None:
    """Main entry point"""
    pycinfo(sys.argv[1:])


if __name__ == "__main__":
    main()
