#!/usr/bin/env python3

"""Functionality that might change during runtime
"""
# pylint: disable=invalid-name  # names come from aiodocker, not my fault
# pylint: disable=too-many-instance-attributes,too-few-public-methods
# pylint: disable=too-many-branches,too-many-return-statements
# pylint: disable=too-many-lines
# pylint: disable=too-many-arguments
# pylint: disable=fixme
# pylint: disable=import-error  # no clue why..

import asyncio
import logging
import re
import time
from asyncio import StreamReader
from asyncio.subprocess import PIPE, create_subprocess_exec
from collections.abc import (
    AsyncIterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
)
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Literal, Type, TypeAlias, cast

from aiodocker import Docker, DockerError
from aiodocker.containers import DockerContainer
from pydantic import BaseModel, ConfigDict, Json, model_validator

from docker_shaper.utils import date_from

MessageType: TypeAlias = Literal[
    "exception",
    "error",
    "reference_update",
    "reference_del",
    "container_add",
    "container_del",
    "container_update",
    "image_add",
    "image_del",
    "image_update",
]
MType: TypeAlias = tuple[MessageType, str, None | object]
ImageIdent: TypeAlias = str | tuple[None | str, str, str]


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper")


def short_id(docker_id: str) -> str:
    """Return the 10-digit variant of a long docker ID
    >>> short_id("sha256:abcdefghijklmnop")
    'abcdefghij'
    """
    if not docker_id or not is_uid(docker_id):
        return docker_id
    return docker_id[7:17] if docker_id.startswith("sha256:") else docker_id[:10]


def is_uid(ident: str) -> bool:
    """
    >>> is_uid("sha256:48a3535fe27fea1ac6c2f41547770d081552c54b2391c2dda99e2ad87561a4f2")
    True
    >>> is_uid("48a3535fe27fea1ac6c2f41547770d081552c54b2391c2dda99e2ad87561a4f2")
    True
    >>> is_uid("48a3535fe2")
    True
    >>> is_uid("48a3535fe27f")
    False
    """
    return bool(
        ident.startswith("sha256:")
        or re.match("^[0-9a-f]{64}$", ident)
        or re.match("^[0-9a-f]{10}$", ident)
    )


def unique_ident(ident: str) -> ImageIdent:
    """Return a short Id if ident is a unique id and leave it as it is otherwise
    >>> unique_ident("sha256:48a3535fe27fea1ac6c2f41547770d081552c54b2391c2dda99e2ad87561a4f2")
    '48a3535fe2'
    >>> unique_ident("914463316976.dkr.ecr.eu-central-1.amazonaws.com/user_admin_panel:958")
    ('914463316976.dkr.ecr.eu-central-1.amazonaws.com', 'user_admin_panel', '958')
    >>> unique_ident("https://abcd.def:1234/nested/structure/base_name:12345")
    ('https://abcd.def:1234/nested/structure', 'base_name', '12345')
    """
    if is_uid(ident):
        return short_id(ident)
    *maybe_reg, tagged_name = ident.rsplit("/", maxsplit=1)
    name, *maybe_tag = tagged_name.split(":")
    assert len(maybe_tag) in {0, 1}, ident
    return maybe_reg[0] if maybe_reg else None, name, maybe_tag[0] if maybe_tag else "latest"


class Deserializable(BaseModel):
    """Implements a generic deserializer with explicitly ignorable keys"""

    model_config = ConfigDict(extra="forbid")
    IgnoreKeys: ClassVar[set[str]] = set()

    @model_validator(mode="before")
    @classmethod
    def remove_ignored(
        cls: Type["Deserializable"], values: Json[dict[str, Any]]
    ) -> Json[dict[str, Any]]:
        """Removes unwanted keys"""
        return {key: value for key, value in values.items() if key not in cls.IgnoreKeys}


class ContainerShowConfig(Deserializable):
    """Wraps information coming from container.show().config"""

    Labels: Mapping[str, str]
    Volumes: None | Mapping[str, object]
    Cmd: None | Sequence[str]

    IgnoreKeys = {
        "User",
        "AttachStderr",
        "Env",
        "AttachStdin",
        "Domainname",
        "Tty",
        "OpenStdin",
        "WorkingDir",
        "Hostname",
        "Entrypoint",
        "AttachStdout",
        "OnBuild",
        "Image",
        "StdinOnce",
        "ExposedPorts",
        "Healthcheck",
        "Shell",
    }


