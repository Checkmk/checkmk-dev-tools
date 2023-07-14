#!/usr/bin/env python3

"""Functionality that might change during runtime
"""
import asyncio
import logging
import os
import re
import time
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from subprocess import CalledProcessError
from typing import MutableMapping, MutableSequence, Optional, Tuple

from aiodocker import Docker, DockerError
from dateutil import tz
from flask_table import Col, Table
from quart import redirect, render_template, request, url_for

from docker_shaper.utils import (
    age_str,
    date_from,
    date_str,
    dur_str,
    impatient,
    process_output,
    setup_introspection,
)


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper.dynamic")


@dataclass
class GlobalState:
    """The dirty globally shared state of docker-shaper"""

    intervals: MutableMapping[str, float]
    image_ids: MutableMapping[str, object]
    images: MutableMapping[str, object]
    containers: MutableMapping[str, object]
    volumes: MutableMapping[str, object]
    event_horizon: int
    last_referenced: MutableMapping[str, MutableSequence[int]]
    tag_rules: MutableMapping[str, int]
    extra_links: MutableMapping[str, int]
    messages: MutableSequence[Tuple[int, str, str]]
    switches: MutableMapping[str, bool]
    hostname: str
    expiration_ages: MutableMapping[str, int]

    def __init__(self):
        self.intervals = {
            "state": 2,
            "image_stats": 2,
            "image_update": 2,
            "container_update": 2,
            "container_stats": 2,
            "cleanup": 3600,
        }
        self.cleanup_fuse = 0
        self.image_ids = {}
        self.images = {}
        self.containers = {}
        self.volumes = {}

        self.event_horizon = int(time.time())
        self.last_referenced = {}
        self.tag_rules = {}
        self.counter = 0
        self.extra_links = {}
        self.switches = {}
        self.messages = []
        self.hostname = open("/etc/hostname").read().strip()
        self.expiration_ages = {}


def short_id(docker_id: str) -> str:
    """Return the 10-digit variant of a long docker ID
    >>> short_id("sha256:abcdefghijklmnop")
    'abcdefghij'
    """
    if not docker_id:
        return docker_id
    assert is_uid(docker_id)
    return docker_id[7:17] if docker_id.startswith("sha256:") else docker_id[:10]


@impatient
def id_from(name: str) -> Optional[str]:
    """Looks up name using `docker inspect` and returns a 10 digit Docker ID"""
    with suppress(CalledProcessError):
        log().debug("resolve %s", name)
        return short_id(
            name
            if name.startswith("sha256:")
            else process_output(f"docker inspect --format='{{{{.Id}}}}' {name}")
        )
    return None


def lookup_id(ids: MutableMapping[str, Optional[str]], name: str) -> Optional[str]:
    """Looks up a given @name in @ids and resolves it first if not yet given"""
    if name not in ids:
        ids[name] = id_from(name)
    return ids[name]


def event_from(line: str):
    """Reads a line from event log and turns it into a tuple containing the data"""
    match = re.match(r"^(.*) \((.*)\)$", line)
    assert match, f"line did not match the expected format: {line!r}"
    cmd, params = match.groups()
    timestamp, object_type, operator, *cmd, uid = cmd.split(" ")
    assert len(timestamp) == 35
    assert (operator in {"exec_create:", "exec_start:", "health_status:"}) == bool(
        cmd
    ), f"{operator=} {cmd=} {line=}"
    assert object_type in {
        "container",
        "network",
        "image",
        "volume",
        "builder",
    }, f"{object_type}"
    assert operator in {
        "create",
        "destroy",
        "attach",
        "connect",
        "disconnect",
        "start",
        "die",
        "pull",
        "push",
        "tag",
        "save",
        "delete",
        "untag",
        "prune",
        "commit",
        "unpause",
        "resize",
        "exec_die",
        "exec_create:",
        "exec_start:",
        "health_status:",
        "mount",
        "unmount",
        "archive-path",
        "rename",
        "kill",
        "stop",
        "top",
        "pause",
    }, f"{operator}"
    assert len(uid) == 64 or (object_type, operator) in {
        ("image", "pull"),
        ("image", "push"),
        ("image", "tag"),
        ("image", "untag"),
        ("image", "save"),
        ("image", "delete"),
        ("image", "prune"),
        ("volume", "prune"),
        ("volume", "create"),
        ("container", "prune"),
        ("network", "prune"),
        ("builder", "prune"),
    }, f"{len(uid)=} {(object_type, operator)}"
    return (
        int(
            datetime.strptime(
                f"{timestamp[:26]}{timestamp[-6:]}", "%Y-%m-%dT%H:%M:%S.%f%z"
            ).timestamp()
        ),
        object_type,
        operator,
        cmd,
        uid,
        dict(p.split("=") for p in params.split(", ")),
    )


