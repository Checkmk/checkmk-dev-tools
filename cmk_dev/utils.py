#!/usr/bin/env python3

"""Common stuff shared among modules"""

import hashlib
import logging
import os
import shlex
import sys
import traceback
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from subprocess import DEVNULL, check_output

## we need 3.8 compatible typing (python on build nodes)
from typing import Iterator, Union

from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape as markup_escape

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("cmk-dev.utils")


def stack_str(depth: int = 0) -> str:
    """Returns a short local function call stack"""

    def stack_fns() -> Iterator[str]:
        stack = list(
            reversed(
                traceback.extract_stack(sys._getframe(depth))  # pylint: disable=protected-access
            )
        )

        for site in stack:
            if site.filename != stack[0].filename or site.name == "<module>":
                break
            yield site.name

    return ">".join(reversed(list(stack_fns())))


def setup_logging(logger: logging.Logger, level: str = "INFO") -> None:
    """Make logging fun"""

    class CustomLogger(logging.getLoggerClass()):  # type: ignore[misc]
        """Injects the 'stack' element"""

        def makeRecord(self, *args: object, **kwargs: object) -> logging.LogRecord:
            """Adds 'stack' element to given record"""
            kwargs.setdefault("extra", {})["stack"] = stack_str(5)  # type: ignore[index]
            return super().makeRecord(*args, **kwargs)  # type: ignore[no-any-return]

    logging.setLoggerClass(CustomLogger)

    if not logging.getLogger().hasHandlers():
        logging.getLogger().setLevel(logging.WARNING)
        shandler = RichHandler(
            show_time=False,
            show_path=False,
            markup=True,
            console=Console(
                stderr=True, color_system="standard" if os.environ.get("FORCE_COLOR") else "auto"
            ),
        )
        logging.getLogger().addHandler(shandler)
        shandler.setLevel(getattr(logging, level.split("_")[-1]))
        shandler.setFormatter(logging.Formatter("│ [grey]%(name)-15s[/] │ [bold]%(message)s[/]"))

        # logging.basicConfig(
        #   format="%(name)s %(levelname)s: %(message)s",
        #   datefmt="%Y-%m-%d %H:%M:%S",
        #   level=logging.DEBUG if level == "ALL_DEBUG" else logging.WARNING,
        # )

        def markup_escaper(record: logging.LogRecord) -> bool:
            record.args = record.args and tuple(
                markup_escape(arg) if isinstance(arg, str) else arg for arg in record.args
            )
            record.msg = markup_escape(record.msg)
            return True

        shandler.addFilter(markup_escaper)

    # for lev in LOG_LEVELS:
    #    logging.addLevelName(getattr(logging, lev), f"{lev[0] * 2}")

    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
    logger.setLevel(getattr(logging, level.split("_")[-1]))


def md5from(filepath: Path) -> Union[str, None]:
    """Returns an MD5 sum from contents of file provided"""
    with suppress(FileNotFoundError):
        with open(filepath, "rb") as input_file:
            file_hash = hashlib.md5()
            while chunk := input_file.read(1 << 16):
                file_hash.update(chunk)
            return file_hash.hexdigest()
    return None


@contextmanager
def cwd(path: Path) -> Iterator[None]:
    """Changes working directory and returns to previous on exit."""
    prev_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev_cwd)


def process_output(cmd: str) -> str:
    """Return command output as one blob"""
    return check_output(shlex.split(cmd), stderr=DEVNULL, text=True)


def compact_dict(
    mapping: Mapping[str, float | str], *, maxlen: None | int = 10, delim: str = ", "
) -> str:
    """Turns a dict into a 'string packed map' (for making a dict human readable)
    >>> compact_dict({'foo': '23', 'bar': '42'})
    'foo=23, bar=42'
    """

    def short(string: str) -> str:
        return string if maxlen is None or len(string) <= maxlen else f"{string[:maxlen-2]}.."

    return delim.join(
        f"{k}={short_str}" for k, v in mapping.items() if (short_str := short(str(v)))
    )


def value_from(raw_str: str) -> str | float | int:
    """Returns an int, a float or the raw input in this order"""
    with suppress(ValueError):
        return int(raw_str)
    with suppress(ValueError):
        return float(raw_str)
    return raw_str
