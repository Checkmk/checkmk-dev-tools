#!/usr/bin/env python3

"""Common stuff shared among modules"""

import asyncio
import logging
import os
import shlex
import signal
import sys
import time
import traceback
from datetime import datetime
from functools import wraps
from pathlib import Path
from subprocess import DEVNULL, check_output

# we need 3.8 compatible typing (python on build nodes)
from typing import AsyncIterator, Union, cast

from asyncinotify import Event, Inotify, Mask
from dateutil import tz

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper")


def setup_logging(level: str = "INFO") -> None:
    """Make logging fun"""
    for lev in LOG_LEVELS:
        logging.addLevelName(getattr(logging, lev), f"{lev[0] * 2}")

    logging.basicConfig(
        format=(
            "(%(levelname)s): %(message)s"
            if os.environ.get("USER") == "root"
            else "(%(levelname)s) %(asctime)s %(name)s: %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.DEBUG if level == "ALL_DEBUG" else logging.WARNING,
    )
    logging.getLogger().setLevel(getattr(logging, level.split("_")[-1]))
    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


def impatient(func):
    """Tells us, when a function takes suspiciously long"""

    @wraps(func)
    def run(*args: object, **kwargs: object) -> object:
        try:
            t1 = time.time()
            return func(*args, **kwargs)
        finally:
            if (duration := time.time() - t1) > 0.2:
                log().warn("%s took %.2fs!", func.__name__, duration)
            # log().info("%s took %.2fs!", func.__name__, duration)

    return run


def aimpatient(func):
    """Tells us, when a function takes suspiciously long"""

    @wraps(func)
    async def run(*args: object, **kwargs: object) -> object:
        try:
            t1 = time.time()
            return await func(*args, **kwargs)
        finally:
            if (duration := time.time() - t1) > 0.1:
                log().warning("%s took %.2fs!", func.__name__, duration)
            # log().info("%s took %.2fs!", func.__name__, duration)

    return run


async def fs_changes(
    *paths: Path,
    queue: asyncio.Queue = asyncio.Queue(),
    mask: Mask = Mask.CLOSE_WRITE | Mask.MOVED_TO | Mask.CREATE,
    postpone: bool = False,
    timeout: float = 2,
) -> AsyncIterator[Path]:
    """Controllable, timed filesystem watcher"""

    # pylint: disable=too-many-locals

    async def fuse_fn(queue: asyncio.Queue, timeout: float) -> None:
        await asyncio.sleep(timeout)
        await queue.put("timeout")

    def task(name: str) -> asyncio.Task:
        """Creates a task from a name identifying a data source to read from"""
        return asyncio.create_task(
            cast(Union[asyncio.Queue, Inotify], {"inotify": inotify, "mqueue": queue}[name]).get(),
            name=name,
        )

    with Inotify() as inotify:
        for path in paths:
            inotify.add_watch(path, mask)
        fuse = None
        changed_files = set()
        tasks = set(map(task, ("inotify", "mqueue")))

        while True:
            done, tasks = await asyncio.wait(
                fs=tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for event in done:
                event_type, event_value = event.get_name(), event.result()
                tasks.add(task(event_type))
                if event_type == "inotify":
                    assert isinstance(event_value, Event)
                    if event_value.path:
                        changed_files.add(event_value.path)
                    if postpone and fuse:
                        fuse.cancel()
                        del fuse
                        fuse = None
                    if not fuse:
                        fuse = asyncio.create_task(fuse_fn(queue, timeout))
                elif event_type == "mqueue":
                    if event_value == "timeout":
                        del fuse
                        fuse = None
                        for file in changed_files:
                            yield file
                        changed_files.clear()


async def read_process_output(cmd: str) -> AsyncIterator[str]:
    """Run a process asynchronously and handle each line on stdout using provided callback"""
    process = await asyncio.create_subprocess_exec(
        *shlex.split(cmd),
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout
    while True:
        if not (line := (await process.stdout.readline()).decode().strip("\n")):
            break
        yield line


def process_output(cmd: str) -> str:
    """Return command output as one blob"""
    return check_output(shlex.split(cmd), stderr=DEVNULL, text=True)


def dur_str(seconds: int, fixed=False) -> str:
    """Turns a number of seconds into a string like 1d:2h:3m"""
    if not fixed and not seconds:
        return "0s"
    digits = 2 if fixed else 1
    days = f"{seconds//86400:0{digits}d}d" if fixed or seconds >= 86400 else ""
    hours = (
        f"{seconds//3600%24:0{digits}d}h" if fixed or seconds >= 3600 and (seconds % 86400) else ""
    )
    minutes = f"{seconds//60%60:0{digits}d}m" if fixed or seconds >= 60 and (seconds % 3600) else ""
    seconds_str = (
        f"{seconds%60:0{digits}d}s" if not fixed and ((seconds % 60) or seconds == 0) else ""
    )
    return ":".join(e for e in (days, hours, minutes, seconds_str) if e)


def age_str(now: Union[int, datetime], age: Union[int, datetime, None], fixed: bool = False) -> str:
    """Turn a number of seconds into something human readable"""
    if age is None:
        return "--"
    return dur_str(
        int(
            (now.timestamp() if isinstance(now, datetime) else now)
            - (age.timestamp() if isinstance(age, datetime) else age)
        ),
        fixed=fixed,
    )


def date_str(date: datetime) -> str:
    if not date:
        return "--"
    return (date if isinstance(date, datetime) else datetime.fromtimestamp(date)).strftime(
        "%Y.%m.%d-%H:%M:%S"
    )


def date_from(timestamp: Union[int, float, str]) -> Union[None, datetime]:
    """
    2023-07-14T15:05:32.174200714+02:00
    """
    try:
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp)

        if timestamp[-1] == "Z":
            return (
                datetime.strptime(timestamp[:19], "%Y-%m-%dT%H:%M:%S")
                .replace(tzinfo=tz.tzutc())
                .astimezone(tz.tzlocal())
            )
        if len(timestamp) == 35:
            timestamp = timestamp[:19] + timestamp[-6:]
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
    except OverflowError:
        return None
    except Exception as exc:
        raise ValueError(f"Could not parse datetime from <{timestamp!r}> ({exc})") from exc


def increase_loglevel(*_):
    """Become one level more verbose.
    If level is already DEBUG we go back to WARNING.
    """
    try:
        new_level = {
            logging.WARNING: logging.INFO,
            logging.INFO: logging.DEBUG,
            logging.DEBUG: logging.WARNING,
        }.get(log().level) or logging.INFO

        log().setLevel(new_level)
        logging.getLogger("docker-shaper.server").setLevel(new_level)
        level = {
            logging.CRITICAL: "CRITICAL",
            logging.ERROR: "ERROR",
            logging.WARNING: "WARNING",
            logging.INFO: "INFO",
            logging.DEBUG: "DEBUG",
        }[new_level]
        print(f"increase_loglevel to {level}", file=sys.stderr)
    except Exception:
        log().exception("Could not fully write application stack trace")


def print_stacktrace_on_signal(sig, frame):
    """interrupt running process, and provide a python prompt for
    interactive debugging.
    see http://stackoverflow.com/questions/132058
       "showing-the-stack-trace-from-a-running-python-application"
    """
    try:
        print(f"signal {sig} received - print stack trace", file=sys.stderr)

        def print_stack_frame(stack_frame, file):
            for _f in traceback.format_stack(stack_frame):
                for _l in _f.splitlines():
                    print(_l, file=file)

        def print_stack_frames(file):
            print("++++++ MAIN ++++++++", file=file)
            print_stack_frame(frame, file)
            for task in asyncio.all_tasks():
                print(f"++++++ {task.get_coro().__name__} ++++++++", file=file)
                for stack in task.get_stack(limit=1000):
                    print_stack_frame(stack, file)

        print_stack_frames(sys.stderr)
        with open(Path("~/.docker_shaper/traceback.log").expanduser(), "w") as trace_file:
            print_stack_frames(trace_file)
    except Exception:
        log().exception("Could not fully write application stack trace")


def setup_introspection_on_signal():
    """Install signal handlers for some debug stuff"""

    def setup_signal(sig, func, msg):
        signal.signal(sig, func)
        signal.siginterrupt(sig, False)
        sig_str = {signal.SIGUSR1: "USR1", signal.SIGUSR2: "USR2"}.get(sig, sig)
        print(f"Run `kill -{sig_str} {os.getpid()}` to {msg}", file=sys.stderr)

    setup_signal(signal.SIGUSR1, increase_loglevel, "increase log level")
    setup_signal(signal.SIGUSR2, print_stacktrace_on_signal, "print stacktrace")