class ContainerShowState(Deserializable):
    """Wraps information coming from container.show().state"""

    Running: bool
    Status: str
    OOMKilled: bool
    Dead: bool
    Error: str
    Pid: int
    ExitCode: int
    StartedAt: datetime
    FinishedAt: datetime
    Health: None | Mapping[str, object] = None

    IgnoreKeys = {"Paused", "Restarting"}


class ContainerMount(Deserializable):
    """Wraps information for elements coming from container.show().Mounts"""

    Name: None | str = None
    Type: str
    Source: str
    Destination: str
    Driver: None | str = None
    Mode: str
    RW: bool

    IgnoreKeys = {"Propagation"}


class ContainerShow(Deserializable):
    """Wraps information coming from container.show()"""

    Id: str
    Name: str
    Created: datetime
    Image: str
    Mounts: Sequence[ContainerMount]
    State: ContainerShowState
    Args: Sequence[str]
    Config: ContainerShowConfig
    HostConfig: Mapping[str, object]
    IgnoreKeys = {
        "RestartCount",
        "Path",
        "Platform",
        "MountLabel",
        "AppArmorProfile",
        "ExecIDs",
        "ProcessLabel",
        "Driver",
        "ResolvConfPath",
        "HostnamePath",
        "HostsPath",
        "NetworkSettings",
        "GraphDriver",
        "LogPath",
        "AppArmorLabel",
    }

    def __str__(self) -> str:
        return (
            f"{self.Id[:10]}{self.Name:<20s}"
            f" {self.State.Status} {str(self.Created)[:19]} {self.Config.Volumes}"
        )

    @property
    def status(self) -> str:
        """Status shortcut"""
        return self.State.Status


class ContainerCpuStatsCpuUsage(Deserializable):
    """Wraps information coming from container.stats().cpu_stats.cpu_usage"""

    total_usage: int
    IgnoreKeys = {"usage_in_kernelmode", "usage_in_usermode"}


class ContainerCpuStats(Deserializable):
    """Wraps information coming from container.stats().cpu_stats"""

    cpu_usage: ContainerCpuStatsCpuUsage
    system_cpu_usage: None | int = None
    online_cpus: None | int = None
    throttling_data: None | Mapping[str, int]


class ContainerMemoryStats(Deserializable):
    """Wraps information coming from container.stats().memory_stats"""

    stats: None | Mapping[str, int] = None
    limit: None | int = None
    usage: None | int = None


class ContainerStats(Deserializable):
    """Wraps information coming from container.show()"""

    cpu_stats: ContainerCpuStats
    memory_stats: ContainerMemoryStats
    IgnoreKeys = {
        "preread",
        "id",
        "read",
        "name",
        "num_procs",
        "blkio_stats",
        "pids_stats",
        "storage_stats",
        "precpu_stats",
        "networks",
    }


@dataclass
class Container:
    """Gathers information about a Docker container"""

    raw_container: DockerContainer
    show: None | ContainerShow = None
    stats: None | ContainerStats = None
    last_stats: None | ContainerStats = None

    def __str__(self) -> str:
        image_str = "" if not self.show else f" image:{short_id(self.show.Image)}"
        status_str = "" if not self.show else f" - {self.show.State.Status}"
        return f"{self.short_id} / {self.name:<26s}{image_str}{status_str}"

    @property
    def id(self) -> str:
        """Container ID"""
        return self.raw_container.id

    @property
    def short_id(self) -> str:
        """First 10 digits of Id"""
        return self.raw_container.id[:10]

    @property
    def name(self) -> str:
        """Shortcut for name of image (without leading /)"""
        if not self.show:
            return "(unknown)"
        return self.show.Name[1:]

    @property
    def status(self) -> str:
        """Status shortcut"""
        if not self.show:
            return "(unknown)"
        return self.show.State.Status

    @property
    def created_at(self) -> datetime:
        """Shortcut to self.show.Created"""
        assert self.show
        return self.show.Created

    @property
    def started_at(self) -> datetime:
        """Shortcut to self.show.State.StartedAt"""
        assert self.show
        return self.show.State.StartedAt

    @property
    def finished_at(self) -> datetime:
        """Shortcut to self.show.State.FinishedAt"""
        assert self.show
        return self.show.State.FinishedAt

    @property
    def image(self) -> str:
        """Shortcut to self.show.State.FinishedAt"""
        assert self.show
        return self.show.Image

    @property
    def pid(self) -> int:
        """Shortcut to self.show.State.Pid"""
        assert self.show
        return self.show.State.Pid

    @property
    def labels(self) -> Mapping[str, str]:
        """Shortcut to self.show.State.Pid"""
        assert self.show
        return self.show.Config.Labels

    @property
    def cmd(self) -> None | Sequence[str]:
        """Shortcut to self.show.State.Pid"""
        assert self.show
        return self.show.Config.Cmd

    @property
    def host_config(self) -> Mapping[str, object]:
        """Shortcut to self.show.HostConfig"""
        assert self.show
        return self.show.HostConfig

    def cpu_usage(self) -> float:
        """Returns actual CPU usage of container"""
        if not self.stats or not self.last_stats:
            return 0
        cpu_stats, last_cpu_stats = self.stats.cpu_stats, self.last_stats.cpu_stats
        if (
            (cpu_stats := self.stats.cpu_stats) is None
            or (last_cpu_stats := self.last_stats.cpu_stats) is None
            or cpu_stats.system_cpu_usage is None
            or last_cpu_stats.system_cpu_usage is None
            or cpu_stats.online_cpus is None
        ):
            return 0
        return (
            ((cpu_stats.cpu_usage.total_usage or 0) - (last_cpu_stats.cpu_usage.total_usage or 0))
            / (cpu_stats.system_cpu_usage - last_cpu_stats.system_cpu_usage)
            * cpu_stats.online_cpus
        )


