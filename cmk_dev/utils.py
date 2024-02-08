#!/usr/bin/env python3

"""Common stuff shared among modules"""

import logging
from contextlib import suppress


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("cmk-dev.utils")


def value_from(raw_str: str) -> str | float | int:
    """Returns an int, a float or the raw input in this order"""
    with suppress(ValueError):
        return int(raw_str)
    with suppress(ValueError):
        return float(raw_str)
    return raw_str
