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

import logging

from rich.logging import RichHandler
from textual import work
from textual.app import App, ComposeResult
from textual.widgets import RichLog, Tree

from docker_shaper import docker_state


def log() -> logging.Logger:
    """Returns the logger instance to use here"""
    return logging.getLogger("dockermon")


class RichLogHandler(RichHandler):
    """Redirects rich.RichHanlder capabilities to a textual.RichLog"""

    def __init__(self, widget: RichLog):
        super().__init__(show_path=False, markup=False, show_time=False, show_level=False)
        self.widget: RichLog = widget

    def emit(self, record: logging.LogRecord) -> None:
        self.widget.write(
            self.render(
                record=record,
                message_renderable=self.render_message(record, self.format(record)),
                traceback=None,
            )
        )


class DockerMon(App[None]):
    """Tree view for Jenkins upstream vs. JJB generated jobs"""

    def __init__(self) -> None:
        super().__init__()
        self._richlog = RichLog()
        self.docker_stats_tree: Tree[None] = Tree("Docker stats")

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
        containers_node = self.docker_stats_tree.root.add("Containers")
        images_node = self.docker_stats_tree.root.add("Images")
        references_node = self.docker_stats_tree.root.add("References")
        networks_node = self.docker_stats_tree.root.add("Networks")
        volumes_node = self.docker_stats_tree.root.add("Volumes")
        self.docker_stats_tree.root.expand_all()

        async for mtype, mtext, mobj in self.docker_state.wait_for_change():
            if mtype == "exception":
                log().exception("%s: %s", mtext, mobj)
            elif mtype == "error":
                log().error(mtext)

            elif mtype in {"container_add", "container_del", "container_update"}:
                print(f"CONTAINER {mtype} {mtext}")
                print(f"total containers: {len(self.docker_state.containers)}")
                containers_node.remove_children()
                for cnt_id, cnt in self.docker_state.containers.items():
                    containers_node.add(f"{cnt}")

            # elif mtype == :
            #    print(f"CONTAINER {mtype} {mtext}")

            elif mtype in {"image_add", "image_del", "image_update"}:
                print(f"IMAGE {mtype} {mtext}")
                print(f"total images: {len(self.docker_state.images)}")
                images_node.remove_children()
                for img_id, img in self.docker_state.images.items():
                    images_node.add(f"{img}")

            elif mtype in {"reference_update", "reference_del"}:
                print(f"UPDATE REFERENCE {mtext}")
                print(f"total references: {len(self.docker_state.last_referenced)}")
                references_node.remove_children()
                for ident, timestamp in self.docker_state.last_referenced.items():
                    references_node.add(f"{ident} {timestamp}")

            else:
                log().error("don't know message type %s", mtype)

    async def on_mount(self) -> None:
        """UI entry point"""

        def fmt_filter(record):
            record.levelname = f"[{record.levelname}]"
            record.funcName = f"[{record.funcName}]"
            return True

        logging.getLogger().handlers = [handler := RichLogHandler(self._richlog)]
        handler.setFormatter(
            logging.Formatter(
                "%(levelname)-9s %(asctime)s %(funcName)-22s│ %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handler.addFilter(fmt_filter)
        self.run_docker_stats()
        self.produce()


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.DEBUG)
    log().setLevel(logging.DEBUG)
    DockerMon().run()