class ImageInspect(Deserializable):
    """Wraps information docker.image.inspect"""

    Id: str
    RepoTags: Sequence[str]
    RepoDigests: Sequence[str]
    Created: datetime
    Parent: str

    IgnoreKeys = {
        "RootFS",
        "ContainerConfig",
        "Os",
        "Author",
        "Container",
        "Config",
        "Comment",
        "Architecture",
        "DockerVersion",
        "Size",
        "Metadata",
        "VirtualSize",
        "GraphDriver",
    }

    @property
    def tags(self) -> set[str]:
        """All named references to image"""
        return set(self.RepoTags)  # | set(self.RepoDigests)


class ImageHistoryElement(Deserializable):
    """Wraps information docker.image.history"""

    Id: str
    CreatedBy: str
    Created: int
    Tags: None | Sequence[str]
    Size: int
    Comment: str


@dataclass
class Image:
    """Wraps information docker.images
    Note: images.get() will be deprecated and thereof images.inspect() has to be used. As a result
    all values also in ImageInspect will be pruned
    """

    inspect: ImageInspect
    history: Sequence[ImageHistoryElement]
    children: set[str] = field(default_factory=set)

    def __str__(self) -> str:
        return f"{self.short_id} / {list(self.tags)}"

    @property
    def id(self) -> str:
        """Image ID"""
        assert re.match("^sha256:[0-9a-f]{64}$", self.inspect.Id)
        return self.inspect.Id

    @property
    def short_id(self) -> str:
        """First 10 digits of Id"""
        assert re.match("^sha256:[0-9a-f]{64}$", self.inspect.Id)
        return self.inspect.Id[7:17]

    @property
    def parent(self) -> str:
        """Shortcut to self.inspect.Parent"""
        return self.inspect.Parent

    @property
    def created_at(self) -> datetime:
        """Shortcut to self.inspect.Created"""
        return self.inspect.Created

    @property
    def tags(self) -> set[str]:
        """All named references to image"""
        return self.inspect.tags


class Volume(Deserializable):
    """Wraps information docker.volumes"""

    Name: str
    Labels: None | Mapping[str, str]
    CreatedAt: datetime
    Mountpoint: str
    IgnoreKeys = {"Driver", "Scope", "Options"}

    def __str__(self) -> str:
        return f"{self.Name[:12]} {str(self.CreatedAt)[:19]} {self.Mountpoint}"


class Network(Deserializable):
    """Wraps information docker.networks"""

    Id: str
    Name: str
    Created: datetime
    IgnoreKeys = {
        "Driver",
        "Options",
        "Containers",
        "Ingress",
        "Scope",
        "Attachable",
        "ConfigFrom",
        "IPAM",
        "EnableIPv6",
        "ConfigOnly",
        "Internal",
        "Labels",
    }

    def __str__(self) -> str:
        return f"{self.Id[:12]} {str(self.Created)[:19]}"


