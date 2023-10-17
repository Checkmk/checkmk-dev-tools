#!/usr/bin/env python3

"""Functionality that might change during runtime
"""

# pylint: disable=invalid-name  # names come from aiodocker, not my fault
# pylint: disable=too-many-instance-attributes
# pylint: disable=too-many-lines
# pylint: disable=too-few-public-methods
# pylint: disable=too-many-branches
# pylint: disable=too-many-return-statements
# pylint: disable=missing-function-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=fixme

import asyncio
import json
import logging
import os
import re
import sys
import time
import traceback
from collections import Counter
from collections.abc import Iterable, MutableMapping, MutableSequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from aiodocker import Docker, DockerError
from aiodocker.volumes import DockerVolume
from dateutil import tz
from quart import redirect, render_template, request, url_for, websocket

from docker_shaper.docker_state import (
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
from docker_shaper.flask_table_patched import Col, Table
from docker_shaper.utils import (
    age_str,
    date_str,
    dur_str,
    impatient,
    setup_introspection_on_signal,
)

BASE_DIR = Path("~/.docker_shaper").expanduser()


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper")


def read_hostname() -> str:
    """Returns local hostname read from /etc/hostname"""
    with open("/etc/hostname", encoding="utf-8") as hostname_file:
        return hostname_file.read().strip()


@dataclass
class GlobalState:
    """The dirty globally shared state of docker-shaper"""

    intervals: MutableMapping[str, float]
    tag_rules: MutableMapping[str, int]
    extra_links: MutableMapping[str, int]
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
            "state": 2,
            "image_stats": 2,
            "container_stats": 2,
            "cleanup": 3600,
        }
        self.cleanup_fuse = 0

        self.tag_rules = {}
        self.counter = 0
        self.extra_links = {}
        self.switches = {}
        self.messages = []
        self.hostname = read_hostname()
        self.expiration_ages = {}
        self.update_mqueues = set()
        self.additional_values = {}

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

    async def inform(self, message: str) -> None:
        """Send a message to all connected message queues"""
        for mqueue in self.update_mqueues:
            await mqueue.put(message)

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


