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

Todo:
- [ ] List rules
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import cast

from docker_shaper import docker_state, dynamic, utils
from docker_shaper.dynamic import Container, ImageIdent, Network, Volume, short_id
from textual import work
from textual.app import ComposeResult
from textual.widgets import Tree
from trickkiste.base_tui_app import TuiBaseApp
from trickkiste.misc import date_str, dur_str


def log() -> logging.Logger:
    """Returns the logger instance to use here"""
    return logging.getLogger("dockermon")


def container_markup(container: Container) -> str:
    status_markups = {"running": "cyan bold"}
    image_str, status_str = (
        ("", "", "")
        if not container.show
        else (
            f" image:[bold]{short_id(container.show.Image)}[/]",
            f" - [{status_markups.get(container.show.State.Status, 'grey53')}]"
            f"{container.show.State.Status:7s}[/]",
        )
    )
    return (
        f"[bold]{container.short_id}[/] / {container.name:<26s}{image_str}{status_str}"
        f" - {container.cpu_usage() * 100:7.2f}%"
        f" - {container.mem_usage() >> 20:6d}MiB"
    )


class DockerMon(TuiBaseApp):
    """Tree view for Jenkins upstream vs. JJB generated jobs"""

    def __init__(self) -> None:
        super().__init__()
        self.docker_stats_tree: Tree[None] = Tree("Docker stats")
        self.removal_patterns: Mapping[str, int] = {}
        self.pattern_usage_count: Mapping[str, int] = {}

    def compose(self) -> ComposeResult:
        """Set up the UI"""
        yield self.docker_stats_tree
        yield from super().compose()

    @work(exit_on_error=True)
    async def run_docker_stats(self) -> None:
        """Runs the docker-stats 'daemon' in background"""
        self.docker_state = docker_state.DockerState()
        await self.docker_state.run()

    @work(exit_on_error=True)
    async def produce(self) -> None:
        """Continuously updates Docker elements tree"""
        container_nodes = {}
        containers_node = self.docker_stats_tree.root.add("Containers", expand=True)

        image_nodes = {}
        images_node = self.docker_stats_tree.root.add("Images", expand=False)

        reference_nodes = {}
        references_node = self.docker_stats_tree.root.add("Image-references", expand=True)

        network_nodes = {}
        networks_node = self.docker_stats_tree.root.add("Networks", expand=True)

        volume_nodes = {}
        volumes_node = self.docker_stats_tree.root.add("Volumes", expand=True)

        patterns_node = self.docker_stats_tree.root.add("Image-pattern", expand=False)

        self.docker_stats_tree.root.expand()
        self.docker_stats_tree.root.allow_expand = False

        # from rich import color
        # color_node = self.docker_stats_tree.root.add("Colors", expand=False)
        # for color_str in color.ANSI_COLOR_NAMES:
        #     color_node.add(f"[{color_str}]{color_str}[/]")

        # wait for all items to be registered
        while not all(
            (
                self.docker_state.containers_crawled,
                self.docker_state.images_crawled,
                self.docker_state.volumes_crawled,
                self.docker_state.networks_crawled,
            )
        ):
            log().info(
                "wait for initial crawls (C: %s, I: %s, V: %s, N: %s)",
                self.docker_state.containers_crawled,
                self.docker_state.images_crawled,
                self.docker_state.volumes_crawled,
                self.docker_state.networks_crawled,
            )
            await asyncio.sleep(1)

        # add all containers
        for container in self.docker_state.containers.values():
            container_nodes[container.id] = containers_node.add(f"{container}")

        # add all images
        pattern_issues = []
        for img in self.docker_state.images.values():
            img_node = image_nodes[img.id] = images_node.add(f"{img}", expand=True)
            for tag in img.tags:
                dep_age, reason = dynamic.expiration_age_from_image_name(
                    self.removal_patterns, tag, 666
                )
                reason_markup = "bold red"
                if reason in self.removal_patterns:
                    if reason not in self.pattern_usage_count:
                        self.pattern_usage_count[reason] = 0
                    self.pattern_usage_count[reason] += 1
                    reason_markup = "sky_blue2"
                else:
                    pattern_issues.append(f"{tag} # {reason}")
                img_node.add(
                    f"dep_age=[sky_blue2]{dep_age:10d}[/]"
                    f" [bold]{tag}[/] '[{reason_markup}]{reason}[/]'"
                )

        # add all volumes
        for volume in self.docker_state.volumes.values():
            volume_nodes[volume.Name] = volumes_node.add(f"{volume}")

        # add all networks
        for network in self.docker_state.networks.values():
            network_nodes[network.Id] = networks_node.add(f"{network}")

        # add all pattern
        for issue in pattern_issues:
            patterns_node.add(f"[bold red]{issue}[/]'")
        for pattern, dep_age in self.removal_patterns.items():
            usage_count = self.pattern_usage_count.get(pattern, 0)
            if usage_count == 0:
                pattern_issues.append(pattern)
            patterns_node.add(f"{usage_count:3d}: r'[sky_blue2]{pattern}[/]'")
            # network_nodes[network.Id] = networks_node.add(f"{network}")

        with open(Path("~/.docker_shaper").expanduser() / "pattern-issues.txt", "w") as issues_file:
            issues_file.write("\n".join(pattern_issues))

        patterns_node.set_label(f"Image-pattern ({len(self.removal_patterns)})")

        async for mtype, mtext, mobj in self.docker_state.wait_for_change():
            self.docker_stats_tree.root.set_label(
                f"{utils.get_hostname()}"
                f" / horizon={date_str(self.docker_state.event_horizon)}"
                f" ({dur_str(int(time.time()) - self.docker_state.event_horizon)})"
            )

            if mtype == "exception":
                log().exception("%s: %s", mtext, mobj)

            elif mtype == "error":
                log().error(mtext)

            elif mtype == "warning":
                log().warning(mtext)

            elif mtype == "info":
                log().info(mtext)

            elif mtype == "client_disconnect":
                raise SystemExit(1)

            elif mtype in {"container_add", "container_del", "container_update"}:
                cnt: Container = cast(Container, mobj)
                log().info(
                    "container info: '%s' / %s (%d total)",
                    cnt.short_id,
                    mtype,
                    len(self.docker_state.containers),
                )
                if mtype == "container_add" and cnt.id not in container_nodes:
                    container_nodes[cnt.id] = containers_node.add(f"{cnt}")
                if mtype == "container_update":
                    container_nodes[cnt.id].set_label(container_markup(cnt))
                if mtype == "container_del" and cnt.id in container_nodes:
                    container_nodes[cnt.id].remove()
                    del container_nodes[cnt.id]

                total_cpu = sum(map(lambda c: c.cpu_usage(), self.docker_state.containers.values()))
                total_mem = sum(map(lambda c: c.mem_usage(), self.docker_state.containers.values()))
                containers_node.set_label(
                    f"Containers ({len(self.docker_state.containers):2d})"
                    f" {' ' * 56} [bold]{total_cpu * 100:7.2f}% - {total_mem >> 20:6d}MiB[/]"
                )

            elif mtype in {"image_add", "image_del", "image_update"}:
                image_id = mtext

                log().info(
                    "image info: '%s' / %s (%d total)",
                    short_id(image_id),
                    mtype,
                    len(self.docker_state.images),
                )
                if mtype == "image_del":
                    if image_id in image_nodes:
                        image_nodes[image_id].remove()
                        del image_nodes[image_id]
                    continue
                image = self.docker_state.images[image_id]
                if mtype == "image_add" and image.id not in image_nodes:
                    image_nodes[image.id] = images_node.add(f"{image}")
                if mtype == "image_update":
                    image_nodes[image.id].set_label(f"{image} - +")

                images_node.set_label(f"Images ({len(self.docker_state.images)})")

            elif mtype in {"volume_add", "volume_del"}:
                volume_id = mtext

                log().info(
                    "volume info: '%s' / %s (%d total)",
                    short_id(volume_id),
                    mtype,
                    len(self.docker_state.volumes),
                )
                if mtype == "volume_add" and volume_id not in volume_nodes:
                    vol: Volume = cast(Volume, mobj)
                    volume_nodes[volume_id] = volumes_node.add(f"{vol}")
                if mtype == "volume_del":
                    if volume_id in volume_nodes:
                        volume_nodes[volume_id].remove()
                        del volume_nodes[volume_id]
                volumes_node.set_label(f"Volumes ({len(self.docker_state.volumes)})")

            elif mtype in {"network_add", "network_del"}:
                network_id = mtext

                log().info(
                    "network info: '%s' / %s (%d total)",
                    short_id(network_id),
                    mtype,
                    len(self.docker_state.networks),
                )
                if mtype == "network_add" and network_id not in network_nodes:
                    netw: Network = cast(Network, mobj)
                    network_nodes[network_id] = networks_node.add(f"{netw}")
                if mtype == "network_del":
                    if network_id in network_nodes:
                        network_nodes[network_id].remove()
                        del network_nodes[network_id]
                networks_node.set_label(f"Networks ({len(self.docker_state.networks)})")

            elif mtype in {"reference_update", "reference_del"}:
                ident = cast(ImageIdent, mobj)
                log().info(
                    "reference updated: %s (%d total)",
                    ident,
                    len(self.docker_state.last_referenced),
                )
                if mtype == "reference_update":
                    if mtype in reference_nodes:
                        reference_nodes[ident].set_label(
                            f"{ident} - {self.docker_state.last_referenced[ident]} - +"
                        )
                    else:
                        reference_nodes[ident] = references_node.add(
                            f"{ident} - {self.docker_state.last_referenced[ident]}"
                        )
                if mtype == "reference_del" and ident in reference_nodes:
                    reference_nodes[ident].remove()
                    del reference_nodes[ident]

            else:
                log().error("don't know message type %s", mtype)

    async def on_mount(self) -> None:
        """UI entry point"""
        with suppress(FileNotFoundError):
            config = utils.load_module(Path("~/.docker_shaper").expanduser() / "config.py")
            self.removal_patterns = config.removal_rules(111)

        self.run_docker_stats()
        self.produce()


def main() -> None:
    logging.getLogger().setLevel(logging.WARNING)
    log().setLevel(logging.DEBUG)
    DockerMon().execute()


if __name__ == "__main__":
    main()
