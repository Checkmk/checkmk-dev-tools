#!/usr/bin/env python3

"""Common stuff shared among modules"""

import hashlib
import logging
import os
import shlex
import sys
import traceback
from contextlib import contextmanager, suppress
from pathlib import Path
from subprocess import DEVNULL, check_output

## we need 3.8 compatible typing (python on build nodes)
from typing import Iterator, Union

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("cmk-dev.utils")


def stack_str(depth: int = 0) -> str:
    """Returns a short local function call stack"""

    def stack_fns() -> Iterator[str]:
        stack = list(reversed(traceback.extract_stack(sys._getframe(depth))))
        for site in stack:
            if site.filename != stack[0].filename or site.name == "<module>":
                break
            yield site.name

    return ">".join(reversed(list(stack_fns())))


def setup_logging(logger: logging.Logger, level: str = "INFO") -> None:
    """Make logging fun"""

    class CustomLogger(logging.getLoggerClass()):
        """Injects the 'stack' element"""

        def makeRecord(self, *args, **kwargs):
            kwargs.setdefault("extra", {})["stack"] = stack_str(5)
            return super().makeRecord(*args, **kwargs)

    logging.setLoggerClass(CustomLogger)

    if not logging.getLogger().hasHandlers():
        # ch.setLevel(getattr(logging, level.split("_")[-1]))
        shandler = logging.StreamHandler()
        shandler.setFormatter(
            logging.Formatter(
                "(%(levelname)s) %(asctime)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        logging.getLogger().addHandler(shandler)

        # logging.basicConfig(
        # format="%(name)s %(levelname)s: %(message)s",
        # datefmt="%Y-%m-%d %H:%M:%S",
        # level=logging.DEBUG if level == "ALL_DEBUG" else logging.WARNING,
        # )

    for lev in LOG_LEVELS:
        logging.addLevelName(getattr(logging, lev), f"{lev[0] * 2}")

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