async def handle_docker_event_line(global_state: GlobalState, line: str):
    """Read a `docker events` line and maintain the last-used information"""

    tstamp, object_type, operator, _cmd, uid, params = event_from(line)

    if (object_type, operator) in {
        ("image", "tag"),
        ("image", "pull"),
        ("container", "create"),
    }:
        ident = params.get("image") or params["name"]
        log().info(
            "docker event %s %s %s ident=%s _uid=%s",
            datetime.fromtimestamp(tstamp),
            object_type,
            operator,
            ident,
            uid,
        )
    elif object_type in {"network", "builder"}:
        return
    elif (object_type, operator) in {
        ("image", "untag"),
        ("image", "prune"),
        ("image", "push"),
        ("image", "delete"),
        ("container", "exec_create:"),
        ("container", "exec_start:"),
        ("container", "exec_die"),
        ("container", "kill"),
        ("container", "start"),
        ("container", "attach"),
        ("container", "die"),
        ("container", "top"),
        ("container", "destroy"),
        ("container", "prune"),
        ("container", "stop"),
        ("container", "resize"),
        ("container", "archive-path"),
        ("network", "connect"),
        ("network", "disconnect"),
        ("volume", "create"),
        ("volume", "mount"),
        ("volume", "unmount"),
        ("volume", "destroy"),
    }:
        return
    else:
        log().warning("unknown type/operator %s %s", object_type, operator)
        return

    global_state.event_horizon = min(global_state.event_horizon, tstamp)
    register_reference(ident, tstamp, global_state)


def is_uid(ident: str) -> bool:
    """
    sha256:48a3535fe27fea1ac6c2f41547770d081552c54b2391c2dda99e2ad87561a4f2
    48a3535fe27fea1ac6c2f41547770d081552c54b2391c2dda99e2ad87561a4f2
    48a3535fe27f
    """
    return (
        ident.startswith("sha256:")
        or re.match("[0-9a-f]{64}", ident)
        or re.match("[0-9a-f]{10}", ident)
    )


def unique_ident(ident: str) -> str:
    return short_id(ident) if is_uid(ident) else ident


def register_reference(ident: str, timestamp: int, global_state) -> None:
    effective_ident = unique_ident(ident)
    if effective_ident not in global_state.last_referenced:
        global_state.last_referenced[effective_ident] = [
            0,
            expiration_age_from_ident(effective_ident, global_state),
        ]

    # increase last reference date if applicable
    global_state.last_referenced[effective_ident][0] = max(
        global_state.last_referenced[effective_ident][0] or 0, timestamp
    )


def expiration_age_from_ident(ident: str, global_state: GlobalState) -> int:
    # TODO: distinguish between container, image and volume
    if is_uid(ident):
        return global_state.expiration_ages["tag_default"]

    effective_ident = unique_ident(ident)

    matching_rules = tuple(
        (regex, age)
        for regex, age in global_state.tag_rules.items()
        if re.match(regex, effective_ident)
    )

    if len(matching_rules) == 1:
        return matching_rules[0][1]
    if not matching_rules:
        log().warn("No rule found for %r", ident)
    else:
        log().error("Multiple rules found for %s:", ident)
        for rule in matching_rules:
            log().error("  %s:", rule[0])

    return global_state.expiration_ages["tag_unknown"]


