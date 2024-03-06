#!/usr/bin/env python3

"""Check RPATH integrity for all files found below provided directory"""

import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from subprocess import check_output

ISSUE_TYPE = {
    "no-rpath": ("WW", "has no R[UN]PATH at all"),
    "more-than-one-rpath": ("WW", "has more than one R[UN]PATH"),
    "does-not-exist": ("EE", "rpath does not exist"),
    "origin-misplaced": ("EE", "'$ORIGIN' not at beginning"),
    "not-absolute": ("EE", "does not resolve to absolute path"),
    "mismatching-root": ("EE", "file and rpath point to different root folders"),
}


def runpath(path: Path) -> Sequence[str]:
    """Returns all RPATH or RUNPATH entries (':'-splitted) from @file"""
    return [
        p
        for rpaths in re.findall(
            r"R(UN)?PATH\s+(.*)\n",
            check_output(["objdump", "-x", path], text=True),
        )
        for p in rpaths[1].split(":")
    ]


def is_elf(path: Path) -> bool:
    """Returns True if @path points to an ELF binary"""
    return (
        path.is_file()
        and path.suffix != ".o"
        and check_output(["file", "-b", path], text=True).startswith("ELF ")
    )


def resolve_origin(path: Path, rpath: str) -> Path:
    """Turns a path containing '$ORIGIN' into an absolute one"""
    return Path(rpath.replace("$ORIGIN", str(path.parent.resolve())))


def check_integrity(path: Path) -> Iterator[tuple[str, str]]:
    """In a good world every binary should have exactly one R[UN]PATH entry being
    relative to $ORIGIN. Here we complain about all derivations."""
    rpaths = runpath(path)
    if not rpaths:
        yield "no-rpath", ""
    if len(rpaths) > 1:
        yield "more-than-one-rpath", str(rpaths)
    for rpath, resolved in ((r, resolve_origin(path, r)) for r in rpaths):
        if not resolved.exists():
            yield "does-not-exist", f"{resolved} (from {rpath=!r})"
        if rpath.find("$ORIGIN") > 0:
            yield "origin-misplaced", f"{rpath=!r}"
        if not (rpath.startswith("$ORIGIN") or rpath.startswith("/")):
            yield "not-absolute", f"{rpath!r}"
        if resolved.parts[:2] == ("opt", "omd") and resolved.parts[:5] != path.resolve().parts[:5]:
            yield "mismatching-root", f"{resolved} (from {rpath!r})"


def check_rpath(root_dir: str | Path) -> None:
    """Search and check"""
    issue_count = 0
    try:
        root_path = Path(root_dir)
        for file in filter(is_elf, root_path.glob("**/*")):
            for issue_type, info in check_integrity(file):
                issue_count += 1
                severity, description = ISSUE_TYPE[issue_type]
                print(f"{severity}: {file}: {description} {info} [{issue_type}]")
    except KeyboardInterrupt:
        pass
    finally:
        print(f"found {issue_count} issues")
        raise SystemExit(1 if issue_count else 0)


def main() -> None:
    """Main entrypoint"""
    check_rpath(sys.argv[1] if len(sys.argv) > 1 else ".")


if __name__ == "__main__":
    main()