class DockerEventActorAttributes(Deserializable):
    """Wraps information coming from DockerEvent.Actor"""

    image: None | str
    name: None | str
    IgnoreKeys = {
        "maintainer",
        "execID",
        "container",
        "type",
        "exitCode",
        "execDuration",
        "signal",
        "reclaimed",
        "driver",
        "read/write",
        "destination",
        "propagation",
        "version",
        "comment",
        "imageID",
        "imageRef",
    }


class DockerEventActor(Deserializable):
    """Wraps information coming from DockerEvent.Actor"""

    ID: str
    Attributes: Mapping[str, str]  # DockerEventActorAttributes


class DockerEvent(Deserializable):
    """Wraps a Docker event"""

    timeNano: int
    Type: str
    Action: str  # create
    Actor: DockerEventActor

    # id: None | str # same as event.Actor.ID
    # status: str    # same as event.Action
    # from: str      # same as event.Actor.Attributes["image"]

    IgnoreKeys = {"id", "time", "scope", "status", "from"}


class DockerState:
    """Gathers all information about local docker service"""

    containers: MutableMapping[str, Container]
    containers_crawled: bool
    containers_crawl_interval: int

    images: MutableMapping[str, Image]
    images_crawled: bool
    images_crawl_interval: int

    volumes: MutableMapping[str, Volume]
    volumes_crawled: bool
    volumes_crawl_interval: int

    networks: MutableMapping[str, Network]
    networks_crawled: bool
    networks_crawl_interval: int

    event_horizon: int
    last_referenced: MutableMapping[ImageIdent, int]

    def __init__(self) -> None:
        self.containers = {}
        self.containers_crawled = False
        self.containers_crawl_interval = 120

        self.images = {}
        self.images_crawled = False
        self.images_crawl_interval = 120

        self.volumes = {}
        self.volumes_crawled = False
        self.volumes_crawl_interval = 120

        self.networks = {}
        self.networks_crawled = False
        self.networks_crawl_interval = 120

        self.event_horizon = int(time.time())
        self.last_referenced = {}

        self.docker_client: None | Docker = None
        self.updates = asyncio.Queue[MType]()

    async def inform(self, mtype: MessageType, mtext: str, mobj: None | object = None) -> None:
        """Inform about something important has happened"""
        await self.updates.put((mtype, mtext, mobj))

    async def wait_for_change(self) -> AsyncIterator[MType]:
        """Pass messages read from message queue"""
        while True:
            message = await self.updates.get()
            yield message

    async def run(self) -> None:
        """Starts and awaits monitoring background tasks"""
        try:
            async with Docker() as self.docker_client:
                await asyncio.gather(
                    self.monitor_events(),
                    self.run_crawl_containers(),
                    self.run_crawl_images(),
                    self.run_crawl_volumes(),
                    self.run_crawl_networks(),
                )
                self.docker_client = None
        except Exception:  # pylint: disable=broad-except
            log().exception("in DockerState.run()")

    def client(self) -> Docker:
        """Returns the current Docker client and raises if not available (for typing reasons)"""
        if not self.docker_client:
            raise RuntimeError("Wrong state")
        return self.docker_client

    async def monitor_events(self) -> None:
        """Continuously reads and handles Docker events"""

        subscriber = self.client().events.subscribe()  # type: ignore[no-untyped-call]
        event_buffer: MutableSequence[DockerEvent] = []

        # import json
        # for _raw_e in map(json.loads, open("docker-events.log")):
        while True:
            try:
                _raw_e = await subscriber.get()
                event = DockerEvent(**_raw_e)

                assert not _raw_e.get("id") or _raw_e.get("id") == event.Actor.ID
                assert not _raw_e.get("status") or _raw_e.get("status") == event.Action
                assert (
                    not _raw_e.get("from") or _raw_e.get("from") == event.Actor.Attributes["image"]
                )

                if all(
                    (
                        self.containers_crawled,
                        self.images_crawled,
                        self.volumes_crawled,
                        self.networks_crawled,
                    )
                ):
                    while event_buffer:
                        await handle_docker_event(self, event_buffer.pop(0))
                    await handle_docker_event(self, event)
                else:
                    event_buffer.append(event)
            except Exception as exc:  # pylint: disable=broad-except
                log().error("Error while handling event:")
                log().error("%s", _raw_e)
                await self.inform("exception", "in monitor_events()", exc)

    async def run_crawl_containers(self) -> None:
        """Continuously updates information about running containers"""
        while True:
            try:
                await crawl_containers(self)
            except Exception as exc:  # pylint: disable=broad-except
                await self.inform("exception", "in run_crawl_containers()", exc)
            await asyncio.sleep(self.containers_crawl_interval)

    async def run_crawl_images(self) -> None:
        """Continuously updates information about local images"""
        while True:
            try:
                await crawl_images(self)
            except Exception as exc:  # pylint: disable=broad-except
                await self.inform("exception", "in run_crawl_images()", exc)
            await asyncio.sleep(self.images_crawl_interval)

    async def run_crawl_volumes(self) -> None:
        """Continuously updates information about docker volumes"""
        while True:
            try:
                await crawl_volumes(self)
            except Exception as exc:  # pylint: disable=broad-except
                await self.inform("exception", "in run_crawl_volumes()", exc)
            await asyncio.sleep(self.volumes_crawl_interval)

    async def run_crawl_networks(self) -> None:
        """Continuously updates information about docker networks"""
        while True:
            try:
                await crawl_networks(self)
            except Exception as exc:  # pylint: disable=broad-except
                await self.inform("exception", "in run_crawl_networks()", exc)
            await asyncio.sleep(self.networks_crawl_interval)

    async def prune_builder_cache(self) -> tuple[Sequence[str], Sequence[str], int]:
        """Runs `docker builder prune` in background"""
        return await prune_builder_cache()


