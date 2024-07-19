#!/usr/bin/env python3

"""Functionality that might change during runtime
"""

# pylint: disable=too-many-instance-attributes
# pylint: disable=too-many-branches
# pylint: disable=too-many-lines
# pylint: disable=too-many-locals
# pylint: disable=too-many-statements
# pylint: disable=missing-function-docstring
# pylint: disable=fixme

import asyncio
import importlib
import json
import logging
import os
import re
import sys
import time
import traceback
from collections.abc import Iterable, Mapping, MutableMapping, MutableSequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import psutil
from aiodocker import Docker, DockerError
from aiodocker.volumes import DockerVolume
from rich.markup import escape as markup_escape
from textual.widgets import Button, Label, Tree
from textual.widgets.tree import TreeNode
from trickkiste.misc import age_str, date_str, dur_str, process_output

from . import __version__, utils
from .docker_state import (
    Container,
    DockerState,
    Image,
    ImageIdent,
    MessageType,
    Network,
    Volume,
    is_uid,
    short_id,
    unique_ident,
)

BASE_DIR = Path("~/.docker_shaper").expanduser()


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper")


@dataclass
class GlobalState:
    """The dirty globally shared state of docker-shaper"""

    intervals: MutableMapping[str, float]
    tag_rules: MutableMapping[str, int]
    messages: MutableSequence[tuple[int, str, str, None | object]]
    switches: MutableMapping[str, bool]
    hostname: str
    expiration_ages: MutableMapping[str, int]
    update_mqueues: set[asyncio.Queue[str]]
    additional_values: MutableMapping[str, object]
    docker_state: DockerState

    def __init__(self) -> None:
        self.docker_state = DockerState()
        self.intervals = {
            "cleanup": 3600,
        }
        self.cleanup_fuse = 0

        self.tag_rules = {}
        self.switches = {}
        self.messages = []
        self.hostname = utils.get_hostname()
        self.expiration_ages = {}
        self.update_mqueues = set()
        self.additional_values = {}

    async def run(self) -> None:
        self.docker_state.import_references(BASE_DIR / "event_horizon.json")
        try:
            await self.docker_state.run()
        finally:
            self.docker_state.export_references(BASE_DIR / "event_horizon.json")

    def new_update_queue(self) -> asyncio.Queue[str]:
        """Creates and returns a new message queue"""
        mqueue: asyncio.Queue[str] = asyncio.Queue()
        self.update_mqueues.add(mqueue)
        log().info("new connection (%d)", len(self.update_mqueues))
        return mqueue

    def remove_queue(self, mqueue: asyncio.Queue[str]) -> None:
        """Removes an existing queue from message queues"""
        self.update_mqueues.remove(mqueue)
        del mqueue
        log().info("closed connection (%d)", len(self.update_mqueues))

    def inform(self, message: str) -> None:
        """Send a message to all connected message queues"""
        for mqueue in self.update_mqueues:
            mqueue.put_nowait(message)

    @property
    def containers(self) -> MutableMapping[str, Container]:
        return self.docker_state.containers

    @property
    def images(self) -> MutableMapping[str, Image]:
        return self.docker_state.images

    @property
    def volumes(self) -> MutableMapping[str, Volume]:
        return self.docker_state.volumes

    @property
    def networks(self) -> MutableMapping[str, Network]:
        return self.docker_state.networks

    @property
    def last_referenced(self) -> MutableMapping[ImageIdent, int]:
        return self.docker_state.last_referenced

    @property
    def docker_client(self) -> Docker:
        assert self.docker_state.docker_client
        return self.docker_state.docker_client


class DockerShaperUI(Protocol):
    """Used to avoid importing DockerShaper TUI class"""

    global_state: GlobalState

    pattern_usage_count: MutableMapping[str, int]
    removal_patterns: MutableMapping[str, int]

    docker_stats_tree: Tree

    containers_node: TreeNode
    images_node: TreeNode
    references_node: TreeNode
    networks_node: TreeNode
    volumes_node: TreeNode
    patterns_node: TreeNode

    lbl_event_horizon: Label
    lbl_runtime: Label
    lbl_switches: Label
    lbl_expiration: Label
    btn_clean: Button

    def exit(self): ...
    def update_status_bar(self, text: str): ...
    def update_node_labels(self) -> None: ...
    def write_message(self, text: str) -> None: ...


