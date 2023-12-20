#!/usr/bin/env python3

"""Prints ps output optionally filtered for CPU"""

import sys

from cmk_dev.utils import compact_dict, process_output, value_from


def main() -> None:
    """Main entrypoint"""
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
        if len(sys.argv) < 2 or str(proc_info["PSR"]) in sys.argv[1].split(","):
            print(compact_dict(proc_info, delim="\t", maxlen=50))


if __name__ == "__main__":
    main()