async def prune_builder_cache() -> tuple[Sequence[str], Sequence[str], int]:
    """Runs `docker builder prune` in background"""

    async def acc_stream(stream: StreamReader, prefix: str) -> Sequence[str]:
        result = []
        async for line in (raw_line.decode().strip() async for raw_line in stream):
            log().debug("%s: %s", prefix, line)
            result.append(line)
        return result

    cmd = ("docker", "builder", "prune", "--force", "--filter=until=24h", "--keep-storage=100G")
    process = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
    assert process.stdout and process.stderr
    stdout, stderr, returncode = await asyncio.gather(
        acc_stream(process.stdout, "docker-builder-prune-std"),
        acc_stream(process.stderr, "docker-builder-prune-err"),
        process.wait(),
    )
    if returncode != 0:
        log().error("prune_builder_cache failed")
    return stdout, stderr, returncode


async def crawl_containers(state: DockerState) -> None:
    """Updates set of known containers - first run is allowed to find unknown containers,
    afterwards the set is expected to be consistent"""

    containers = {
        cont.id: cont
        for cont in await state.client().containers.list(all=True)  # type: ignore[no-untyped-call]
    }
    log().info("crawl (%d) containers..", len(containers))
    for container in containers.values():
        if container.id in state.containers:
            continue
        log().debug("found unregistered container %s", short_id(container.id))
        if state.containers_crawled:
            log().error("%s should have been registered automatically before!", container.id)
        await register_container(state, container)

    if not state.containers_crawled:
        state.containers_crawled = True
        log().info("initial container crawl done")

    for container_id in list(state.containers):
        if container_id not in containers:
            log().error("registered container %s does not exist anymore", short_id(container_id))


async def container_from(docker_client: Docker, ident: str) -> None | DockerContainer:
    """Retrieves container instance identified by @ident"""
    with suppress(DockerError):
        return cast(
            DockerContainer,
            await docker_client.containers.get(ident),  # type: ignore[no-untyped-call]
        )

    return None


async def register_container(state: DockerState, container: DockerContainer) -> None:
    """Put a container into set of known containers"""
    log().debug("register container %s", container.id[:10])
    state.containers[container.id] = Container(container)
    asyncio.ensure_future(watch_container(state, container))


async def unregister_container(state: DockerState, container_id: str) -> None:
    """Remove a container from set of known containers"""
    try:
        del state.containers[container_id]
    except KeyError:
        await state.inform(
            "error", f"tried to remove container {short_id(container_id)} unknown to us"
        )