def log_file_name(cnt: Container) -> Path:
    (BASE_DIR / "container-logs").mkdir(parents=True, exist_ok=True)
    return (
        BASE_DIR
        / "container-logs"
        / f"{cnt.created_at.strftime('%Y.%m.%d-%H.%M.%S')}-{cnt.short_id}.ndjson"
    )


def write_log_entry(file, data, indent=None):
    file.write(json.dumps(data, indent=indent))
    file.write("\n")


def handle_docker_state_message(
    global_state: GlobalState, mtype: MessageType, mtext: str, mobj: None | object
) -> None:
    try:
        if mtype == "exception":
            try:
                raise mobj  # type: ignore[misc]
            except Exception:  # pylint: disable=broad-except
                report(global_state, message=mtext)

        elif mtype in {"error", "warning", "info"}:
            report(global_state, mtype, mtext, mobj)

        elif mtype == "client_disconnect":
            report(
                global_state,
                "warning",
                "got None from docker events - export references and shutdown",
            )
            raise SystemExit(1)

        elif mtype in {"container_add", "container_del", "container_update"}:
            log().debug(
                "container info: %s / %s (%d total)",
                short_id(mtext),
                mtype,
                len(global_state.docker_state.containers),
            )
            assert hasattr(mobj, "show")  # assert isinstance(mobj, Container) not possible
            cnt: Container = cast(Container, mobj)
            # assert cnt.show
            with open(log_file_name(cnt), "a", encoding="utf-8") as log_file:
                if mtype == "container_add":
                    if not cnt.show:
                        return
                    write_log_entry(log_file, cnt.show.model_dump(mode="json"))
                if mtype == "container_del":
                    # write_log_entry(log_file, cnt.show.model_dump(mode="json"))
                    pass
                if mtype == "container_update":
                    assert cnt.stats
                    write_log_entry(
                        log_file,
                        {
                            "time": int(time.time() - cnt.started_at.timestamp()),
                            "cpu-usage": int(cnt.cpu_usage() * 100) / 100,
                            "mem-usage": cnt.mem_usage(),
                        },
                    )

        elif mtype in {"image_add", "image_del", "image_update"}:
            log().debug(
                "image info: %s / %s (%d total)",
                short_id(mtext),
                mtype,
                len(global_state.docker_state.images),
            )

        elif mtype in {"volume_add", "volume_del"}:
            volume_id = mtext

            log().debug(
                "volume info: '%s' / %s (%d total)",
                short_id(volume_id),
                mtype,
                len(global_state.docker_state.volumes),
            )

        elif mtype in {"network_add", "network_del"}:
            network_id = mtext

            log().debug(
                "network info: '%s' / %s (%d total)",
                short_id(network_id),
                mtype,
                len(global_state.docker_state.networks),
            )

        elif mtype in {"reference_update", "reference_del"}:
            log().info(
                "reference updated: %s (%d total)",
                mtext,
                len(global_state.docker_state.last_referenced),
            )
        else:
            raise RuntimeError(f"don't know message type {mtype}")
    except Exception:  # pylint: disable=broad-except
        report(global_state)


def expiration_age_from_ident(global_state: GlobalState, ident: str) -> tuple[int, str]:
    # TODO: distinguish between container, image and volume
    if is_uid(ident):
        return global_state.expiration_ages["tag_default"], "is_uid=>tag_default"

    # effective_ident = unique_ident(ident)
    return expiration_age_from_image_name(
        global_state.tag_rules, ident, global_state.expiration_ages["tag_unknown"]
    )


def expiration_age_from_image_name(
    tag_rules: Mapping[str, int], image_name: str, default: int
) -> tuple[int, str]:
    matching_rules = tuple(
        (regex, age) for regex, age in tag_rules.items() if re.match(regex, image_name)
    )

    if len(matching_rules) == 1:
        return matching_rules[0][1], matching_rules[0][0]

    if not matching_rules:
        log().warning("No rule found for %r", image_name)
        return default, "no rule=>tag_unknown"

    log().error("Multiple rules found for %s:", image_name)
    for rule in matching_rules:
        log().error("  %s:", rule[0])

    return default, f"multiple_rules=>tag_unknown ({[rule[0] for  rule in  matching_rules]})"


