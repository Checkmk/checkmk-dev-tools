#!/usr/bin/env python3

"""Runs the DockerShaper TUI in an auto-reloading and asynchronous way

Connect to a given build node using the following command:

  ssh -t root@build-fra-005 "su - jenkins -c 'screen -r docker-shaper'"
"""
# pylint: disable=too-many-instance-attributes
# _pylint: disable=fixme

# TODO:
# * [ ] fix docker-shaper events and updates
# * [ ] consolidate image pattern with nexus script
# * [ ] create Nexus Rules

# - [-] async cleanup (return from button press)
# - [-] turn cleanup/crawler buttons red to indicate progress
# - [ ] show images/containers/disk space
# - [ ] show width/TERM / encoding
# - [ ] Button: run crawler
# - [ ] fix container tracing
# - [ ] tag rules to config.cfg
# - [ ] builder prune rules to config.cfg
# - [ ] restart gracefully after docker.socket restart

import asyncio
import logging
import logging.handlers
import sys
from collections.abc import MutableMapping
from pathlib import Path

from apparat import fs_changes
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.widgets import Button, Header, Label, Tree
from trickkiste.base_tui_app import TuiBaseApp

from docker_shaper import dynamic
from docker_shaper.utils import get_hostname

CONFIG_FILE = dynamic.BASE_DIR / "config.py"

__version__ = "2.0.1"  # It MUST match the version in pyproject.toml file


def log() -> logging.Logger:
    """Returns the logger instance to use here"""
    return logging.getLogger("docker-shaper")


