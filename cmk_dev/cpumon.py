#!/usr/bin/env python3

"""Prints ps output optionally filtered for CPU

Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
"""

import sys

from trickkiste.misc import compact_dict, process_output

from cmk_dev.utils import value_from


def cpumon(cpus: str) -> None:
    """Basically runs `ps` and shows results for given CPUs only (if given, else all)"""
    header, *lines = (
        x.split(maxsplit=8)
        for x in process_output("ps -axo pid,user,pcpu,psr,sz,rss,args --sort=-pcpu").split("\n")
        if x.strip()
    )

    for proc_info in sorted(
        (pinfo for line in lines if (pinfo := dict(zip(header, map(value_from, line))))),
        key=lambda x: x["%CPU"],
        reverse=True,
    ):
        if not cpus or str(proc_info["PSR"]) in cpus.split(","):
            print(compact_dict(proc_info, delim="\t", maxlen=50))


def main() -> None:
    """Main entrypoint"""
    cpumon(sys.argv[1] if len(sys.argv) > 1 else "")


if __name__ == "__main__":
    main()