def check_expiration(
    global_state: GlobalState, ident: str, now: int, extra_date: int = 0
) -> tuple[bool, None | int, int, str]:
    uident = unique_ident(ident)

    if uident not in global_state.last_referenced:
        log().debug("%s: no reference yet", ident)

    last_referenced = global_state.last_referenced.get(uident)
    expiration_age, reason = expiration_age_from_ident(global_state, ident)
    # TODO
    # last_referenced, expiration_age = global_state.last_referenced.setdefault(
    #    ident, [None, expiration_age_from_ident(global_state, ident)]
    # )

    if last_referenced is None and (match := re.match(r"^.*(\d{4}\.\d{2}\.\d{2}).*$", ident)):
        # Fallback strategy for the case no reference exists yet but from a date encoded in the
        # image tag we know it's outdated anyway
        tag_date = datetime.strptime(match.group(1), "%Y.%m.%d")
        effective_age = now - max(extra_date, int(tag_date.timestamp()) + 3600 * 24)
    else:
        effective_age = now - max(
            last_referenced or 0,
            global_state.docker_state.event_horizon,
            extra_date,
        )
    return effective_age > expiration_age, last_referenced, expiration_age, reason


def jobname_from(binds):
    candidates = [
        d.replace("/home/jenkins/workspace/", "").replace("/checkout", "")
        for b in binds or []
        for d in (b.split(":")[0],)
        if "workspace" in d
    ]
    if not len(candidates) == len(set(candidates)):
        print(binds)
    return candidates and candidates[0] or "--"


def label_filter(label_values):
    return ",".join(
        w.replace("artifacts.lan.tribe29.com:4000", "A")
        for key, l in label_values.items()
        if key
        in (
            "org.tribe29.base_image",
            "org.tribe29.cmk_branch",
            "org.tribe29.cmk_edition_short",
            "org.tribe29.cmk_hash",
            "org.tribe29.cmk_version",
        )
        for w in l.split()
        if not (w.startswith("sha256") or len(w) == 64)
    )


def would_cleanup_container(global_state: GlobalState, container: Container, now: int) -> bool:
    if not container.show:
        return False
    if container.status == "exited":
        return (
            now - container.finished_at.timestamp()
            > global_state.expiration_ages["container_exited"]
        )
    if container.status == "created":
        return (
            now - container.created_at.timestamp()
            > global_state.expiration_ages["container_created"]
        )
    if container.status == "running":
        return (
            now - container.started_at.timestamp()
            > global_state.expiration_ages["container_running"]
        )
    return False


def expired_idents(global_state: GlobalState, image: Image, now) -> Iterable[tuple[str, str]]:
    log().debug("check expiration for image %s, tags=%s", image.short_id, image.tags)

    created_timestamp = int(image.created_at.timestamp())

    for tag in image.tags:
        is_expired, *_, reason = check_expiration(global_state, tag, now, created_timestamp)
        if is_expired:
            yield tag, reason

    # only remove a container directly if there are no tags we could monitor
    if not image.tags:
        # todo: dangling images should be deleted
        # is_expired, *_, reason = check_expiration(
        #     global_state, image.short_id, now, created_timestamp
        # )
        # if is_expired:
        #     yield image.short_id, reason
        yield image.short_id, "no tags"


async def remove_image_ident(global_state: GlobalState, ident: str) -> None:
    report(global_state, "info", f"remove image/tag '{ident}'", None)
    await global_state.docker_client.images.delete(ident)
    # should be done outomatically
    # await update_image_registration(global_state, ident)


async def delete_container(global_state: GlobalState, ident: str | Container) -> None:
    container = (global_state.containers[ident] if isinstance(ident, str) else ident).raw_container

    report(
        global_state,
        "warning",
        f"force removing container {short_id(container.id)}",
        container,
    )
    await container.delete(force=True, v=True)

    # Container should cleanup itself

    # if container := await container_from(docker_client, container.id):
    #    report(
    #        global_state,
    #        "error",
    #        f"container {container_ident} still exists after deletion",
    #        container,
    #    )


