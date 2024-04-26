#!/usr/bin/env python3

"""Common stuff shared among modules"""

import asyncio
import logging
import sys
import traceback
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper")


def increase_loglevel():
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
    except Exception:  # pylint: disable=broad-except
        log().exception("Could not fully write application stack trace")


def dump_stacktrace(*out_fns) -> None:
    """interrupt running process, and provide a python prompt for
    interactive debugging.
    see http://stackoverflow.com/questions/132058
       "showing-the-stack-trace-from-a-running-python-application"
    """

    def print_stack_frame(stack_frame, out_fn):
        for _f in traceback.format_stack(stack_frame):
            for _l in _f.splitlines():
                out_fn(_l)

    def print_stack_frames(out_fn):
        out_fn("++++++ MAIN ++++++++")
        print_stack_frame(sys._getframe().f_back, out_fn)
        for task in asyncio.all_tasks():
            out_fn(f"++++++ {task.get_coro().__name__} ++++++++")
            for stack in task.get_stack(limit=1000):
                print_stack_frame(stack, out_fn)

    for out_fn in out_fns:
        print_stack_frames(out_fn)


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


def get_hostname() -> str:
    """Returns local hostname read from /etc/hostname"""
    with open("/etc/hostname", encoding="utf-8") as hostname_file:
        return hostname_file.read().strip()
