#!/usr/bin/env python3
#
# Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
#
"""
Show jobs with highest resource usage

This uses files containing container metadata produced by docker-shaper.
"""

import json
import pathlib
from argparse import ArgumentParser, Namespace
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pprint import pprint
from typing import Any, Generator

from pydantic import AliasPath, BaseModel, Field, ValidationError
from trickkiste.misc import parse_age

from .version import __version__

MILISECOND_INDICATOR_THRESHOLD = 1_000_000_000


class Datapoint(BaseModel):
    time: int  # seconds since creation
    cpu_usage: float = Field(validation_alias=AliasPath("cpu-usage"))
    memory_usage: int = Field(validation_alias=AliasPath("mem-usage"))  # byte?


class ContainerMount(BaseModel):
    Destination: str
    Driver: str | None
    Mode: str
    Name: str | None
    RW: bool
    Source: str
    Type: str


class ContainerState(BaseModel):
    Dead: bool
    Error: str
    ExitCode: int
    FinishedAt: datetime
    Health: None | dict[str, Any]
    OOMKilled: bool
    Pid: int
    Running: bool
    StartedAt: datetime
    Status: str


class ContainerMetadata(BaseModel):
    Id: str
    Name: str
    Created: datetime
    Image: str
    Mounts: list[ContainerMount]
    State: ContainerState
    Args: list[Any]
    Config: dict[str, None | list[Any] | dict[str, Any]]
    HostConfig: dict[str, Any]


class Container(BaseModel):
    metadata: ContainerMetadata
    datapoints: list[Datapoint]
    source_filename: str  # for debugging - str is enough


def main() -> None:
    args = parse_cli()

    data_path = pathlib.Path(args.path)
    datapoints: list[Container] | Generator[Container] = load_data(data_path)

    # We assume there is little to no difference between container creation and
    # start time, making it the same.
    # The data retrieved from docker-shaper has a timezone applied and to be
    # able to compare our before and after dates, we need a timezone aswell.
    # This is also the reason for the currently used late filtering. While
    # filtering based on filename could be faster, the filenames do not contain
    # a timezone information and we then would be limited to having filenames
    # following a specific format.
    timezone = dt_timezone.utc  # naive?
    if args.after_date:
        after_date = datetime.now(tz=timezone) - timedelta(seconds=args.after_date)
        print(f"Limiting search to containers started after {after_date}")

        datapoints = (d for d in datapoints if d.metadata.Created > after_date)

    if args.before_date:
        before_date = datetime.now(tz=timezone) - timedelta(seconds=args.before_date)
        print(f"Limiting search to containers started before {before_date}")
        datapoints = (d for d in datapoints if d.metadata.Created < before_date)

    # Convert our generator into a fixed list, to be able to run multiple
    # analysis over it.
    datapoints = list(datapoints)
    assert isinstance(datapoints, list)
    assert datapoints, "No data found"
    print(f"\nProcessing {len(datapoints)} elements")

    limit = args.limit

    print("Top CPU usage")
    for index, entry in enumerate(order_by_cpu(datapoints), start=1):
        if index > limit:
            break

        print(f"{index:>3}: {get_max_cpu_usage(entry)} {get_pretty_container_info(entry)}")

    print("Top Memory usage")
    for index, entry in enumerate(order_by_memory(datapoints), start=1):
        if index > limit:
            break

        print(
            f"{index:>3}: {format_bytes(get_max_memory_usage(entry))} {get_pretty_container_info(entry)}"
        )


def parse_cli() -> Namespace:
    parser = ArgumentParser(description="""
        This script uses data collected by docker-shaper.
        The ndjson formatted data files can usually be found on the build nodes
        at: ~jenkins/.docker_shaper/container-logs
        """)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("path", help="Path to look for data files")
    parser.add_argument("--limit", default=10, type=int, help="Limit results to this many")
    parser.add_argument(
        "--after",
        dest="after_date",
        type=parse_age,
        help="Only use containers started from this point on, e.g. 48h",
    )
    parser.add_argument(
        "--before",
        dest="before_date",
        type=parse_age,
        help="Only use containers started until this point on, e.g. 24h",
    )

    return parser.parse_args()