async def cleanup(global_state: GlobalState) -> None:
    report(global_state, "info", "start cleanup", None)

    try:
        now = int(datetime.now().timestamp())
        # we could go through docker_client.containers/images/volumes, too, but to be more
        # consistent, we operate on one structure only.
        for container_info in list(
            filter(
                lambda cnt: would_cleanup_container(global_state, cnt, now),
                global_state.containers.values(),
            )
        ):
            if not global_state.switches.get("remove_container"):
                log().info("skip removal of container %s", container_info.short_id)
                continue
            try:
                await delete_container(global_state, container_info)
            except DockerError as exc:
                log().error(
                    "Could not delete container %s, error was %s", container_info.short_id, exc
                )

        # traverse all images, remove expired tags
        # use a DFT approach

        # async def handle_branch(branch):
        #    for image_id, children in list(branch.items()):
        #        if not (image_info := global_state.images.get(image_id)):
        #            continue
        #        if children:
        #            await handle_branch(children)
        #        else:
        #            for ident in expired_idents(global_state, image_info, now):
        #                if not global_state.switches.get("remove_images"):
        #                    log().info("skip removal of image/tag %s", ident)
        #                    continue
        #                try:
        #                    await remove_image_ident(global_state, ident)
        #                except DockerError as exc:
        #                    log().error("Could not delete image %s, error was %s", ident, exc)
        # await handle_branch(dep_tree)

        # FOR NOW: only handle images without children
        no_remove_images = not global_state.switches.get("remove_images")
        image: Image
        for image in list(global_state.images.values()):
            if image.children:
                continue

            for ident, reason in expired_idents(global_state, image, now):
                log().info("%sexpired: %s (%s)", "SKIP " if no_remove_images else "", ident, reason)
                if no_remove_images:
                    continue
                try:
                    await remove_image_ident(global_state, ident)
                except DockerError as exc:
                    report(global_state, "error", f"'{ident}' could not be removed: {exc}")

        log().info("Prune docker volumes")
        for volume_name in list(global_state.volumes):
            log().info("try to delete %s..", volume_name)
            try:
                await DockerVolume(global_state.docker_client, volume_name).delete()
                report(
                    global_state,
                    "warning",
                    f"volume {short_id(volume_name)} has not been removed automatically",
                )
            except DockerError as exc:
                log().info("not possible: %s", exc)

        # TODO: react on disapearing volumes

        report(global_state, "info", "invoke 'docker builder prune'")
        _stdout, _stderr, result = await global_state.docker_state.prune_builder_cache()
        if result == 0:
            report(global_state, "info", "'docker builder prune' successful")
        else:
            report(global_state, "error", "'docker builder prune' returned non-zero")

    finally:
        report(global_state, "info", "cleanup done", None)


def report(
    global_state: GlobalState,
    msg_type: None | str = None,
    message: None | str = None,
    extra: None | object = None,
) -> None:
    """Report an incident - maybe good or bad"""
    if sys.exc_info()[1] and msg_type in {None, "exception"}:
        log().exception(str(message) if message else "Exception:")
        traceback_str = traceback.format_exc().strip("\n")
        type_str, msg_str, extra_str = (
            msg_type or "exception",
            "\n".join((message, traceback_str)) if message else traceback_str,
            None,
        )
    else:
        type_str, msg_str, extra_str = (msg_type or "debug", message or "--", extra and str(extra))
        log().log(getattr(logging, type_str.upper(), logging.WARNING), msg_str)

    icon = {"info": "🔵", "warning": "🟠", "error": "🔴", "exception": "🟣"}.get(type_str, "⚪")
    now = datetime.now()

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(
        BASE_DIR / f"messages-{now.strftime('%Y.%m.%d')}.log", "a", encoding="utf-8"
    ) as log_file:
        log_file.write(
            f"{icon} {type_str:<9s}"
            f" │ {date_str(now)} ({age_str(datetime.now(), global_state.docker_state.started_at)})"
            f" │ {os.getpid()} │ {msg_str} {extra_str or  ''}\n"
        )

    global_state.messages.insert(0, (int(now.timestamp()), type_str, msg_str, extra_str))

    if isinstance(msize := global_state.additional_values.get("message_history_size", 100), int):
        global_state.messages = global_state.messages[:msize]


def reconfigure(global_state: GlobalState) -> None:
    """Reacts on changes to the configuration, e.g. applies"""

    # for ident, reference in global_state.last_referenced.items():
    #    reference[1] = expiration_age_from_ident(global_state, ident)

    # todo
    # global_state.inform("refresh")
    from . import utils as _utils  # pylint: disable=import-outside-toplevel

    importlib.reload(_utils)

    report(global_state, "info", "configuration reloaded")