@impatient
def expired(ident: str, global_state, now: int, extra_date: int = 0) -> bool:
    if ident not in global_state.last_referenced:
        log().warn("no reference: %s", ident)

    # TODO
    last_referenced, expiration_age = global_state.last_referenced.setdefault(
        ident, [None, expiration_age_from_ident(ident, global_state)]
    )

    effective_age = now - max(
        last_referenced or 0,
        global_state.event_horizon,
        extra_date,
    )
    return effective_age > expiration_age, last_referenced, expiration_age


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


def cpu_perc(cpu_stats, last_cpu_stats):
    if not (
        cpu_stats
        and "system_cpu_usage" in cpu_stats
        and last_cpu_stats
        and "system_cpu_usage" in last_cpu_stats
    ):
        return 0
    return (
        (cpu_stats["cpu_usage"]["total_usage"] - last_cpu_stats["cpu_usage"]["total_usage"])
        / (cpu_stats["system_cpu_usage"] - last_cpu_stats["system_cpu_usage"])
        * cpu_stats["online_cpus"]
    )


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


async def dump_global_state(global_state):
    global_state.counter += 1
    print(f"STATE: {', '.join('='.join(map(str, i)) for i in global_state.intervals.items())}")
    print(f"STATE: frame counter: {global_state.counter}")
    print(f"STATE: images: {len(global_state.images)}")
    print(f"STATE: containers: {len(global_state.containers)}")
    print(f"STATE: tag_rules: {len(global_state.tag_rules)}")
    print(
        f"STATE: tasks: {', '.join('/'.join(map(str, i)) for i in Counter(t.get_coro().__name__ for t in asyncio.all_tasks()).items())}"
    )


class BaseTable(Table):
    allow_sort = True
    classes = ["table", "table-striped"]

    def __init__(self, endpoint, items):
        super().__init__(items)
        self.endpoint = endpoint

    def get_tr_attrs(self, item):
        return {"class": item.get("class")}


class PlainCol(Col):
    def td_format(self, content):
        return f"<tt><b>{content}</b></tt>"


