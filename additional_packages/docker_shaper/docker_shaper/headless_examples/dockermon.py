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

from rich.logging import RichHandler
from textual import on, work
from textual.app import App, ComposeResult
from textual.events import Message
from textual.scrollbar import ScrollTo
from textual.widgets import RichLog, Tree

from docker_shaper import docker_state, dynamic, utils
from docker_shaper.dynamic import Container, ImageIdent


def log() -> logging.Logger:
    """Returns the logger instance to use here"""
    return logging.getLogger("dockermon")


class RichLogHandler(RichHandler):
    """Redirects rich.RichHanlder capabilities to a textual.RichLog"""

    def __init__(self, widget: RichLog):
        super().__init__(show_path=False, markup=True, show_time=False)
        self.widget: RichLog = widget

    def emit(self, record: logging.LogRecord) -> None:
        self.widget.write(
            self.render(
                record=record,
                message_renderable=self.render_message(record, self.format(record)),
                traceback=None,
            )
        )


class LockingRichLog(RichLog):
    @on(ScrollTo)
    def on_scroll_to(self, _event: Message) -> None:
        self.auto_scroll = self.is_vertical_scroll_end


class DockerMon(App[None]):
    """Tree view for Jenkins upstream vs. JJB generated jobs"""

    CSS = "RichLog {height: 20; border: solid grey;}"

    def __init__(self) -> None:
        super().__init__()
        self._richlog = LockingRichLog()
        self.docker_stats_tree: Tree[None] = Tree("Docker stats")
        self.removal_patterns: Mapping[str, int] = {}

    def compose(self) -> ComposeResult:
        """Set up the UI"""
        yield self.docker_stats_tree
        yield self._richlog

    @work(exit_on_error=True)
    async def run_docker_stats(self) -> None:
        """Runs the docker-stats 'daemon' in background"""
        self.docker_state = docker_state.DockerState()
        await self.docker_state.run()

    @work(exit_on_error=True)
    async def produce(self) -> None:
        """Continuously updates Docker elements tree"""
        container_nodes = {}
        containers_node = self.docker_stats_tree.root.add("Containers")

        image_nodes = {}
        images_node = self.docker_stats_tree.root.add("Images")

        reference_nodes = {}
        references_node = self.docker_stats_tree.root.add("References")

        network_nodes = {}
        networks_node = self.docker_stats_tree.root.add("Networks")

        volume_nodes = {}
        volumes_node = self.docker_stats_tree.root.add("Volumes")

        self.docker_stats_tree.root.expand_all()

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
                "waiting for elements to be crawled"
                f" (cont: {self.docker_state.containers_crawled})"
                f" (images: {self.docker_state.images_crawled})"
                f" (volumes: {self.docker_state.volumes_crawled})"
                f" (networks: {self.docker_state.networks_crawled})"
            )
            await asyncio.sleep(1)

        # add all containers
        for container in self.docker_state.containers.values():
            container_nodes[container.id] = containers_node.add(f"{container}")

        # add all images
        for img in self.docker_state.images.values():
            img_node = image_nodes[img.id] = images_node.add(f"{img}", expand=True)
            for tag in img.tags:
                dep_age, reason = dynamic.expiration_age_from_image_name(
                    self.removal_patterns, tag, 666
                )
                img_node.add(
                    f"dep_age=[sky_blue2]{dep_age:10d}[/]"
                    f" [bold]{tag}[/] '[sky_blue2]{reason}[/]'"
                )

        # add all volumes
        for volume in self.docker_state.volumes.values():
            volume_nodes[volume.Name] = volumes_node.add(f"{volume}")

        # add all networks
        for network in self.docker_state.networks.values():
            network_nodes[network.Id] = networks_node.add(f"{network}")

        async for mtype, mtext, mobj in self.docker_state.wait_for_change():
            self.docker_stats_tree.root.set_label(
                f"{utils.get_hostname()}"
                f" / {utils.date_str(self.docker_state.event_horizon)}"
                f" / {utils.dur_str(int(time.time()) - self.docker_state.event_horizon)}"
            )

            if mtype == "exception":
                log().exception("%s: %s", mtext, mobj)
            elif mtype == "error":
                log().error(mtext)

            elif mtype in {"container_add", "container_del", "container_update"}:
                cnt: Container = cast(Container, mobj)
                log().info(
                    "container info: %s / %s (%d total)",
                    cnt.short_id,
                    mtype,
                    len(self.docker_state.containers),
                )
                if mtype == "container_add" and cnt.id not in container_nodes:
                    container_nodes[cnt.id] = containers_node.add(f"{cnt}")
                if mtype == "container_update":
                    container_nodes[cnt.id].set_label(f"{cnt} - {cnt.cpu_usage() *100:.2f}%")
                if mtype == "container_del" and cnt.id in container_nodes:
                    container_nodes[cnt.id].remove()
                    del container_nodes[cnt.id]

            elif mtype in {"image_add", "image_del", "image_update"}:
                image = self.docker_state.images[mtext]

                log().info(
                    "image info: %s / %s (%d total)",
                    image.short_id,
                    mtype,
                    len(self.docker_state.images),
                )
                if mtype == "image_add" and image.id not in image_nodes:
                    image_nodes[image.id] = containers_node.add(f"{image}")
                if mtype == "image_del" and image.id in image_nodes:
                    image_nodes[image.id].remove()
                    del image_nodes[image.id]
                if mtype == "image_update":
                    image_nodes[image.id].set_label(f"{image} - +")

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
        logging.getLogger().handlers = [handler := RichLogHandler(self._richlog)]
        handler.setFormatter(
            logging.Formatter(
                "│ %(asctime)s │ [grey53]%(funcName)-32s[/] │ [bold white]%(message)s[/]",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        with suppress(FileNotFoundError):
            config = utils.load_module(Path("~/.docker_shaper").expanduser() / "config.py")
            self.removal_patterns = config.removal_rules(111)

        self.run_docker_stats()
        self.produce()


def main():
    logging.getLogger().setLevel(logging.DEBUG)
    log().setLevel(logging.DEBUG)
    DockerMon().run()


if __name__ == "__main__":
    main()
