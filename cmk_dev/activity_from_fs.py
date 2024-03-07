#!/usr/bin/env python3
"""Run this to get a rough estimation of activity on this system based on file system access traces
"""
# pylint: disable=too-few-public-methods
# pylint: disable=too-many-locals
# pylint: disable=too-many-branches

import math
import os
import sys
from collections.abc import Mapping, MutableMapping, MutableSet, Set
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import rich
from rich.color import Color
from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.measure import Measurement
from rich.segment import Segment
from rich.style import Style


class Day:
    """Holds activity data for a day"""

    class ColorBox:
        """Draws a simple activity graph"""

        def __init__(self, minutes: Mapping[int, Set[Path]], width: None | int = None) -> None:
            self.minutes = minutes
            self.width = width

        def __rich_console__(self, _console: Console, options: ConsoleOptions) -> RenderResult:
            box_width = self.width or (options.max_width - 30)
            bar_duration = (22 - 5) * 60 / box_width
            for x in range(box_width):
                seg_start = int(5 * 60 + bar_duration * x)
                seg_end = int(5 * 60 + bar_duration * (x + 1))
                count = sum(
                    len(self.minutes[i]) for i in range(seg_start, seg_end) if i in self.minutes
                )
                intensity = min(255, count and (50 + count * math.log(count, 50000)))
                color = Color.from_rgb(intensity, intensity, intensity)
                yield Segment("█", Style(color=color))
            if not self.width:
                yield Segment.line()

        def __rich_measure__(self, _console: "Console", options: ConsoleOptions) -> Measurement:
            return Measurement(1, options.max_width)

    def __init__(self) -> None:
        self.begin: None | datetime = None
        self.end: None | datetime = None
        self.minutes: MutableMapping[int, MutableSet[Path]] = {}

    def update(self, timeofday: datetime, filepath: Path) -> bool:
        """Registeres a file for the current day"""
        minute = timeofday.hour * 60 + timeofday.minute
        self.minutes.setdefault(minute, set()).add(filepath)

        if timeofday.hour < 4 or timeofday.hour > 22:
            return False

        if self.begin and self.end and self.begin < timeofday < self.end:
            return False
        self.begin = min(self.begin, timeofday) if self.begin else timeofday
        self.end = max(self.end, timeofday) if self.end else timeofday

        return True

    def __str__(self) -> str:
        if not self.begin or not self.end:
            return "---"
        return f"{self.begin.strftime('%H:%M')} {self.end.strftime('%H:%M')}"

    def activity_bar(self, width: None | int = None) -> RenderableType:
        """Returns a drawable bar representing a day"""
        return self.ColorBox(self.minutes, width)


def activity_from_fs(start_dir: Path) -> None:
    """Traverse filesystem and track traces of activity"""
    dates = {}
    filecount = 0
    files_max = 0
    year_min = 2024
    year_max = datetime.now().year
    show_progress = True
    ignore = {
        Path("~/.cache/google-chrome").expanduser(),
        Path("~/.cache/mozilla/firefox").expanduser(),
        Path("~/.config/BraveSoftware/Brave-Browser/Default").expanduser(),
    }
    with suppress(KeyboardInterrupt):
        for dirpath, _dirnames, filenames in os.walk(start_dir.expanduser()):
            if files_max and filecount > files_max:
                break

            directory = Path(dirpath)
            if any(directory.is_relative_to(path) for path in ignore):
                continue

            for filename in filenames:
                try:
                    mtime = datetime.fromtimestamp(
                        (filepath := directory / filename).stat().st_mtime
                    )
                except FileNotFoundError:
                    continue

                if not year_min <= mtime.year <= year_max:
                    continue

                if (today := mtime.date()) not in dates:
                    dates[today] = Day()

                dates[today].update(mtime, filepath.parent)
                filecount += 1
                if show_progress:
                    if (loga := math.log(filecount, 2)) == int(loga):
                        print(f"{filecount:8d} files taken into account")
                else:
                    if filecount % 100 == 0 and today == datetime.now().date():
                        rich.print(dates[today].activity_bar(204), end="")
                        print(filepath)

    hint_bar = "." * (Console().options.max_width - 30 - 1)
    for h in range(23 - 5):
        hour = str(5 + h)
        i = int((len(hint_bar) - 1) / (22 - 5) * h)
        hint_bar = hint_bar[:i] + hour + hint_bar[i + len(hour) :]

    last_weekday = 0
    for date, borders in sorted(dates.items()):
        if date.weekday() < last_weekday:
            print(f"                             {hint_bar}")
        last_weekday = date.weekday()
        rich.print(f"{date.strftime('%Y-%m-%d %a')}: {borders}  ", end="")
        rich.print(borders.activity_bar())

    print(f"                             {hint_bar}")
    print()
    print(f"{filecount:8d} files taken into account")
    print()
    print(
        "Hint: also search Slack for `on:<date> from:@<your-name>`"
        " e.g. `on:2024-02-19 from:@Ernie`"
    )


def main() -> None:
    """Main entry point"""
    activity_from_fs(Path(sys.argv[1] if len(sys.argv) > 1 else "~"))


if __name__ == "__main__":
    main()
