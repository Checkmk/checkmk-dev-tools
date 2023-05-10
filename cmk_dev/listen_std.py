#!/usr/bin/env python3

"""Run a command and complain only after a while
"""

import signal
import sys
from asyncio import Queue, StreamReader
from asyncio import TimeoutError as AsyncTimeoutError
from asyncio import create_subprocess_exec, gather, run, wait_for
from asyncio.subprocess import PIPE, Process
from contextlib import suppress
from typing import Optional, Sequence, TextIO

LineQueue = Queue[Optional[tuple[TextIO, bytes]]]


async def print_after(
    timeout: float,
    abort: Queue[bool],
    buffer: LineQueue,
) -> None:
    """Wait for a given time or until aborted - print buffer contents if appropriate"""
    with suppress(AsyncTimeoutError):
        if await wait_for(abort.get(), timeout):
            return
    while elem := await buffer.get():
        out_file, line = elem
        out_file.write(line.decode(errors="replace"))


async def buffer_stream(stream: StreamReader, buffer: LineQueue, out_file: TextIO) -> None:
    """Records a given stream to a buffer line by line along with the source"""
    while line := await stream.readline():
        await buffer.put((out_file, line))
    await buffer.put(None)


async def wait_and_notify(process: Process, abort: Queue[bool]) -> None:
    """Just waits for @process to finish and notify the result"""
    await process.wait()
    await abort.put(process.returncode == 0)


async def run_quiet_and_verbose(timeout: float, cmd: Sequence[str]) -> None:
    """Run a command and start printing it's output only after a given timeout"""
    buffer: LineQueue = Queue()
    abort: Queue[bool] = Queue()

    process = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)

    assert process.stdout
    assert process.stderr

    signal.signal(signal.SIGINT, lambda _sig, _frame: 0)

    await gather(
        print_after(float(timeout), abort, buffer),
        buffer_stream(process.stdout, buffer, sys.stdout),
        buffer_stream(process.stderr, buffer, sys.stderr),
        wait_and_notify(process, abort),
    )
    raise SystemExit(process.returncode)


def main() -> None:
    """Just the entrypoint for run_quiet_and_verbose()"""
    timeout, *cmd = sys.argv[1:]
    run(run_quiet_and_verbose(float(timeout), cmd))


if __name__ == "__main__":
    main()