async def watch_container(state: DockerState, container: DockerContainer) -> None:
    """Continuously monitor a distinct container over time"""
    name = "unknown"  # have a value for the `except` case
    normally_terminated = False
    try:
        container_info = state.containers[container.id]
        container_info.show = (
            show := ContainerShow(**(await container.show()))  # type: ignore[no-untyped-call]
        )

        # todo: wrong - other things could have happened since..
        await register_reference(state, show.Image, show.Created.timestamp())

        await state.inform("container_add", container.id, container_info)

        log().info(">> new container: %s %s", container_info.short_id, name)

        last_cpu_usage, last_mem_usage, count = 0.0, 0, 0

        async for raw_stats in container.stats():  # type: ignore[no-untyped-call]
            container_info.last_stats = container_info.stats
            container_info.stats = ContainerStats(**raw_stats)
            old_show = container_info.show
            container_info.show = ContainerShow(
                **(await container.show())  # type: ignore[no-untyped-call]
            )
            cpu_usage = container_info.cpu_usage()
            mem_usage = container_info.stats.memory_stats.usage or 0
            if not old_show and container_info.last_stats:
                continue

            if update_inform_trigger(
                count,
                old_show,
                container_info.show,
                cpu_usage,
                mem_usage,
                last_cpu_usage,
                last_mem_usage,
            ):
                last_cpu_usage, last_mem_usage = cpu_usage, mem_usage
                await state.inform("container_update", container.id, container_info)

            count += 1

        normally_terminated = True

    except DockerError as exc:
        log().warning("DockerError while watching %s: %s", container.id, exc)
    except Exception as exc:  # pylint: disable=broad-except
        await state.inform("exception", f"in watch_container() while watching {container.id}", exc)
    finally:
        if normally_terminated:
            log().info("<< container terminated: %s %s", container_info.short_id, name)
        await unregister_container(state, container.id)
        await state.inform("container_del", container.id, container_info)


def update_inform_trigger(
    count: int,
    old_show: ContainerShow,
    new_show: ContainerShow,
    cpu_usage: float,
    mem_usage: int,
    last_cpu_usage: float,
    last_mem_usage: int,
) -> bool:
    """Returns wether we should send a container update"""
    return (
        count == 0
        or (new_show.status != old_show.status)
        or (
            (count % 10 == 0)
            and (abs(last_cpu_usage - cpu_usage) > 0.2 or abs(last_mem_usage - mem_usage) > 1 << 20)
        )
    )


async def crawl_images(state: DockerState) -> None:
    """Updates set of known images - first run is allowed to find unknown images,
    afterwards the set is expected to be consistent"""
    log().debug("fetch image list..")
    image_ids = set(image["Id"] for image in await state.client().images.list(all=True))
    log().info("crawl (%d) images..", len(image_ids))

    for image_id in image_ids:
        if image_id in state.images:
            continue
        if state.images_crawled:
            log().warning("  found unregistered image %s", short_id(image_id))
        try:
            await register_image(state, image_id)
        except DockerError:
            log().warning("newly found image %s suddenly disappeared", short_id(image_id))

    if not state.images_crawled:
        state.images_crawled = True
        log().info("initial image crawl done")

    # This is only for plausibility - go through all registered images and check if they still
    # exist and also check their parents having stored a reference
    for image_id in list(state.images):
        if image_id in image_ids:
            # registered image also exists in found images (ok)
            if not (parent_id := state.images[image_id].parent):
                continue
            if parent_id in state.images:
                # parent is registered (as expected) - now check if it knows it's child
                if image_id not in state.images[parent_id].children:
                    log().error(
                        "parent '%s' of '%s' does not store a reference to its child",
                        short_id(parent_id),
                        short_id(image_id),
                    )
                    state.images[parent_id].children.add(image_id)
            else:
                log().error(
                    "parent '%s' of '%s' is not registered even after crawls",
                    short_id(parent_id),
                    short_id(image_id),
                )
                await register_image(state, parent_id)
                state.images[parent_id].children.add(image_id)
        else:
            log().error("registered image %s does not exist anymore", short_id(image_id))
            await unregister_image(state, image_id)


async def image_from(docker_client: Docker, ident: str) -> None | ImageInspect:
    """Retrieve image details from an image identifier"""
    with suppress(DockerError):
        try:
            return ImageInspect(**await docker_client.images.inspect(ident))
        except ValueError as exc:
            log().error("could not resolve '%s', got '%r'", ident, exc)
            raise
    return None


async def register_image(state: DockerState, image_id: str) -> None:
    """Put an image into set of known images"""
    log().debug("fetch inspect data for and register image '%s'..", short_id(image_id))
    inspect = ImageInspect(**await state.client().images.inspect(image_id))
    raw_history = await state.client().images.history(inspect.Id)
    state.images[inspect.Id] = Image(inspect, [ImageHistoryElement(**hist) for hist in raw_history])
    if inspect.Parent:
        if inspect.Parent not in state.images:
            await register_image(state, inspect.Parent)
        state.images[inspect.Parent].children.add(inspect.Id)
    await state.inform("image_add", inspect.Id)


