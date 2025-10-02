#!/usr/bin/env python3

"""Starts a process and logs file access (later maybe more)

Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
"""

# pylint: disable=fixme

import logging
import re
import sys
from argparse import Namespace
from asyncio import CancelledError, StreamReader, create_subprocess_exec, gather, run
from asyncio.subprocess import PIPE
from collections.abc import AsyncIterable, Sequence
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TextIO

# _type: ignore[import-untyped]
# _type: ignore[no-any-unimported]
import aiofiles
import yaml
from ttrace import (
    OpenatType,
    StraceType,
    parse,
    sanatized_strace_lines,
    strace_output_path,
)


def log() -> logging.Logger:
    """Convenience function retrieves 'our' logger"""
    return logging.getLogger("trickkiste.cmk-dev.procmon")


def load_filter_pattern(file_path: str | Path) -> str:
    """Returns the content of the 'exclude' element from given YAML file"""
    with suppress(FileNotFoundError):
        with Path(file_path).expanduser().open(encoding="utf-8") as config_file:
            file_content = yaml.load(config_file, Loader=yaml.BaseLoader)
            pattern = (p.replace("~", str(Path("~").expanduser())) for p in file_content["exclude"])
            return rf"^({'|'.join(pattern)})$"
    return ""


async def extract_paths(
    stream: AsyncIterable[tuple[int, str, StraceType]],
) -> AsyncIterable[tuple[str, str, Path, str, int | None]]:
    """Traverses `openat` strace lines and yields file access"""
    fds = {}
    async for _line_nr, line, strace in stream:
        if strace.fname == "exited":
            continue
        assert strace.fname == "openat"
        openat = parse(OpenatType, strace.args)
        log().debug("%s", line)

        pid, location, raw_path_entry, flags, result_nr = (
            strace.pid,
            openat.position,
            openat.path,
            openat.flags,
            strace.result_nr,
        )
        if location == "AT_FDCWD" and (pid, location) not in fds:
            fds[(pid, location)] = Path()

        if (pid, location) not in fds:
            log().error("[pid=%s, fd=%s] not known, processing %s", pid, location, raw_path_entry)
            continue

        assert fds[(pid, location)].is_dir()

        path = (fds[(pid, location)] / raw_path_entry).resolve()
        if not (path.exists() or result_nr == -1):
            log().debug("%s does not exist but result was %d", path, result_nr)

        if path.is_dir():
            fds[(pid, str(result_nr))] = path

        yield line, location, path, flags, result_nr


async def process_strace_lines(filename: Path, out_file: TextIO) -> None:
    """For testability: provides content of a file to process_strace()"""
    filter_pattern = load_filter_pattern("~/.config/procmon-exclude.yaml")
    accessed_files = set()
    try:
        async with aiofiles.open(filename) as afp:
            async for line, location, path, flags, _result_nr in extract_paths(
                sanatized_strace_lines(afp)
            ):
                if not path.is_file():
                    continue
                path_str = path.as_posix()

                out_file.write(f"{path} | {line}\n")
                if not re.match(filter_pattern, path_str):
                    print(f"{path} ({location}, {flags})")
                    if path_str not in accessed_files:
                        accessed_files.add(path_str)
    finally:
        for file in sorted(accessed_files):
            print(file.replace(str(Path("~").expanduser()), "~"))


async def buffer_stream(stream: StreamReader, out_file: TextIO) -> None:
    """Records a given stream to a buffer line by line along with the source"""
    while line := (await stream.readline()).decode():
        out_file.write(line)


async def main_invoke(cmd: Sequence[str], _args: Namespace) -> None:
    """Runs a program using strace"""
    access_trace_file_path = (
        Path("~").expanduser()
        / f"access-log-{cmd[0]}-{datetime.now().strftime('%Y.%m.%d-%H%M')}.log"
    )
    with open(access_trace_file_path, "w", encoding="utf-8") as outfile:
        print(access_trace_file_path)
        outfile.write(f"{' '.join(cmd)}\n")

        with strace_output_path(None) as strace_output_file_path:
            full_cmd = (
                "strace",
                "--trace=openat",
                "--decode-pids=pidns",
                "--timestamps=unix,us",
                "--follow-forks",
                "--columns=0",
                "--abbrev=none",
                "-s",
                "65536",
                "-o",
                f"{strace_output_file_path}",
                *cmd,
            )
            process = await create_subprocess_exec(*full_cmd, stdout=PIPE, stderr=PIPE)
            assert process.stdout and process.stderr
            try:
                await gather(
                    buffer_stream(process.stdout, sys.stdout),
                    buffer_stream(process.stderr, sys.stderr),
                    process_strace_lines(strace_output_file_path, outfile),
                    process.wait(),
                )
            except (KeyboardInterrupt, CancelledError):
                pass
            finally:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            raise SystemExit(process.returncode)


def main() -> None:
    """Main entrypoint"""
    # args, command = parse_args()
    args: Namespace
    args, command = Namespace(), sys.argv[1:]

    run(main_invoke(command, args))


if __name__ == "__main__":
    main()