async def on_button_pressed(ui: DockerShaperUI, event: Button.Pressed) -> None:
    log().debug("Button %r pressed", event.button.id)
    if event.button.id == "quit":
        ui.exit()
    elif event.button.id == "clean":
        asyncio.ensure_future(trigger_cleanup(ui))
    elif event.button.id == "rotate_log_level":
        utils.increase_loglevel()
        event.button.label = f"rotate log level ({logging.getLevelName(log().level)})"
        log().info("changed log level to %s", logging.getLevelName(log().level))
    elif event.button.id == "dump_trace":
        trace_log_file_path = BASE_DIR / "traceback.log"
        ui.write_message("dump trace")
        with trace_log_file_path.open("w", encoding="utf-8") as log_file:
            utils.dump_stacktrace(lambda msg: log_file.write(f"{msg}\n"), ui.write_message)
            ui.write_message(f"traceback also written to {trace_log_file_path}")


async def update_dashboard(ui: DockerShaperUI) -> None:
    current_process = psutil.Process()
    tasks = [
        name for t in asyncio.all_tasks() if not (name := t.get_name()).startswith("message pump ")
    ]
    now = datetime.now()
    ui.lbl_event_horizon.update(
        f"Event horizon: {date_str(ui.global_state.docker_state.event_horizon)}"
        f" / [bold cyan]{age_str(now, ui.global_state.docker_state.event_horizon)}[/]"
    )
    ui.lbl_runtime.update(
        f"runtime: {date_str(ui.global_state.docker_state.started_at)}"
        f" / [bold cyan]{age_str(now, ui.global_state.docker_state.started_at)}[/]"
    )
    ui.lbl_switches.update(
        "\n".join(
            f"{key:.<20}: [bold  cyan]{markup_escape('[x]' if value else '[ ]')}[/]"
            for key, value in ui.global_state.switches.items()
        )
    )
    ui.lbl_expiration.update(
        "\n".join(
            f"{key:.<20}: [bold cyan]{dur_str(value):>5s}[/]"
            for key, value in ui.global_state.expiration_ages.items()
        )
    )
    cleanup_interval = ui.global_state.intervals["cleanup"]
    ui.btn_clean.label = (
        f"Cleanup now! {dur_str(cleanup_interval - ui.global_state.cleanup_fuse)}"
        f" (interval={dur_str(cleanup_interval)})"
    )

    # toggles: cleanup container / images / volumes / build cache
    # │ connections:       1
    # │ tasks:             34, missing / unknown: none /  none
    # │ initially crawled: containers: True images: True volumes: True networks: True

    # Tables:
    # containers
    # images
    # rules
    # unmatched tags
    docker_version = (
        process_output("docker --version").split("\n", maxsplit=1)[0].split(sep=" ", maxsplit=2)[2]
    )
    cpu_percent = psutil.cpu_percent()
    cpu_count = psutil.cpu_count()
    ui.update_status_bar(
        f" PID: {current_process.pid}"
        f" / {current_process.cpu_percent():6.1f}% CPU"
        f" / {len(tasks)} tasks"
        f" │ System CPU: {cpu_percent:5.1f}% / {int(cpu_percent * cpu_count):4d}%"
        f" │ docker-shaper v{__version__}"
        f" │ docker v{docker_version}"
    )
    # ui.lbl_stats1.update()
    await asyncio.sleep(3)


def update_node_labels(ui: DockerShaperUI) -> None:
    """Fills some labels with useful information"""
    total_cpu = sum(map(lambda c: c.cpu_usage(), ui.global_state.docker_state.containers.values()))
    total_mem = sum(map(lambda c: c.mem_usage(), ui.global_state.docker_state.containers.values()))
    ui.patterns_node.set_label(f"Image-pattern ({len(ui.removal_patterns)})")
    ui.containers_node.set_label(
        f"Containers ({len(ui.global_state.docker_state.containers):2d})"
        f" {' ' * 56} [bold]{total_cpu * 100:7.2f}% - {total_mem >> 20:6d}MiB[/]"
    )
    ui.images_node.set_label(f"Images ({len(ui.global_state.docker_state.images)})")
    ui.volumes_node.set_label(f"Volumes ({len(ui.global_state.docker_state.volumes)})")
    ui.networks_node.set_label(f"Networks ({len(ui.global_state.docker_state.networks)})")