async def unregister_image(state: DockerState, image_id: str) -> None:
    """Remove an image from set of known images"""
    # todo? assert image_from() results None
    if image_id not in state.images:
        return
    for deleted_tag in state.images[image_id].tags:
        await unregister_reference(state, deleted_tag)
    parent_id = state.images[image_id].parent
    del state.images[image_id]
    await unregister_reference(state, image_id)
    if parent_id:
        try:
            state.images[parent_id].children.remove(image_id)
        except KeyError:
            log().error(
                "Could not remove %s from it's parent %s: does not exist",
                short_id(image_id),
                short_id(parent_id),
            )
    await state.inform("image_del", image_id)


async def update_image_registration(state: DockerState, image_id: str) -> None:
    """Update internally held information about image with given @image_id"""
    if inspect := await image_from(state.client(), image_id):
        if inspect.Id in state.images:
            image = state.images[inspect.Id]
            tags_before = image.tags
            tags_new = inspect.tags
            for deleted_tag in tags_before - tags_new:
                await unregister_reference(state, deleted_tag)
            state.images[inspect.Id].inspect = inspect
            # tag for tag in (image["RepoTags"] or []) if tag != "<none>:<none>"
            await state.inform("image_update", inspect.Id)
        else:
            await register_image(state, inspect.Id)
    else:
        await unregister_image(state, image_id)


async def crawl_volumes(state: DockerState) -> None:
    """Crawls volumes"""
    raw_volumes = await state.client().volumes.list()  # type: ignore[no-untyped-call]
    volumes = {vol["Name"]: Volume(**vol) for vol in raw_volumes["Volumes"]}
    log().info("crawl (%d) volumes..", len(volumes))
    for volume_name, volume in volumes.items():
        if volume_name in state.volumes:
            continue
        if state.images_crawled:
            log().warning("  found unregistered volume %s", short_id(volume_name))
        log().debug("  %s", volume_name)
        # todo: proper register fn
        state.volumes[volume_name] = volume

    if not state.volumes_crawled:
        state.volumes_crawled = True
        log().info("initial volume crawl done")

    if raw_volumes["Warnings"]:
        log().warning("  VolumesWarnings: %s", raw_volumes["Warnings"])
    for volume_name in list(state.volumes):
        if volume_name not in volumes:
            log().error("registered volumes %s does not exist anymore", short_id(volume_name))
            # todo: proper register fn
            del state.volumes[volume_name]


async def crawl_networks(state: DockerState) -> None:
    """Crawls networks"""
    networks = {net["Id"]: Network(**net) for net in await state.client().networks.list()}
    log().info("crawl (%d) networks..", len(networks))
    for net_id, network in networks.items():
        if net_id in state.networks:
            continue
        if state.images_crawled:
            log().warning("  found unregistered network %s", net_id)
        log().debug("  %s", net_id)
        # todo: proper register fn
        state.networks[net_id] = network

    if not state.networks_crawled:
        state.networks_crawled = True
        log().info("initial network crawl done")

    for net_id in list(state.networks):
        if net_id not in networks:
            log().error("registered network %s does not exist anymore", short_id(net_id))
            # todo: proper register fn
            del state.networks[net_id]


async def register_reference(state: DockerState, ident: str, timestamp: float) -> None:
    """Remember the exact time we've last seen @ident"""
    if "@sha256:" in ident or is_uid(ident):
        return
    effective_ident = unique_ident(ident)
    if timestamp > state.last_referenced.get(effective_ident, 0):
        state.last_referenced[effective_ident] = int(timestamp)
        await state.inform("reference_update", str(effective_ident), effective_ident)


async def unregister_reference(state: DockerState, ident: str) -> None:
    """Forget the time we've last seen @ident"""
    effective_ident = unique_ident(ident)
    with suppress(KeyError):
        del state.last_referenced[effective_ident]
        await state.inform("reference_del", str(effective_ident))


