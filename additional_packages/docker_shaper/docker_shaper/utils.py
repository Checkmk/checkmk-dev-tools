#!/usr/bin/env python3

"""Common stuff shared among modules"""

import asyncio
import logging
import os
import re
import signal
import sys
import time
import traceback
from datetime import datetime
from functools import wraps
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from dateutil import tz
from rich.logging import RichHandler

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper")


def stack_str(depth: int = 0):
    def stack_fns():
        stack = list(reversed(traceback.extract_stack(sys._getframe(depth))))
        for site in stack:
            if site.filename != stack[0].filename or site.name == "<module>":
                break
            yield site.name

    return ">".join(reversed(list(stack_fns())))


def setup_logging(level: str = "INFO") -> None:
    """Make logging fun"""

    class CustomLogger(logging.getLoggerClass()):
        """Logger with stack information"""

        def makeRecord(
            self, name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None
        ):
            if extra is None:
                extra = {}
            extra["stack"] = stack_str(5)
            return super().makeRecord(name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)

    logging.setLoggerClass(CustomLogger)

    logging.getLogger().setLevel(logging.WARNING)
    log().setLevel(getattr(logging, level.split("_")[-1]))
    # logging.getLogger("urllib3.connectionpool")
    ch = RichHandler(show_path=False, markup=True, show_time=False)
    ch.setLevel(getattr(logging, level.split("_")[-1]))
    ch.setFormatter(
        logging.Formatter(
            "│ %(asctime)s | [grey]%(stack)-55s[/] │ [bold white]%(message)s[/]",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    log().handlers = [ch]
    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)

    # https://stackoverflow.com/questions/76788727/how-can-i-change-the-debug-level-and-format-for-the-quart-i-e-hypercorn-logge
    # https://pgjones.gitlab.io/hypercorn/how_to_guides/logging.html#how-to-log
    # https://www.phind.com/agent?cache=clkqhh48y001smg0832tvq1rl

    # from quart.logging import default_handler
    # logging.getLogger('quart.app').removeHandler(default_handler)
    # logger = logging.getLogger("hypercorn.error")
    # logger.removeHandler(default_handler)
    # logger.addHandler(ch)
    # logger.setLevel(logging.WARNING)
    # logger.propagate = False


def impatient(func):
    """Tells us, when a function takes suspiciously long"""

    @wraps(func)
    def run(*args: object, **kwargs: object) -> object:
        t1 = time.time()
        try:
            return func(*args, **kwargs)
        finally:
            if (duration := time.time() - t1) > 0.2:
                log().warning("%s took %.2fs!", func.__name__, duration)

    return run


def aimpatient(func):
    """Tells us, when a function takes suspiciously long"""

    @wraps(func)
    async def run(*args: object, **kwargs: object) -> object:
        t1 = time.time()
        try:
            return await func(*args, **kwargs)
        finally:
            if (duration := time.time() - t1) > 0.1:
                log().warning("%s took %.2fs!", func.__name__, duration)

    return run


def dur_str(seconds: float, fixed=False) -> str:
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


def age_str(now: float | datetime, age: None | int | datetime, fixed: bool = False) -> str:
    """Turn a number of seconds into something human readable"""
    if age is None:
        return "--"
    start = age.timestamp() if isinstance(age, datetime) else age
    if (now if age == 0 else start) <= 0.0:
        return "--"
    return dur_str(
        int((now.timestamp() if isinstance(now, datetime) else now) - start),
        fixed=fixed,
    )


def date_str(date: int | datetime) -> str:
    """Create a uniform time string from a timestamp or a datetime"""
    if not date:
        return "--"
    date_dt = date if isinstance(date, datetime) else datetime.fromtimestamp(date)
    if date_dt.year < 1000:
        return "--"
    return (date_dt).strftime("%Y.%m.%d-%H:%M:%S")


def date_from(timestamp: int | float | str) -> None | datetime:
    """
    >>> str(date_from("2023-07-14T15:05:32.174200714+02:00"))
    '2023-07-14 15:05:32+02:00'
    >>> str(date_from("2023-07-24T21:25:26.89389821+02:00"))
    '2023-07-24 21:25:26+02:00'
    """
    try:
        if isinstance(timestamp, datetime):
            return timestamp

        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp)

        if timestamp[-1] == "Z":
            return (
                datetime.strptime(timestamp[:19], "%Y-%m-%dT%H:%M:%S")
                .replace(tzinfo=tz.tzutc())
                .astimezone(tz.tzlocal())
            )
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+\d{2}:\d{2}$", timestamp):
            timestamp = timestamp[:19] + timestamp[-6:]
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S%z")
    except OverflowError:
        return None
    except Exception as exc:  # pylint: disable=broad-except
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
    except Exception:  # pylint: disable=broad-except
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
        with open(
            Path("~/.docker_shaper/traceback.log").expanduser(), "w", encoding="utf-8"
        ) as trace_file:
            print_stack_frames(trace_file)
            print(f"traceback also written to {trace_file}", file=sys.stderr)
    except Exception:  # pylint: disable=broad-except
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


def load_module(path: Path) -> ModuleType:
    """Loads a module from a file path"""
    spec = spec_from_file_location("dynamic_config", path)
    if not (spec and spec.loader):
        raise RuntimeError("Could not load")
    module = module_from_spec(spec)
    assert module
    # assert isinstance(spec.loader, SourceFileLoader)
    loader: SourceFileLoader = spec.loader
    loader.exec_module(module)
    return module