async def run_listen_messages(global_state: GlobalState) -> None:
    """Print messages"""
    (BASE_DIR / "container-logs").mkdir(parents=True, exist_ok=True)
    async for mtype, mtext, mobj in global_state.docker_state.wait_for_change():
        handle_docker_state_message(global_state, mtype, mtext, mobj)


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
        elif mtype == "error":
            report(global_state, mtype, mtext, mobj)
        elif mtype in {"container_add", "container_del", "container_update"}:
            log().info(
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
                    write_log_entry(log_file, cnt.show.model_dump(mode="json"))
                if mtype == "container_del":
                    #write_log_entry(log_file, cnt.show.model_dump(mode="json"))
                    pass
                if mtype == "container_update":
                    assert cnt.stats
                    write_log_entry(
                        log_file,
                        {
                            "time": int(time.time()-cnt.started_at.timestamp()),
                            "cpu-usage": int(cnt.cpu_usage() * 100) / 100,
                            "mem-usage": cnt.stats.memory_stats.usage,
                        },
                    )

        elif mtype in {"image_add", "image_del", "image_update"}:
            log().info(
                "image info: %s / %s (%d total)",
                short_id(mtext),
                mtype,
                len(global_state.docker_state.images),
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

    matching_rules = tuple(
        (regex, age) for regex, age in global_state.tag_rules.items() if re.match(regex, ident)
    )

    if len(matching_rules) == 1:
        return matching_rules[0][1], matching_rules[0][0]

    if not matching_rules:
        log().warning("No rule found for %r", ident)
        return global_state.expiration_ages["tag_unknown"], "no rule=>tag_unknown"

    log().error("Multiple rules found for %s:", ident)
    for rule in matching_rules:
        log().error("  %s:", rule[0])

    return (
        global_state.expiration_ages["tag_unknown"],
        f"multiple_rules=>tag_unknown ({[rule[0] for  rule in  matching_rules]})",
    )


@impatient
def check_expiration(
    global_state: GlobalState, ident: str, now: int, extra_date: int = 0
) -> tuple[bool, None | int, int, str]:
    # assert ident == unique_ident(ident)
    uident = unique_ident(ident)

    if uident not in global_state.last_referenced:
        log().debug("%s: no reference yet", ident)

    last_referenced = global_state.last_referenced.get(uident)
    expiration_age, reason = expiration_age_from_ident(global_state, ident)
    # TODO
    # last_referenced, expiration_age = global_state.last_referenced.setdefault(
    #    ident, [None, expiration_age_from_ident(global_state, ident)]
    # )

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


async def dump_global_state(global_state: GlobalState):
    # TODO
    # dep_tree, max_depth = image_dependencies(global_state)
    # def handle_branch(branch, depth=0):
    #     for image_id, children in list(branch.items()):
    #         print(
    #             f"{'.' * (depth + 1)}"
    #             f" {short_id(image_id)}"
    #             f" {'.' * (max_depth - depth + 1)}"
    #             f" {len(children)}"
    #             f" {global_state.images[image_id].tags}"
    #         )
    #         handle_branch(children, depth + 1)
    # handle_branch(dep_tree)

    global_state.counter += 1
    coro_name_count = Counter(
        name
        for t in asyncio.all_tasks()
        if (coro := t.get_coro())
        if (name := getattr(coro, "__name__"))
        # ignore
        not in {
            "handle_lifespan",
            "handle_websocket",
            "handle_messages",
            "get",
            "wait",
            "raise_shutdown",
            "_connect_pipes",
            "_server_callback",
            "_handle",
            "_run_idle",
            # starts/stops randomly
            "watch_container",
            "fuse_fn",
            "add_next",
            "sleep",
            "iterate",
        }
    )

    expectd_coro_names = {
        "run",
        "serve",
        "dump_global_state",
        "watch_fs_changes",
        "schedule_cleanup",
        "schedule_print_state",
        "run_crawl_images",
        "run_crawl_containers",
        "run_crawl_networks",
        "run_listen_messages",
        "run_crawl_volumes",
        "monitor_events",
    }

    print()
    print(f"STATE: ====[ {global_state.hostname } ]===============================================")
    print(f"STATE: frame counter:     {global_state.counter}")
    print(f"STATE: event horizon:     {global_state.docker_state.event_horizon}")
    print(
        f"STATE: intervals:         "
        f"{', '.join('='.join(map(str, i)) for i in global_state.intervals.items())}"
    )
    print(f"STATE: containers:        {len(global_state.containers)}")
    print(f"STATE: images:            {len(global_state.images)}")
    # print(f"STATE: image tree depth: {max_depth}")
    print(f"STATE: volumes:           {len(global_state.volumes)}")
    print(f"STATE: networks:          {len(global_state.networks)}")
    print(f"STATE: references:        {len(global_state.last_referenced)}")
    print(f"STATE: tag_rules:         {len(global_state.tag_rules)}")
    print(f"STATE: connections:       {len(global_state.update_mqueues)}")
    print(
        f"STATE: missing / unknown tasks:"
        f" {(expectd_coro_names - coro_name_count.keys()) or 'none'} / "
        f" {(coro_name_count.keys() - expectd_coro_names) or 'none'}"
    )
    print(
        f"STATE: initially crawled:"
        f" containers: {global_state.docker_state.containers_crawled}"
        f" images: {global_state.docker_state.images_crawled}"
        f" volumes: {global_state.docker_state.volumes_crawled}"
        f" networks: {global_state.docker_state.networks_crawled}"
    )
    print("STATE: ==================================================================")
    print()


class BaseTable(Table):
    allow_sort = True
    classes = ["table", "table-striped"]

    def __init__(self, endpoint, items):
        super().__init__(items)
        self.endpoint = endpoint

    def get_tr_attrs(self, item):
        return {"class": item.get("class")}

    def sort_url(self, col_id, reverse=False):
        raise NotImplementedError()


class PlainCol(Col):
    def td_format(self, content):
        return f"<tt><b>{content}</b></tt>"


class ImageTable(BaseTable):
    short_id = PlainCol("short_id")
    tags = PlainCol("tags")
    created_at = PlainCol("created_at")
    age = PlainCol("age")

    def sort_url(self, col_id, reverse=False):
        return url_for(
            self.endpoint,
            sort_key_images=col_id,
            sort_direction_images="desc" if reverse else "asc",
        )

    @staticmethod
    def html_from(endpoint, global_state, sort, reverse):
        now = datetime.now(tz=tz.tzutc())

        def dict_from(image: Image):
            now_timestamp = now.timestamp()
            created_timestamp = image.created_at.timestamp()

            def coloured_ident(ident: str) -> str:
                is_expired, last_referenced, expiration_age, _reason = check_expiration(
                    global_state, ident, now_timestamp, created_timestamp
                )
                return (
                    f"<div class='text-{'danger' if is_expired else 'success'}'>"
                    f"{ident} ({age_str(now, last_referenced)}/{age_str(expiration_age, 0)}) "
                    f"<a href=remove_image_ident?ident={ident}>del</a>"
                    f"</div>"
                )

            return {
                "short_id": coloured_ident(image.short_id),
                "tags": "".join(map(coloured_ident, image.tags)),
                "created_at": date_str(image.created_at),
                "age": age_str(now, image.created_at, fixed=True),
                # "last_referenced": last_referenced_str(image["short_id"]),
                # "class": ("text-danger" if
                # would_cleanup_image(image, now, global_state) else "text-success"),
            }

        return ImageTable(
            endpoint,
            items=sorted(
                map(dict_from, global_state.images.values()),
                key=lambda e: e[sort],
                reverse=reverse,
            ),
        ).__html__()


class ContainerTable(BaseTable):
    short_id = PlainCol("short_id")
    name = PlainCol("name")
    image = PlainCol("image")

    status = PlainCol("status")
    created_at = PlainCol("created_at")
    started_at = PlainCol("started_at")
    uptime = PlainCol("uptime/age")
    pid = PlainCol("pid")
    mem_usage = PlainCol("mem_usage")
    cpu = PlainCol("cpu")
    cmd = PlainCol("cmd")

    job = PlainCol("job")
    hints = PlainCol("hints")
    # link = LinkCol('Link', 'route_containers', url_kwargs=dict(id='id'), allow_sort=False)

    def sort_url(self, col_id, reverse=False):
        return url_for(
            self.endpoint,
            sort_key_containers=col_id,
            sort_direction_containers="desc" if reverse else "asc",
        )

    @staticmethod
    def html_from(endpoint, global_state, sort, reverse):
        now = datetime.now(tz=tz.tzutc())

        def coloured_ident(cnt):
            cnt_expired = would_cleanup_container(global_state, cnt, now.timestamp())
            return (
                f"<div class='text-{'danger' if cnt_expired else 'success'}'>"
                f"{cnt.short_id} "
                f"<a href=delete_container?ident={cnt.short_id}>del</a>"
                f"</div>"
            )

        return ContainerTable(
            endpoint,
            items=sorted(
                (
                    {
                        "short_id": coloured_ident(cnt),
                        "name": cnt.name,
                        "image": short_id(cnt.image) if is_uid(cnt.image) else cnt.image,
                        "mem_usage": f"{((cnt.stats.memory_stats.usage or 0)>>20)}MiB",
                        "cpu": f"{int(cnt.cpu_usage() * 1000) / 10}%",
                        "cmd": "--" if not (cmd := cnt.cmd) else " ".join(cmd)[:100],
                        "job": jobname_from(
                            cnt.host_config["Binds"] or list(cnt.show.Config.Volumes or [])
                        ),
                        "created_at": date_str(cnt.created_at),
                        "started_at": date_str(cnt.started_at),
                        "uptime": age_str(
                            now,
                            cnt.started_at if cnt.started_at.year > 1000 else cnt.created_at,
                            fixed=True,
                        ),
                        "status": cnt.status,
                        "hints": label_filter(cnt.labels),
                        "pid": cnt.pid,
                        # https://getbootstrap.com/docs/4.0/utilities/colors/
                    }
                    for cnt in global_state.containers.values()
                    if cnt.stats and cnt.last_stats
                ),
                key=lambda e: e[sort],
                reverse=reverse,
            ),
        ).__html__()


class VolumeTable(BaseTable):
    name = PlainCol("name")
    labels = PlainCol("labels")
    created_at = PlainCol("created_at")
    age = PlainCol("age")
    mountpoint = PlainCol("mountpoint")

    def sort_url(self, col_id, reverse=False):
        return url_for(
            self.endpoint,
            sort_key_volumes=col_id,
            sort_direction_volumes="desc" if reverse else "asc",
        )

    @staticmethod
    def html_from(endpoint, global_state, sort, reverse):
        now = datetime.now(tz=tz.tzutc())

        def dict_from(volume: Volume):
            now_timestamp = now.timestamp()
            created_timestamp = volume.CreatedAt.timestamp()

            def coloured_ident(ident: str, formatter=lambda s: s) -> str:
                is_expired, last_referenced, expiration_age, _reason = check_expiration(
                    global_state, ident, now_timestamp, created_timestamp
                )
                return (
                    f"<div class='text-{'danger' if is_expired else 'success'}'>"
                    f"{formatter(ident)}"
                    f" ({age_str(now, last_referenced)}/{age_str(expiration_age, 0)})"
                    f"<a href=delete_volume?ident={ident}>del</a></div>"
                )

            return {
                "name": coloured_ident(volume.Name, formatter=lambda s: s[:12]),
                "labels": "".join(map(coloured_ident, volume.Labels or [])),
                "created_at": date_str(volume.CreatedAt),
                "age": age_str(now, volume.CreatedAt),
                "mountpoint": volume.Mountpoint,
            }

        return VolumeTable(
            endpoint,
            items=sorted(
                map(dict_from, global_state.volumes.values()),
                key=lambda e: e[sort],
                reverse=reverse,
            ),
        ).__html__()


def meta_info(global_state: GlobalState):
    return {
        "refresh_interval": global_state.intervals.get("site_refresh", 10),
        "event_horizon": age_str(time.time(), global_state.docker_state.event_horizon),
        "container_count": len(global_state.containers),
        "image_count": len(global_state.images),
        "volume_count": len(global_state.volumes),
        "extra_links": global_state.extra_links,
        "intervals": {key: dur_str(value) for key, value in global_state.intervals.items()},
        "next_cleanup": dur_str(global_state.intervals["cleanup"] - global_state.cleanup_fuse),
        "hostname": global_state.hostname,
        "switches": global_state.switches,
        "expiration_ages": {
            key: dur_str(value) for key, value in global_state.expiration_ages.items()
        },
        "self_pid": os.getpid(),
    }


async def response_remove_image_ident(global_state: GlobalState):
    try:
        if not (ident := request.args.get("ident")):
            raise RuntimeError("STRAGE: response_remove_image_ident called with empty `ident`")
        await remove_image_ident(global_state, ident)
    except Exception as exc:  # pylint: disable=broad-except
        log().exception("Exception raised in remove_image_ident(%s)", request.args.get("ident"))
        return f"Exception raised in remove_image_ident({request.args.get('ident')}): {exc}"
    return redirect(request.referrer or url_for("route_dashboard"))


async def response_delete_container(global_state: GlobalState):
    try:
        await delete_container(global_state, request.args.get("ident", ""))
    except Exception as exc:  # pylint: disable=broad-except
        log().exception("Exception raised in delete_container(%s)", request.args.get("ident"))
        return f"Exception raised in delete_container({request.args.get('ident')}): {exc}"
    return redirect(request.referrer or url_for("route_dashboard"))


async def response_cleanup(global_state: GlobalState):
    global_state.cleanup_fuse = int(global_state.intervals["cleanup"])
    return redirect(request.referrer or url_for("route_dashboard"))


async def response_rules(_global_state: GlobalState) -> str:
    return "no rules yet"


async def response_messages(_global_state: GlobalState) -> str:
    return "no messages yet"


async def response_volumes(global_state: GlobalState):
    return await render_template(
        "volumes.html",
        meta=meta_info(global_state),
        volumes_html=VolumeTable.html_from(
            "route_volumes",
            global_state,
            sort=request.args.get("sort_key_volumes", "created_at"),
            reverse=request.args.get("sort_direction_volumes", "desc") == "desc",
        ),
    )


async def response_containers(global_state: GlobalState):
    # https://github.com/plumdog/flask_table/blob/master/examples/sortable.py
    return await render_template(
        "containers.html",
        meta=meta_info(global_state),
        containers_html=ContainerTable.html_from(
            "route_containers",
            global_state,
            sort=request.args.get("sort_key_containers", "cpu"),
            reverse=request.args.get("sort_direction_containers", "desc") == "desc",
        ),
    )


async def response_images(global_state: GlobalState):
    # https://github.com/plumdog/flask_table/blob/master/examples/sortable.py
    return await render_template(
        "images.html",
        meta=meta_info(global_state),
        images_html=ImageTable.html_from(
            "route_images",
            global_state,
            sort=request.args.get("sort_key_images", "created_at"),
            reverse=request.args.get("sort_direction_images", "asc") == "desc",
        ),
    )


async def response_dashboard(global_state: GlobalState):
    return await render_template(
        "dashboard.html",
        meta=meta_info(global_state),
        containers_html=ContainerTable.html_from(
            "route_dashboard",
            global_state,
            sort=request.args.get("sort_key_containers", "cpu"),
            reverse=request.args.get("sort_direction_containers", "desc") == "desc",
        ),
        images_html=ImageTable.html_from(
            "route_dashboard",
            global_state,
            sort=request.args.get("sort_key_images", "created_at"),
            reverse=request.args.get("sort_direction_images", "asc") == "desc",
        ),
        messages=[
            (date_str(m[0]), m[1], m[2].replace("\n", "<br>|")) for m in global_state.messages
        ],
    )


async def container_table_html(global_state: GlobalState):
    return await response_containers(global_state)


async def image_table_html(global_state: GlobalState):
    return await response_images(global_state)


async def dashboard(global_state: GlobalState):
    return await response_dashboard(global_state)


async def print_container_stats(global_state: GlobalState) -> None:
    stats = [
        {
            "short_id": cnt.short_id,
            "name": cnt.name,
            "usage": cnt.stats.memory_stats.usage,
            "cmd": " ".join(cnt.cmd or []),
            "job": (
                jobname_from(
                    cnt.host_config["Binds"] or list((cnt.show and cnt.show.Config.Volumes) or [])
                )
            ),
            "cpu": cnt.cpu_usage(),
            "created_at": cnt.created_at,
            "started_at": cnt.started_at,
            "status": cnt.status,
            "hints": label_filter(cnt.labels),
            "pid": cnt.pid,
            "container": cnt.raw_container,
        }
        for cnt in global_state.containers.values()
        if cnt.stats and cnt.last_stats
    ]

    os.system("clear")
    print(f"=[ {global_state.hostname} ]======================================")
    print(
        f"{'ID':<12}  {'NAME':<25}"
        f" {'PID':>9}"
        f" {'CPU':>9}"
        f" {'MEM':>9}"
        f" {'UP':>9}"
        f" {'STATE':>9}"
        f" {'JOB':<60}"
        f" {'HINTS'}"
    )
    now = datetime.now()
    for s in sorted(stats, key=lambda e: e["pid"]):
        tds = int((now - (s["started_at"] or s["created_at"])).total_seconds())
        col_td = "\033[1m\033[91m" if tds // 3600 > 5 else ""
        duration_str = f"{tds//86400:2d}d+{tds//3600%24:02d}:{tds//60%60:02d}"
        col_mem = "\033[1m\033[91m" if s["usage"] >> 30 > 2 else ""
        mem_str = f"{(s['usage']>>20)}MiB"
        col_cpu = "\033[1m\033[91m" if s["cpu"] > 2 else ""
        # container_is_critical = (
        #     (s["started_at"] and tds // 3600 > 5) or s["status"]== "exited" or not s["started_at"]
        # )
        col_cpu = "\033[1m\033[91m" if s["cpu"] > 2 else ""
        print(
            f"{s['short_id']:<12}  {s['name']:<25}"
            f" {s['pid']:>9}"
            f" {col_cpu}{int(s['cpu'] * 100):>8}%\033[0m"
            f" {col_mem}{mem_str:>9}\033[0m"
            f" {col_td}{duration_str}\033[0m"
            f" {s['status']:>9}"
            f" {s['job']:<60}"
            f" {s['hints']}"
        )
        # if (
        #    (s["started_at"] and tds // 3600 > 5)
        #    or s["status"] == "exited"
        #    or not s["started_at"]
        # ):
        #    log(f"remove {s['short_id']}")
        #    await s["container"].delete(force=True)
    print(
        f"{'TOTAL':<12}  {len(stats):<25}"
        f" {'':>9}"
        f" {int(sum(s['cpu'] for s in stats)*1000) / 10:>8}%\033[0m"
        f" {int(sum(s['usage'] for s in stats) / (1<<30)*10) / 10:>6}GiB\033[0m"
        f" {''}"
        f" {'':>9}"
        f" {'':<60}"
        f" {''}"
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
        is_expired, *_, reason = check_expiration(
            global_state, image.short_id, now, created_timestamp
        )
        if is_expired:
            yield image.short_id, reason


async def remove_image_ident(global_state: GlobalState, ident: str) -> None:
    report(global_state, "info", f"remove image/tag '{ident}'", None)
    await global_state.docker_client.images.delete(ident)
    # should be done outomatically
    # await update_image_registration(global_state, ident)


async def delete_container(global_state: GlobalState, ident: str | Container) -> None:
    container = (global_state.containers[ident] if isinstance(ident, str) else ident).raw_container

    report(
        global_state,
        "warn",
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
    log().info("Cleanup!..")

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
        for image in (img for img in list(global_state.images.values()) if not img.children):
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
                    "warn",
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
    if msg_type is None and sys.exc_info()[1]:
        log().exception(message)
        traceback_str = traceback.format_exc().strip("\n")
        type_str, msg_str, extra_str = (
            msg_type or "exception",
            "\n".join((message, traceback_str)) if message else traceback_str,
            None,
        )
    else:
        type_str, msg_str, extra_str = (msg_type or "debug", message or "--", extra and str(extra))
    icon = {"info": "🔵", "warn": "🟠", "error": "🔴", "exception": "🟣"}.get(type_str, "⚪")
    now = datetime.now()

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(
        BASE_DIR / f"messages-{now.strftime('%Y.%m.%d')}.log", "a", encoding="utf-8"
    ) as log_file:
        log_file.write(f"{icon} {date_str(now)} │ {type_str:<10s} │ {msg_str} {extra_str or  ''}\n")

    global_state.messages.insert(0, (int(now.timestamp()), type_str, msg_str, extra_str))

    if isinstance(msize := global_state.additional_values.get("message_history_size", 100), int):
        global_state.messages = global_state.messages[:msize]


def reconfigure(global_state: GlobalState) -> None:
    """Reacts on changes to the configuration, e.g. applies"""

    # for ident, reference in global_state.last_referenced.items():
    #    reference[1] = expiration_age_from_ident(global_state, ident)

    # todo
    # global_state.inform("refresh")
    import importlib  # pylint: disable=import-outside-toplevel

    from docker_shaper import utils  # pylint: disable=import-outside-toplevel

    importlib.reload(utils)
    # utils.setup_logging()

    report(global_state, "info", "configuration reloaded")


def setup_introspection():
    setup_introspection_on_signal()


async def response_control_ws(global_state) -> None:
    """Provides a way to talk with a connected client"""
    # Only allow clients we know
    # user_id = websocket.cookies.get("session")
    # log().debug(f"/control({user_id})")
    # if not user_id:
    # websocket.close()

    mqueue = global_state.new_update_queue()
    await websocket.accept()

    try:
        while True:
            message = await mqueue.get()
            await websocket.send(message)
            reply = await websocket.receive()
            log().debug("reply to '%s': '%s'", message, reply)
    except asyncio.CancelledError:  # pylint: disable=try-except-raise
        raise
    except Exception:  # pylint: disable=broad-except
        log().exception("Unhandled exception in control")
    finally:
        global_state.remove_queue(mqueue)
        log().info("Connection closed")