def load_data(path: pathlib.Path) -> Generator[Container, None, None]:
    for index, entry in enumerate(path.glob("*.ndjson")):
        if index % 100 == 0:
            print(".", end="")

        # if index == 10000:  # Limit amount for testing
        #     break

        intermediate_dict = _load_file_to_container_format(entry)
        if not intermediate_dict["datapoints"]:
            # Skip entries without datapoints - no resource usage data
            continue

        if not intermediate_dict["metadata"]:
            # Skip entries with incomplete information
            print(f"{entry} has no metadata")
            continue

        try:
            yield Container.model_validate(intermediate_dict)
        except ValidationError as verr:
            raise ValueError(f"Unable to process {entry}") from verr


def _load_file_to_container_format(path: pathlib.Path) -> dict[str, Any]:
    """
    Helper to prepare data for pydantic

    This parses the files and tries to use their contents.
    The output format will be so that it can be used by pydantic
    """
    metadata: None | dict[str, Any] = None
    datapoints: list[dict[str, int | float]] = []

    with path.open() as f:
        for line in f:
            try:
                json_data = json.loads(line)
            except json.decoder.JSONDecodeError as decodeError:
                print(f"Failed to process line in {path}: {decodeError}")
                continue

            if "Id" in json_data:
                if metadata:
                    # Some files contain a final block of metadata,
                    # probably to show the state after the run.
                    # Example file: container-logs/2024.11.20-01.13.55-ba22e72425.ndjson
                    # We try to combine this with existing data
                    metadata.update(json_data)
                    continue

                metadata = json_data
            elif "cpu-usage" in json_data:
                datapoints.append(_sanitize_raw_dp(json_data))

    return {
        "metadata": metadata,
        "datapoints": datapoints,
        "source_filename": str(path),
    }


def _sanitize_raw_dp(dp: dict[str, int | float | None]) -> dict[str, int | float]:
    return {
        k: v if v is not None else 0
        for k, v
        in dp.items()
    }


def order_by_cpu(data: list[Container]) -> list[Container]:
    return sorted(
        data,
        key=get_max_cpu_usage,
        reverse=True,
    )


def get_max_cpu_usage(c: Container) -> float:
    return max(dp.cpu_usage for dp in c.datapoints)


def order_by_memory(data: list[Container]) -> list[Container]:
    return sorted(
        data,
        key=get_max_memory_usage,
        reverse=True,
    )


def get_max_memory_usage(c: Container) -> int:
    return max(dp.memory_usage for dp in c.datapoints)


def get_pretty_container_info(c: Container) -> str:
    # The date in StartedAt sometimes is 0001-01-01, therefore we switch to
    # the date of the creation. In our CI setup date of creation is usually
    # the time the container is started aswell.
    start_time = c.metadata.Created

    job_name = guess_jenkins_job(c.metadata)

    cpu_peak = sorted(c.datapoints, key=lambda dp: dp.cpu_usage, reverse=True)[0]
    mem_peak = sorted(c.datapoints, key=lambda dp: dp.memory_usage, reverse=True)[0]

    return (
        f"{job_name}, {start_time} "
        f"{format_cpu_peak(cpu_peak, start_time)} ({format_cpu_average(c.datapoints)}), "
        f"{format_memory_peak(mem_peak, start_time)} (data: {c.source_filename})"
    )


def format_cpu_peak(dp: Datapoint, start_time: datetime) -> str:
    peak_time = format_peak_time(dp, start_time)

    return f"CPU: {dp.cpu_usage} @ {peak_time}"