class DockerShaper(TuiBaseApp):
    """Tree view for Jenkins upstream vs. JJB generated jobs"""

    CSS = """
        Header {text-style: bold;}
        #dashboard {
            grid-size: 2;
            padding: 1;
            grid-rows: auto;
            grid-gutter:1;
            height: auto;
        }
        Button  {width: 40;}
        Tree {padding: 1;}
        Tree > .tree--guides {
            color: $success-darken-3;
        }
        Tree > .tree--guides-selected {
            text-style: none;
            color: $success-darken-1;
        }
        #button_grid {
            grid-size: 3;
            height: auto;
        }
    """

    BINDINGS = [
        # We don't want the user to accidentally quit DockerShaper by pressing CTRL-C instead of
        # detatching a screen or tmux session. So we inform them to press CTRL-Q instead
        Binding("ctrl+q", "quit"),
        Binding("ctrl+c", "inform_ctrlc_deactivated"),
    ]

    def __init__(self) -> None:
        super().__init__(logger_show_funcname=False)

        self.docker_stats_tree: Tree[None] = Tree("Docker stats")
        self.containers_node = self.docker_stats_tree.root.add("Containers", expand=True)
        self.images_node = self.docker_stats_tree.root.add("Images", expand=False)
        self.references_node = self.docker_stats_tree.root.add("Image-references", expand=False)
        self.networks_node = self.docker_stats_tree.root.add("Networks", expand=False)
        self.volumes_node = self.docker_stats_tree.root.add("Volumes", expand=False)
        self.patterns_node = self.docker_stats_tree.root.add("Image-pattern", expand=False)

        self.removal_patterns: MutableMapping[str, int] = {}
        self.pattern_usage_count: MutableMapping[str, int] = {}
        self.global_state = dynamic.GlobalState()

        try:
            mod_config = dynamic.load_config(self.global_state, CONFIG_FILE)
            self.removal_patterns = mod_config.removal_rules(111)
        except FileNotFoundError:
            log().warning("no config file found at %s", CONFIG_FILE)

        self.lbl_event_horizon = Label("event horizon")
        self.lbl_runtime = Label("runtime")
        self.lbl_switches = Label("switches")
        self.lbl_expiration = Label("expiration ages")
        self.lbl_stats1 = Label()
        self.lbl_stats2 = Label()
        self.lbl_clean_interval = Label("cleanup interval")
        self.btn_clean = Button("clean", id="clean")

        self.title = f"DockerShaper on {get_hostname()}"

    async def initialize(self) -> None:
        """Executed as soon as UI is ready. Called by parent().on_mount()"""
        self.set_log_levels("ALL_DEBUG")

        self.update_dashboard()
        self.run_docker_stats()
        self.maintain_docker_stats_tree()
        self.watch_fs_changes()
        self.schedule_cleanup()
        dynamic.report(self.global_state, "info", "docker-shaper started")

    def write_message(self, text: str) -> None:
        """Write a message in log window unconditionally"""
        self._richlog.write(text)

    def compose(self) -> ComposeResult:
        """Set up the UI"""
        config_file_str = CONFIG_FILE.as_posix().replace(Path.home().as_posix(), "~")
        yield Header(show_clock=True, id="header")
        with Vertical():
            with Grid(id="dashboard"):
                yield self.btn_clean
                yield Label(
                    "[bright_black]Cleanup is done automatically based on the rules below."
                    "\nOnly use when you know what you're doing[/]"
                )
                yield self.lbl_event_horizon
                yield self.lbl_runtime
                yield Label(f"Config file: [bold cyan]{config_file_str}[/]")
                yield Label(
                    "[bright_black]Edit this file to configure DockerShaper. Changes "
                    "\nwill be applied automatically.[/]"
                )
                yield self.lbl_switches
                yield self.lbl_expiration
                yield self.lbl_stats1
                yield self.lbl_stats2
            yield self.docker_stats_tree
            with Grid(id="button_grid"):
                yield Button(
                    f"rotate log level ({logging.getLevelName(log().level)})", id="rotate_log_level"
                )
                yield Button("dump trace", id="dump_trace")
                yield Button("quit (don't!)", id="quit")

        yield from super().compose()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Generic button press handler"""
        try:
            await dynamic.on_button_pressed(self, event)
        except Exception:  # pylint: disable=broad-except
            dynamic.report(self.global_state)

    async def action_inform_ctrlc_deactivated(self) -> None:
        """See BINDINGS"""
        log().error("CTRL-C is deactivated to avoid unintentional shutdown. Press CTRL-Q instead.")

    def update_node_labels(self) -> None:
        """Fills some labels with useful information"""
        try:
            dynamic.update_node_labels(self)
        except Exception:  # pylint: disable=broad-except
            dynamic.report(self.global_state)

    @work(exit_on_error=True)
    async def update_dashboard(self) -> None:
        """Continuously write some internal stuff to log"""
        while True:
            try:
                await dynamic.update_dashboard(self)
            except Exception:  # pylint: disable=broad-except
                dynamic.report(self.global_state)
                await asyncio.sleep(5)
            await asyncio.sleep(0)

    @work(exit_on_error=True)
    async def run_docker_stats(self) -> None:
        """Runs the docker-stats 'daemon' in background"""
        await self.global_state.run()

    @work(exit_on_error=True)
    async def maintain_docker_stats_tree(self) -> None:
        """Continuously updates Docker elements tree"""
        while True:
            try:
                await dynamic.maintain_docker_stats_tree(self)
            except Exception:  # pylint: disable=broad-except
                dynamic.report(self.global_state)
                await asyncio.sleep(5)
            await asyncio.sleep(0)

    @work(exit_on_error=True)
    async def schedule_cleanup(self) -> None:
        """Async infinitve loop wrapper for cleanup"""
        while True:
            try:
                await dynamic.schedule_cleanup(self)
            except Exception:  # pylint: disable=broad-except
                dynamic.report(self.global_state)
                await asyncio.sleep(5)

    @work(exit_on_error=True)
    async def watch_fs_changes(self) -> None:
        """Watch for changes on imported files and reload them on demand"""
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

        async for changes in (
            relevant_changes
            async for chunk in fs_changes(
                Path(dynamic.__file__).parent, CONFIG_FILE.parent, min_interval=4, postpone=False
            )
            if (changed_files := set(chunk))
            for loaded_modules in (
                {
                    Path(mod.__file__): mod
                    for mod in sys.modules.values()
                    if hasattr(mod, "__file__") and mod.__file__
                    if not any(infix in mod.__file__ for infix in (".pyenv", ".venv", "wingpro"))
                },
            )
            if (
                relevant_changes := [
                    (path, loaded_modules.get(path))
                    for path in changed_files
                    if path == CONFIG_FILE or path in loaded_modules
                ]
            )
        ):
            try:
                await dynamic.on_changed_file(self.global_state, CONFIG_FILE, changes)
            except Exception:  # pylint: disable=broad-except
                dynamic.report(self.global_state)


def main() -> None:
    """Main entry point"""
    DockerShaper().execute()


if __name__ == "__main__":
    main()