async def handle_docker_event(state: DockerState, event: DockerEvent) -> None:
    """Modify current Docker state based on incoming Docker events"""
    # tstamp, object_type, operator, _cmd, uid, params = event_from(line)
    tstamp, event_type, event_action, event_id = (
        event.timeNano // 1_000_000_000,
        event.Type,
        event.Action.split(":", maxsplit=1)[0],
        event.Actor.ID,
    )
    assert tstamp >= state.event_horizon, (
        f"event timestamp {tstamp}" f" < event horizon {state.event_horizon}"
    )

    log().debug(
        "EVENT %d/%s, %s ID=%s",
        tstamp,
        date_from(tstamp),
        f"({event_type:<10s}{event_action:>13s})",
        unique_ident(event_id),
    )

    if event_type == "container":
        if event_action in {
            "create",
        }:
            container = await container_from(state.client(), event_id)
            if not container:
                raise RuntimeError(f"Container {event_id} does not exist after 'create'")
            await register_container(state, container)
            await register_reference(state, event.Actor.Attributes["image"], tstamp)
            return

        if event_action in {
            "destroy",
        }:
            # not needed, since watch_container() already takes care..
            # unregister_container(uid, global_state)

            if await container_from(state.client(), event_id):
                await state.inform(
                    "error", f"container {event_id} still alive after {event_action}"
                )
            return

        if event_action in {
            "exec_create",
            "exec_start",
            "exec_die",
            "commit",
            "pause",
            "rename",
            "unpause",
            "health_status",
            "kill",
            "start",
            "attach",
            "top",
            "prune",
            "die",
            "stop",
            "resize",
            "archive-path",
        }:
            if (
                event_action not in {"prune", "destroy", "die", "stop", "exec_die"}
                and not await container_from(state.client(), event_id)
                and state.containers_crawled
            ):
                log().warning(
                    "Event.action is '%s' but container %s does not exist",
                    event_action,
                    short_id(event_id),
                )
            return

    elif event_type == "image":
        if event_action in {
            "pull",
        }:
            await register_image(state, event_id)
            return

        if event_action in {
            "tag",
            "untag",
            "prune",
            "delete",
        }:
            if event_action == "prune":
                if event.Actor.ID == "":
                    return
                log().error("Event 'image-prune', but Actor.ID is not empty!")

            # NOTE: image tag/untag has NO REFERENCE to the added/removed tag!!
            await update_image_registration(state, event_id)

            # if event_action == "tag":
            #    if 'org.opencontainers.image.ref.name' in event.Actor.Attributes:
            #        await register_reference(state, (
            #            f"{event.Actor.Attributes['org.opencontainers.image.ref.name']}:"
            #            f"{event.Actor.Attributes['org.opencontainers.image.version']}"), tstamp)

            return

        if event_action in {
            "push",
            "save",
        }:
            return

    elif event_type == "network":
        if event_action in {
            "connect",
            "disconnect",
            "destroy",
            "prune",
        }:
            return

    elif event_type == "volume":
        if event_action in {
            "create",
            "mount",
            "unmount",
            "destroy",
        }:
            return
    elif event_type == "builder":
        if event_action in {
            "prune",
        }:
            return

    log().warning("unknown type/operator %s %s", event_type, event_action)


async def main() -> None:
    """Asynchronously run reference application"""
    from rich.logging import RichHandler  # pylint: disable=import-outside-toplevel

    async def printprune(docker_state: DockerState) -> None:
        await asyncio.sleep(2)
        await docker_state.prune_builder_cache()

    async def listen_messages(docker_state: DockerState) -> None:
        """Print messages"""
        async for mtype, mtext, mobj in docker_state.wait_for_change():
            if mtype == "exception":
                try:
                    raise mobj  # type: ignore[misc]
                except Exception:  # pylint: disable=broad-except
                    log().exception(mtext)
            elif mtype == "error":
                log().error(mtext)
            elif mtype in {"container_add", "container_del", "container_update"}:
                cnt: Container = cast(Container, mobj)
                log().info(
                    "container info: %s / %s (%d total)",
                    cnt.short_id,
                    mtype,
                    len(docker_state.containers),
                )
            elif mtype in {"image_add", "image_del", "image_update"}:
                image = docker_state.images[mtext]

                log().info(
                    "image info: %s / %s (%d total)",
                    image.short_id,
                    mtype,
                    len(docker_state.images),
                )
            elif mtype in {"reference_update", "reference_del"}:
                ident = cast(ImageIdent, mobj)
                log().info(
                    "reference updated: %s (%d total)",
                    ident,
                    len(docker_state.last_referenced),
                )
            else:
                log().error("don't know message type %s", mtype)

    logging.basicConfig(
        format="│ %(name)-10s │ %(message)s",
        handlers=[RichHandler(show_path=False, markup=True, show_time=False)],
    )
    logging.getLogger().setLevel(logging.WARNING)
    log().setLevel(logging.DEBUG)

    await asyncio.gather(
        (docker_state := DockerState()).run(),
        listen_messages(docker_state),
        printprune(docker_state),
    )


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
