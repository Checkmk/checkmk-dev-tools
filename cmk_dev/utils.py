#!/usr/bin/env python3

"""
Common stuff shared among modules

Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
"""

import logging
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path


class Fatal(RuntimeError):
    """Rien ne va plus - thrown if process cannot continue but still should terminate
    with a decent error message."""


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("trickkiste.cmk-dev.utils")


def value_from(raw_str: str) -> str | float | int:
    """Returns an int, a float or the raw input in this order"""
    with suppress(ValueError):
        return int(raw_str)
    with suppress(ValueError):
        return float(raw_str)
    return raw_str


def distro_code(distro_name: str) -> str:
    """Maps Checkmk-internal way to identify release versions of Linux distributions to
    their 'human readable' version code"""
    # should rather go to checkmk/versions.py or somewhere
    return {
        "debian-10": "buster",
        "debian-11": "bullseye",
        "debian-12": "bookworm",
        "ubuntu-20.04": "focal",
        "ubuntu-22.04": "jammy",
        "ubuntu-23.04": "lunar",
        "ubuntu-23.10": "mantic",
        "ubuntu-24.04": "noble",
        "centos-8": "el8",
        "almalinux-9": "el9",
        "sles-15sp3": "sles15sp3",
        "sles-15sp4": "sles15sp4",
        "sles-12sp5": "sles12sp5",
        "sles-15sp5": "sles15sp5",
    }[distro_name]


def current_os_name() -> str:  # pylint: disable=too-many-return-statements
    """Returns codename for current OS"""

    def _read_os_release() -> Mapping[str, str]:
        with suppress(FileNotFoundError):
            with Path("/etc/os-release").open(encoding="utf-8") as filep:
                return {
                    key: raw_val.strip('"')
                    for line in filep
                    if "=" in line
                    for key, raw_val in (line.strip().split("=", 1),)
                }
        return {}

    def _read_redhat_release() -> str:
        with suppress(FileNotFoundError):
            with Path("/etc/redhat-release").open(encoding="utf-8") as filep:
                return filep.read().strip()
        return ""

    if redhat_release := _read_redhat_release():
        if redhat_release.startswith("CentOS release 6"):
            return "el6"
        if redhat_release.startswith("CentOS Linux release 7"):
            return "el7"
        if redhat_release.startswith("CentOS Linux release 8"):
            return "el8"
        if redhat_release.startswith("AlmaLinux release 9"):
            return "el9"
        raise NotImplementedError()

    if not (os_release := _read_os_release()):
        raise NotImplementedError()

    if os_release["NAME"] == "SLES":
        return f"sles{os_release['VERSION'].lower().replace('-', '')}"

    if os_release["NAME"] in {"Ubuntu", "Debian GNU/Linux"}:
        if os_release["VERSION_ID"] == "14.04":
            return "trusty"
        if os_release["VERSION_ID"] == "8":
            return "jessie"
        return os_release["VERSION_CODENAME"]

    raise NotImplementedError()