def format_cpu_average(datapoints: list[Datapoint]) -> str:
    amount_datapoints = len(datapoints)
    if not amount_datapoints:
        return "unable to calculate average"
    summed_usage = sum(dp.cpu_usage for dp in datapoints)

    return f"average: {summed_usage / amount_datapoints:.4} CPU"


def format_memory_peak(dp: Datapoint, start_time: datetime) -> str:
    peak_time = format_peak_time(dp, start_time)

    return f"MEM: {format_bytes(dp.memory_usage)} @ {peak_time}"


def format_peak_time(dp: Datapoint, start_time: datetime) -> str:
    point_in_time = dp.time
    if point_in_time > MILISECOND_INDICATOR_THRESHOLD:
        # Probably a millisecond value
        # To me it is currently unclear when what value is used.
        delta = timedelta(milliseconds=point_in_time)
    else:
        delta = timedelta(seconds=point_in_time)

    return f"{start_time + delta}"


def format_bytes(size: int | float) -> str:
    # Adapted from https://stackoverflow.com/a/49361727
    # 2**10 = 1024
    power = 2**10
    n = 0
    power_labels = {0: "", 1: "Ki", 2: "Mi", 3: "Gi", 4: "Ti"}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f}{power_labels[n]}Bi"


def guess_jenkins_job(metadata: ContainerMetadata) -> str:
    if not metadata.Mounts:
        return metadata.Name

    mount_destinations = {mount.Destination for mount in metadata.Mounts}
    mount_destinations = {
        d
        for d in mount_destinations
        if d
        not in (
            "/home/jenkins/.docker",
            "/var/run/docker.sock",
            "/etc/group",
            "/etc/passwd",
            "/etc/.cmk-credentials",
            "/git-lowerdir",
            # Job-specific stuff
            "/home/jenkins/.cache",
            "/home/jenkins",
            "/home/jenkins/git_reference_clones/check_mk.git",
        )
    }
    mount_sources = {mount.Source for mount in metadata.Mounts}
    mount_sources = {
        s
        for s in mount_sources
        if s
        not in (
            "/home/jenkins/.docker",
            "/var/run/docker.sock",
            "/etc/group",
            "/etc/passwd",
            # job stuff
            "/home/jenkins/shared_cargo_folder",
            "/home/jenkins/git_reference_clones/check_mk.git",
            "/home/jenkins/.cmk-credentials",
        )
    }

    mounts = mount_destinations | mount_sources

    def sanitize_mount(mount_path: str) -> str:
        m2 = mount_path.replace("/home/jenkins/workspace/", "")
        m2 = m2.replace("/.cache", "")
        m2 = m2.replace("/.venv", "")
        if m2.endswith("/checkout"):
            m2 = m2.rstrip("/checkout")

        return m2

    possible_names = sorted({sanitize_mount(m) for m in mounts})

    first_candidate = possible_names[0]

    if all(candidate.startswith(first_candidate) for candidate in possible_names):
        # TODO: we sometimes can get more infos, e.g. about the used distro:
        # {'Destination': '/home/jenkins/workspace/checkmk/master/testbuild/checkout/.cache',
        #  'Driver': None,
        #  'Mode': '',
        #  'Name': None,
        #  'RW': True,
        #  'Source': '/home/jenkins/workspace/checkmk/master/testbuild_ubuntu-24.04/container_shadow_workspace_ci/ubuntu-24.04/checkout_cache',
        #  'Type': 'bind'},

        return first_candidate

    # We sometime work in a different workspace or have additional infos
    # present in the mountpoint, like the distro we build for.
    # In this case try to make use of that.
    second_candidate = possible_names[1]
    if all(
        cand.startswith(second_candidate) or cand.startswith(first_candidate)
        for cand in possible_names
    ):
        return second_candidate

    pprint(mounts)
    pprint(possible_names)
    raise RuntimeError("Unable to get matching job name!")


if __name__ == "__main__":
    main()