async def schedule_cleanup(ui: DockerShaperUI) -> None:
    while True:
        if (
            interval := ui.global_state.intervals.get("cleanup", 3600)
        ) and ui.global_state.cleanup_fuse > interval:
            ui.global_state.cleanup_fuse = 0
            break
        if (interval - ui.global_state.cleanup_fuse) % 60 == 0:
            log().debug(
                "cleanup: %s seconds to go..",
                (interval - ui.global_state.cleanup_fuse),
            )
        await asyncio.sleep(1)
        ui.global_state.cleanup_fuse += 1
    await asyncio.ensure_future(trigger_cleanup(ui))


async def trigger_cleanup(ui: DockerShaperUI) -> None:
    try:
        ui.btn_clean.disabled = True
        ui.btn_clean.variant = "error"
        ui.global_state.cleanup_fuse = 0
        await asyncio.ensure_future(cleanup(ui.global_state))
    finally:
        ui.global_state.cleanup_fuse = 0
        ui.btn_clean.disabled = False
        ui.btn_clean.variant = "default"


def load_config(global_state: GlobalState, config_file_path: Path) -> ModuleType:
    """Load the config module and invoke `reconfigure`"""
    module = utils.load_module(config_file_path)
    try:
        module.modify(global_state)
        reconfigure(global_state)
    except AttributeError:
        log().warning("File %s does not provide a `modify(global_state)` function")
    return module


async def on_changed_file(global_state: GlobalState, config_file_path: Path, changes) -> None:
    for changed_file, module in changes:
        try:
            changed_file_str = changed_file.as_posix().replace(Path.home().as_posix(), "~")
            if changed_file == config_file_path:
                log().info("config file %s changed - apply changes", changed_file_str)
                load_config(global_state, config_file_path)
            else:
                log().info("file %s changed - reload module", changed_file_str)
                assert module
                importlib.reload(module)
        except Exception:  # pylint: disable=broad-except
            report(global_state)
            await asyncio.sleep(5)


