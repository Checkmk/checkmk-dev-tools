#!/usr/bin/env python3

"""
https://www.baeldung.com/ops/docker-image-layers-sizes

docker container ls -f 'status=exited' -f 'status=dead' -f 'status=created'

Deleted Containers:
8eaedf28c5057d178662a97e0d673888978d7506d6dfd29b30fa916f1bc30142
...

Deleted Networks:
user_admin_panel_integration-testing-network

Deleted Images:
deleted: sha256:a538cdb9a6613b73214171092b03f84fb3537d3edb4e842a7af49991ce2099ab
...

Deleted build cache objects:
4nmqg2nvgi2xcs3pt4vinhqbm
...

"""

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import ClassVar

from aiodocker import Docker, DockerError
from aiodocker.images import DockerImages


class Deserializable:
    IgnoreKeys: ClassVar[set[str]] = set()

    @staticmethod
    def _from_dict(cls, data):
        """translate"""
        if hasattr(cls, "__dataclass_fields__"):
            if undefined_keys := cls.__annotations__.keys() - data.keys():
                print("Warn: {cls.__name__}() no values for", undefined_keys)
            if (
                ignored_keys := data.keys()
                - cls.__annotations__.keys()
                - getattr(cls, "IgnoreKeys", set())
            ):
                print(f"Warn: {cls.__name__}() ignoring keys {ignored_keys}")
            return cls(
                **{
                    k: Deserializable._from_dict(v, data.get(k))
                    for k, v in cls.__annotations__.items()
                }
            )
        return data

    @classmethod
    def from_dict(cls, data):
        return Deserializable._from_dict(cls, data)


@dataclass
class ContainerShowConfig(Deserializable):
    Labels: dict[str, str]
    Volumes: Sequence[dict[str, object]]
    IgnoreKeys = {
        "User",
        "AttachStderr",
        "Env",
        "AttachStdin",
        "Domainname",
        "Tty",
        "Cmd",
        "OpenStdin",
        "WorkingDir",
        "Hostname",
        "Entrypoint",
        "AttachStdout",
        "OnBuild",
        "Image",
        "StdinOnce",
    }


@dataclass
class ContainerShowState(Deserializable):
    Running: str
    Status: str
    OOMKilled: bool
    Dead: bool
    Error: int
    IgnoreKeys = {
        "StartedAt",
        "Pid",
        "FinishedAt",
        "ExitCode",
        "Paused",
        "Restarting",
    }


@dataclass
class ContainerShow(Deserializable):
    Id: str
    Name: str
    Created: str
    Image: str
    Mounts: str
    State: ContainerShowState
    Args: Sequence[str]
    Config: ContainerShowConfig
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
        "HostConfig",
        "GraphDriver",
        "LogPath",
        "AppArmorLabel",
    }

    def __str__(self) -> str:
        return f"{self.Id[:12]}{self.Name:<20s} {self.State.Status} {self.Created[:19]} {self.Config.Volumes}"


@dataclass
class Volume(Deserializable):
    Name: str
    Labels: str
    CreatedAt: str
    Mountpoint: str
    IgnoreKeys = {"Driver", "Scope", "Options"}
    __str__ = lambda self: f"{self.Name[:12]} {self.CreatedAt[:19]} {self.Mountpoint}"


@dataclass
class Network(Deserializable):
    Id: str
    Name: str
    Created: str
    Containers: str
    IgnoreKeys = {
        "Driver",
        "Options",
        "Ingress",
        "Scope",
        "Attachable",
        "ConfigFrom",
        "Containers",
        "IPAM",
        "EnableIPv6",
        "ConfigOnly",
        "Internal",
        "Labels",
    }
    __str__ = lambda self: f"{self.Id[:12]} {self.Created[:19]} {self.Containers}"


@dataclass
class Image(Deserializable):
    Id: str
    Created: int
    Containers: str
    RepoDigests: Sequence[str]
    RepoTags: Sequence[str]
    Labels: Sequence[str]
    Size: int
    SharedSize: int
    VirtualSize: int
    ParentId: str
    # IgnoreKeys = {'RepoTags', 'Created', 'Size', 'ParentId', 'VirtualSize', 'SharedSize'}
    __str__ = lambda self: f"{self.Id[:12]} {self.Created}"


@dataclass
class ImageInspect(Deserializable):
    Id: str
    RootFS: str
    IgnoreKeys = {
        "ContainerConfig",
        "Os",
        "Author",
        "Parent",
        "Created",
        "Container",
        "Config",
        "RepoDigests",
        "Comment",
        "Architecture",
        "DockerVersion",
        "Size",
        "Metadata",
        "VirtualSize",
        "RepoTags",
        "GraphDriver",
    }


@dataclass
class ImageHistory(Deserializable):
    Id: str
    CreatedBy: str
    Created: int
    Tags: Sequence[str]
    Size: int
    Comment: str


async def main() -> None:
    """Simulate what would be removed on docker system prune"""
    async with Docker() as docker_client:
        while True:
            containers = await docker_client.containers.list(all=True)
            print(f"Containers: {len(containers)}")
            async for container in (
                ContainerShow.from_dict(await cont.show()) for cont in containers
            ):
                print(f"  {container}")

            images = await docker_client.images.list(all=True)
            print(f"Images: {len(images)}")
            for image in map(Image.from_dict, images):
                inspect = ImageInspect.from_dict(await docker_client.images.inspect(image.Id))
                history = list(
                    map(ImageHistory.from_dict, await docker_client.images.history(image.Id))
                )
                print(f"  {image}")

            volumes = await docker_client.volumes.list()
            print(f"Volumes: {len(volumes['Volumes'])}")
            for volume in (Volume.from_dict(vol) for vol in volumes["Volumes"]):
                print(f"  {volume}")
            if volumes["Warnings"]:
                print(f"VolumesWarnings: {volumes['Warnings']}")

            # networks = await docker_client.networks.list()
            # print(f"Networks: {len(networks)}")
            # for network in map(Network.from_dict, networks):
            #    print(f"  {network}")

            await asyncio.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