class ImageTable(BaseTable):
    short_id = PlainCol("short_id")
    tags = PlainCol("tags")
    created_at = PlainCol("created_at")
    age = PlainCol("age")

    def sort_url(self, col_key, reverse=False):
        return url_for(
            self.endpoint,
            sort_key_images=col_key,
            sort_direction_images="desc" if reverse else "asc",
        )

    @staticmethod
    def html_from(endpoint, global_state, sort, reverse):
        now = datetime.now(tz=tz.tzutc())

        def dict_from(image):
            now_timestamp = now.timestamp()
            created_timestamp = date_from(image["created_at"]).timestamp()

            def coloured_ident(ident):
                is_expired, last_referenced, expiration_age = expired(
                    ident, global_state, now_timestamp, created_timestamp
                )
                return (
                    f"<div class='text-{'danger' if is_expired else 'success'}'>"
                    f"{ident} ({age_str(now, last_referenced)}/{age_str(expiration_age, 0)})</div>"
                )

            return {
                "short_id": coloured_ident(image["short_id"]),
                "tags": "".join(map(coloured_ident, image["tags"] or [])),
                "created_at": date_str(image["created_at"]),
                "age": age_str(now, date_from(image["created_at"])),
                # "last_referenced": last_referenced_str(image["short_id"]),
                # "class": "text-danger" if would_cleanup_image(image, now, global_state) else "text-success",
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
    uptime = PlainCol("uptime")
    pid = PlainCol("pid")
    mem_usage = PlainCol("mem_usage")
    cpu = PlainCol("cpu")
    cmd = PlainCol("cmd")

    job = PlainCol("job")
    hints = PlainCol("hints")
    # link = LinkCol('Link', 'route_containers', url_kwargs=dict(id='id'), allow_sort=False)

    def sort_url(self, col_key, reverse=False):
        return url_for(
            self.endpoint,
            sort_key_containers=col_key,
            sort_direction_containers="desc" if reverse else "asc",
        )

    @staticmethod
    def html_from(endpoint, global_state, sort, reverse):
        now = datetime.now(tz=tz.tzutc())
        return ContainerTable(
            endpoint,
            items=sorted(
                (
                    {
                        "short_id": cnt["short_id"],
                        "name": cnt["name"],
                        "image": short_id(cnt["image"]) if is_uid(cnt["image"]) else cnt["image"],
                        "mem_usage": f"{(mem_stats.get('usage', 0)>>20)}MiB",
                        "cpu": f"{int(cpu_perc(cpu_stats, last_cpu_stats) * 1000) / 10}%",
                        "cmd": " ".join(cnt["show"]["Config"]["Cmd"])[:100],
                        "job": jobname_from(
                            cnt["show"]["HostConfig"]["Binds"]
                            or list(cnt["show"]["Config"]["Volumes"] or [])
                        ),
                        "created_at": date_str(date_from(cnt["show"]["Created"])),
                        "started_at": date_str(
                            started_at := date_from(cnt["show"]["State"]["StartedAt"])
                        ),
                        "uptime": age_str(now, started_at),
                        "status": cnt["show"]["State"]["Status"],
                        "hints": label_filter(cnt["show"]["Config"]["Labels"]),
                        "pid": int(cnt["show"]["State"]["Pid"]),
                        # https://getbootstrap.com/docs/4.0/utilities/colors/
                        "class": "text-danger"
                        if would_cleanup_container(cnt, now.timestamp(), global_state)
                        else "text-success",
                    }
                    for cnt, mem_stats, cpu_stats, last_cpu_stats in (
                        (
                            c,
                            c["stats"].get("memory_stats", {}),
                            c["stats"]["cpu_stats"],
                            c["last_stats"].get("cpu_stats"),
                        )
                        for c in global_state.containers.values()
                        if c.keys() > {"short_id", "name", "stats"}
                    )
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

    def sort_url(self, col_key, reverse=False):
        return url_for(
            self.endpoint,
            sort_key_volumes=col_key,
            sort_direction_volumes="desc" if reverse else "asc",
        )

    @staticmethod
    def html_from(endpoint, global_state, sort, reverse):
        now = datetime.now(tz=tz.tzutc())

        def dict_from(volume):
            now_timestamp = now.timestamp()
            created_timestamp = date_from(volume["CreatedAt"]).timestamp()

            def coloured_ident(ident: str, formatter=lambda s: s) -> str:
                is_expired, last_referenced, expiration_age = expired(
                    ident, global_state, now_timestamp, created_timestamp
                )
                return (
                    f"<div class='text-{'danger' if is_expired else 'success'}'>"
                    f"{formatter(ident)} ({age_str(now, last_referenced)}/{age_str(expiration_age, 0)})</div>"
                )

            return {
                "name": coloured_ident(volume["Name"], formatter=lambda s: s[:12]),
                "labels": "".join(map(coloured_ident, volume["Labels"] or [])),
                "created_at": date_str(date_from(volume["CreatedAt"])),
                "age": age_str(now, date_from(volume["CreatedAt"])),
                "mountpoint": volume["Mountpoint"],
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
        "event_horizon": age_str(time.time(), global_state.event_horizon),
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


async def response_cleanup(global_state: GlobalState):
    global_state.cleanup_fuse = global_state.intervals["cleanup"]
    return redirect(request.referrer or url_for("route_dashboard"))


async def response_rules(global_state):
    return "no rules yet"


async def response_messages(global_state):
    return "no messages yet"


async def response_volumes(global_state):
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


async def response_containers(global_state):
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


async def response_images(global_state):
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


async def response_dashboard(global_state):
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
        messages=[(date_str(m[0]), m[1], m[2]) for m in global_state.messages],
    )


async def container_table_html(global_state):
    return await response_containers(global_state)


async def image_table_html(global_state):
    return await response_images(global_state)


async def dashboard(global_state):
    return await response_dashboard(global_state)


# leftover - remove me after restarts
async def generic_html(generic, global_state):
    if generic == "favicon.ico":
        return ""
    if generic == "volumes":
        return await response_volumes(global_state)
    if generic == "rules":
        return await response_rules(global_state)
    if generic == "messages":
        return await response_messages(global_state)

    raise RuntimeError(f"not found: {generic}")


async def print_container_stats(global_state):
    hostname = open("/etc/hostname").read().strip()
    stats = [
        {
            "short_id": cnt["short_id"],
            "name": cnt["name"],
            "usage": mem_stats.get("usage", 0),
            "cmd": " ".join(cnt["show"]["Config"]["Cmd"]),
            "job": jobname_from(
                cnt["show"]["HostConfig"]["Binds"] or list(cnt["show"]["Config"]["Volumes"] or [])
            ),
            "cpu": cpu_perc(cpu_stats, last_cpu_stats),
            "created_at": date_from(cnt["show"]["Created"]),
            "started_at": date_from(cnt["show"]["State"]["StartedAt"]),
            "status": cnt["show"]["State"]["Status"],
            "hints": label_filter(cnt["show"]["Config"]["Labels"]),
            "pid": int(cnt["show"]["State"]["Pid"]),
            "container": cnt["container"],
        }
        for cnt, mem_stats, cpu_stats, last_cpu_stats in (
            (
                c,
                c["stats"].get("memory_stats", {}),
                c["stats"]["cpu_stats"],
                c["last_stats"].get("cpu_stats"),
            )
            for c in global_state.containers.values()
            if c.keys() > {"short_id", "name", "stats"}
        )
    ]

    os.system("clear")
    print(f"=[ {hostname} ]======================================")
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
        dur_str = f"{tds//86400:2d}d+{tds//3600%24:02d}:{tds//60%60:02d}"
        col_mem = "\033[1m\033[91m" if s["usage"] >> 30 > 2 else ""
        mem_str = f"{(s['usage']>>20)}MiB"
        col_cpu = "\033[1m\033[91m" if s["cpu"] > 2 else ""
        container_is_critical = (
            (s["started_at"] and tds // 3600 > 5) or s["status"] == "exited" or not s["started_at"]
        )
        col_cpu = "\033[1m\033[91m" if s["cpu"] > 2 else ""
        print(
            f"{s['short_id']:<12}  {s['name']:<25}"
            f" {s['pid']:>9}"
            f" {col_cpu}{int(s['cpu'] * 100):>8}%\033[0m"
            f" {col_mem}{mem_str:>9}\033[0m"
            f" {col_td}{dur_str}\033[0m"
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


async def watch_container(container, global_state: GlobalState):
    name = "unknown"
    containers = global_state.containers
    try:
        container_info = containers[container.id]
        container_info["container"] = container
        container_info["short_id"] = (short_id_ := short_id(container.id))
        container_info["show"] = (show := await container.show())
        container_info["name"] = (name := show["Name"][1:])
        container_info["image"] = show["Config"]["Image"]

        # wrong - other things could have happened since..
        # register_reference(image, date_from(show["Created"]).timestamp(), global_state)

        log().info(">> new container: %s %s", short_id_, name)

        async for stats in container.stats():
            container_info["last_stats"] = container_info.get("stats", {})
            container_info["stats"] = stats
            container_info["show"] = await container.show()

    except DockerError as exc:
        log().error("DockerError: %s", exc)
    except Exception:
        log().exception("Unhandled exception in watch_container()")
    finally:
        log().info("<< container terminated: %s %s", short_id_, name)
        del containers[container.id]


async def watch_images(docker_client, global_state):
    # TODO: also use events to register
    log().info("crawl images..")
    await asyncio.sleep(1)
    # raise RuntimeError("XXX")
    for image in await docker_client.images.list(all=True):
        # log().debug("  found image %s", image["Id"])
        if True or image["Id"] not in global_state.images:
            global_state.images.setdefault(image["Id"], {}).update(
                {
                    "short_id": short_id(image["Id"]),
                    "created_at": image["Created"],
                    "tags": [tag for tag in (image["RepoTags"] or []) if tag != "<none>:<none>"],
                    "size": image["Size"],
                    "parent": short_id(image["ParentId"]),
                }
            )


async def watch_containers(docker_client, global_state: GlobalState):
    # TODO: also use events to register
    log().info("crawl containers..")
    for container in await docker_client.containers.list(all=True):
        log().debug("  found container %s", container.id)
        if container.id not in global_state.containers:
            global_state.containers[container.id] = {}

            asyncio.ensure_future(watch_container(container, global_state))


async def watch_volumes(docker_client, global_state: GlobalState):
    # TODO: also use events to register
    log().info("crawl volumes..")
    for volume in (await docker_client.volumes.list())["Volumes"]:
        log().debug("  found volume %s", volume)
        if volume["Name"] not in global_state.volumes:
            global_state.volumes[volume["Name"]] = volume


def would_cleanup_container(container, now: int, global_state: GlobalState):
    if "show" not in container:
        return False
    status = (show := container["show"])["State"]["Status"]
    if status == "exited":
        return (
            now - date_from(show["State"]["FinishedAt"]).timestamp()
            > global_state.expiration_ages["container_exited"]
        )
    if status == "created":
        return (
            now - date_from(show["Created"]).timestamp()
            > global_state.expiration_ages["container_created"]
        )
    if status == "running":
        return (
            now - date_from(show["State"]["StartedAt"]).timestamp()
            > global_state.expiration_ages["container_running"]
        )
    return False


def expired_idents(image, now, global_state: GlobalState):
    log().debug("check expiration for image %s, tags=%s", image["short_id"], image["tags"])
    created_timestamp = int(date_from(image["created_at"]).timestamp())
    for tag in image["tags"] or []:
        if expired(tag, global_state, now, created_timestamp)[0]:
            yield tag
    if expired(image["short_id"], global_state, now, created_timestamp)[0]:
        yield image["short_id"]


async def image_from(docker_client: Docker, ident: str) -> bool:
    with suppress(DockerError):
        return await docker_client.images.get(ident)
    return None


async def volume_from(docker_client: Docker, ident: str) -> bool:
    with suppress(DockerError):
        for volume in (await docker_client.volumes.list())["Volumes"]:
            if volume["Name"].startswith(ident):
                return volume
    return None


async def cleanup(docker_client: Docker, global_state):
    log().info("Cleanup!..")

    report(global_state, "info", f"start cleanup", None)
    try:
        now = int(datetime.now().timestamp())
        # we could go through docker_client.containers, too, but to be more consistent, we work
        # on one structure only
        # also, we have to create a list we can operate on, in order to not modify the structure, we're
        # iterating
        for container_info in list(
            filter(
                lambda cnt: would_cleanup_container(cnt, now, global_state),
                global_state.containers.values(),
            )
        ):
            if not global_state.switches.get("remove_container"):
                log().info(f"skip removal of container {container_info['short_id']}")
                continue
            report(
                global_state,
                "warn",
                f"force removing container {container_info['short_id']}",
                container_info["container"],
            )
            await container_info["container"].delete(force=True, v=True)

        for image_info in global_state.images.values():
            for ident in expired_idents(image_info, now, global_state):
                if not global_state.switches.get("remove_images"):
                    log().info(f"skip removal of image/tag {ident}")
                    continue
                report(global_state, "info", f"remove image/tag {ident}", None)
                try:
                    await docker_client.images.delete(ident)
                except DockerError as exc:
                    log().error("Could not delete image %s, error was %s", ident, exc)

        for ident in [
            ident for ident in global_state.images if not await image_from(docker_client, ident)
        ]:
            log().warning("reference to image %s has not been cleaned up, I'll do it now..", ident)
            del global_state.images[ident]

        for ident in [
            ident for ident in global_state.volumes if not await volume_from(docker_client, ident)
        ]:
            log().warning("reference to volume %s has not been cleaned up, I'll do it now..", ident)
            del global_state.volumes[ident]
    finally:
        report(global_state, "info", f"cleanup done", None)


def report(global_state, msg_type, message: str, extra):
    # TODO: cleanup
    # TODO: persist
    log().info(message)
    global_state.messages.insert(0, (datetime.now().timestamp(), msg_type, message, str(extra)))
    global_state.messages = global_state.messages[
        : global_state.additional_values.get("message_history_size", 100)
    ]


def reconfigure(global_state: GlobalState) -> None:
    setup_introspection()
    for ident, reference in global_state.last_referenced.items():
        reference[1] = expiration_age_from_ident(ident, global_state)