def container_markup(container: Container) -> str:
    status_markups = {"running": "cyan bold"}
    image_str, status_str = (
        ("", "")
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


async def maintain_docker_stats_tree(ui: DockerShaperUI) -> None:
    container_nodes = {}
    image_nodes = {}
    reference_nodes: dict[ImageIdent, TreeNode] = {}
    network_nodes = {}
    volume_nodes = {}

    ui.docker_stats_tree.root.expand()
    ui.docker_stats_tree.root.allow_expand = False

    # wait for all items to be registered
    while not all(
        (
            ui.global_state.docker_state.containers_crawled,
            ui.global_state.docker_state.images_crawled,
            ui.global_state.docker_state.volumes_crawled,
            ui.global_state.docker_state.networks_crawled,
        )
    ):
        log().info(
            "wait for initial crawls (C: %s, I: %s, V: %s, N: %s)",
            ui.global_state.docker_state.containers_crawled,
            ui.global_state.docker_state.images_crawled,
            ui.global_state.docker_state.volumes_crawled,
            ui.global_state.docker_state.networks_crawled,
        )
        await asyncio.sleep(1)

    # add all containers known up to now - will be maintained later
    for container in ui.global_state.docker_state.containers.values():
        container_nodes[container.id] = ui.containers_node.add(f"{container}", data=container.id)

    # add all images
    pattern_issues = []
    for img in ui.global_state.docker_state.images.values():
        img_node = image_nodes[img.id] = ui.images_node.add(f"{img}", expand=True)
        for tag in img.tags:
            dep_age, reason = expiration_age_from_image_name(ui.removal_patterns, tag, 666)
            reason_markup = "bold red"
            if reason in ui.removal_patterns:
                if reason not in ui.pattern_usage_count:
                    ui.pattern_usage_count[reason] = 0
                ui.pattern_usage_count[reason] += 1
                reason_markup = "sky_blue2"
            else:
                pattern_issues.append(f"{tag} # {reason}")
            img_node.add(
                f"dep_age=[sky_blue2]{dep_age:10d}[/]"
                f" [bold]{tag}[/] '[{reason_markup}]{reason}[/]'"
            )

    # add all volumes
    for volume in ui.global_state.docker_state.volumes.values():
        volume_nodes[volume.Name] = ui.volumes_node.add(f"{volume}")

    # add all networks
    for network in ui.global_state.docker_state.networks.values():
        network_nodes[network.Id] = ui.networks_node.add(f"{network}")

    # add all pattern
    for issue in pattern_issues:
        ui.patterns_node.add(f"[bold red]{issue}[/]'")
    for pattern, dep_age in ui.removal_patterns.items():
        usage_count = ui.pattern_usage_count.get(pattern, 0)
        if usage_count == 0:
            pattern_issues.append(pattern)
        ui.patterns_node.add(f"{usage_count:3d}: r'[sky_blue2]{pattern}[/]'")

    with (BASE_DIR / "pattern-issues.txt").open("w", encoding="utf-8") as issues_file:
        issues_file.write("\n".join(pattern_issues))

    ui.update_node_labels()

    async for mtype, mtext, mobj in ui.global_state.docker_state.wait_for_change():
        # TODO: this is an endless loop - content should be dynamic.
        handle_docker_state_message(ui.global_state, mtype, mtext, mobj)
        ui.docker_stats_tree.root.set_label(
            f"{utils.get_hostname()}"
            f" / horizon={date_str(ui.global_state.docker_state.event_horizon)}"
            f" ({dur_str(int(time.time()) - ui.global_state.docker_state.event_horizon)})"
        )

        if mtype in {"container_add", "container_del", "container_update"}:
            cnt: Container = cast(Container, mobj)
            if mtype == "container_add" and cnt.id not in container_nodes:
                container_nodes[cnt.id] = ui.containers_node.add(f"{cnt}", data=cnt.id)
            if mtype == "container_update":
                if cnt.id not in container_nodes:
                    # todo: investigate - node should be available already
                    log().warning("container %s not known to UI yet but should", cnt.id)
                    container_nodes[cnt.id] = ui.containers_node.add(f"{cnt}", data=cnt.id)
                container_nodes[cnt.id].set_label(container_markup(cnt))
            if mtype == "container_del" and cnt.id in container_nodes:
                if cnt.id in container_nodes:
                    container_nodes[cnt.id].remove()
                    del container_nodes[cnt.id]

            ui.update_node_labels()

        elif mtype in {"image_add", "image_del", "image_update"}:
            image_id = mtext

            if mtype == "image_del":
                if image_id in image_nodes:
                    image_nodes[image_id].remove()
                    del image_nodes[image_id]
                continue
            image = ui.global_state.docker_state.images[image_id]
            if mtype == "image_add" and image.id not in image_nodes:
                image_nodes[image.id] = ui.images_node.add(f"{image}")
            if mtype == "image_update":
                if image.id not in image_nodes:
                    # todo: investigate - node should be available already
                    log().warning("image %s not known to UI yet but should", image.id)
                    image_nodes[image.id] = ui.images_node.add(f"{image}")
                image_nodes[image.id].set_label(f"{image} - +")

            ui.update_node_labels()

        elif mtype in {"volume_add", "volume_del"}:
            volume_id = mtext
            if mtype == "volume_add" and volume_id not in volume_nodes:
                vol: Volume = cast(Volume, mobj)
                volume_nodes[volume_id] = ui.volumes_node.add(f"{vol}")
            if mtype == "volume_del":
                if volume_id in volume_nodes:
                    volume_nodes[volume_id].remove()
                    del volume_nodes[volume_id]

            ui.update_node_labels()

        elif mtype in {"network_add", "network_del"}:
            network_id = mtext
            if mtype == "network_add" and network_id not in network_nodes:
                netw: Network = cast(Network, mobj)
                network_nodes[network_id] = ui.networks_node.add(f"{netw}")
            if mtype == "network_del":
                if network_id in network_nodes:
                    network_nodes[network_id].remove()
                    del network_nodes[network_id]
            ui.update_node_labels()

        elif mtype in {"reference_update", "reference_del"}:
            ident = cast(ImageIdent, mobj)
            if mtype == "reference_update":
                label_str = (
                    f"{ident} - " f"{date_str(ui.global_state.docker_state.last_referenced[ident])}"
                )
                if ident in reference_nodes:
                    reference_nodes[ident].set_label(label_str)
                else:
                    reference_nodes[ident] = ui.references_node.add(label_str, allow_expand=False)
            if mtype == "reference_del" and ident in reference_nodes:
                reference_nodes[ident].remove()
                del reference_nodes[ident]

        elif mtype in {"exception", "error", "warning", "info"}:
            pass
        else:
            log().error("don't know message type %s", mtype)
