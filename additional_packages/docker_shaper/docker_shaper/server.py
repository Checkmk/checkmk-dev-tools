#!/usr/bin/env python3

import asyncio
import importlib
import logging
from contextlib import suppress
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_file_location
from itertools import count
from pathlib import Path

from aiodocker import Docker
from quart import Quart, Response, redirect

from docker_shaper import dynamic
from docker_shaper.utils import fs_changes, read_process_output

CONFIG_FILE = Path("~/.docker_shaper/config.py").expanduser()


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper.server")


async def schedule_print_container_stats(global_state):
    while True:
        try:
            await asyncio.ensure_future(dynamic.print_container_stats(global_state))
            await asyncio.sleep(global_state.intervals.get("container_stats", 1))
        except Exception as exc:
            log().exception("Unhandled exception caught!")
            dynamic.report(
                global_state, "error", f"exception in container_stats scheduler: {exc}", exc
            )
            await asyncio.sleep(5)


async def schedule_print_state(global_state):
    while True:
        try:
            await asyncio.ensure_future(dynamic.dump_global_state(global_state))
            await asyncio.sleep(global_state.intervals.get("state", 1))
        except Exception as exc:
            log().exception("Unhandled exception caught!")
            dynamic.report(global_state, "error", f"exception in state scheduler: {exc}", exc)
            await asyncio.sleep(5)


async def schedule_watch_containers(global_state):
    # TODO: also use events to register
    try:
        docker = Docker()
        while True:
            try:
                await asyncio.ensure_future(dynamic.watch_containers(docker, global_state))
                await asyncio.sleep(global_state.intervals.get("container_update", 1))
            except Exception as exc:
                log().exception("Unhandled exception caught!")
                dynamic.report(
                    global_state, "error", f"exception in container_update scheduler: {exc}", exc
                )
                await asyncio.sleep(5)
    finally:
        await docker.close()


async def schedule_watch_images(global_state):
    # TODO: also use events to register
    try:
        docker = Docker()
        while True:
            try:
                await asyncio.ensure_future(dynamic.watch_images(docker, global_state))
                await asyncio.sleep(global_state.intervals.get("image_update", 1))
            except Exception as exc:
                log().exception("Unhandled exception caught!")
                dynamic.report(
                    global_state, "error", f"exception in image_update scheduler: {exc}", exc
                )
                await asyncio.sleep(5)
    finally:
        await docker.close()


async def schedule_watch_volumes(global_state):
    # TODO: also use events to register
    try:
        docker = Docker()
        while True:
            try:
                await asyncio.ensure_future(dynamic.watch_volumes(docker, global_state))
                await asyncio.sleep(global_state.intervals.get("volumes_update", 1))
            except Exception as exc:
                log().exception("Unhandled exception in volumes_update()!")
                dynamic.report(global_state, "error", f"exception in volumes_update(): {exc}", exc)
                await asyncio.sleep(5)
    finally:
        await docker.close()


async def schedule_cleanup(global_state: dynamic.GlobalState):
    try:
        docker = Docker()
        while True:
            try:
                while True:
                    if (
                        interval := global_state.intervals.get("cleanup", 3600)
                    ) and global_state.cleanup_fuse > interval:
                        global_state.cleanup_fuse = 0
                        break
                    if (interval - global_state.cleanup_fuse) % 10 == 0:
                        log().debug(
                            "cleanup: %s seconds to go.." % (interval - global_state.cleanup_fuse)
                        )
                    await asyncio.sleep(1)
                    global_state.cleanup_fuse += 1
                await asyncio.ensure_future(dynamic.cleanup(docker, global_state))
            except Exception as exc:
                log().exception("Unhandled exception caught in cleanup()!")
                dynamic.report(global_state, "error", f"exception in cleanup scheduler: {exc}", exc)
                await asyncio.sleep(5)
    finally:
        await docker.close()


def load_config(path, global_state):
    spec = spec_from_file_location("dynamic_config", path)
    if not (spec and spec.loader):
        raise RuntimeError("Could not load")
    module = module_from_spec(spec)
    assert module
    # assert isinstance(spec.loader, SourceFileLoader)
    loader: SourceFileLoader = spec.loader
    loader.exec_module(module)
    try:
        module.modify(global_state)
        dynamic.reconfigure(global_state)
    except AttributeError:
        log().warning("File %s does not provide a `modify(global_state)` function")


async def watch_fs_changes(global_state):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    async for changed_file in fs_changes(
        Path(dynamic.__file__).parent, CONFIG_FILE.parent, timeout=1
    ):
        log().info("file %s changed - reload module", changed_file)
        try:
            if changed_file == Path(dynamic.__file__):
                importlib.reload(dynamic)
                dynamic.setup_introspection()
            elif changed_file == CONFIG_FILE:
                load_config(CONFIG_FILE, global_state)
        except Exception as exc:
            log().exception("Reloading dynamic part failed!")
            dynamic.report(global_state, "error", f"exception in module reload: {exc}", exc)
            await asyncio.sleep(5)
    assert False


async def handle_docker_events(global_state):
    try:
        docker = Docker()
        async for line in read_process_output("docker events"):
            try:
                await asyncio.ensure_future(
                    dynamic.handle_docker_event_line(docker, global_state, line)
                )
            except Exception as exc:
                log().exception("Unhandled exception caught!")
                dynamic.report(
                    global_state, "error", f"exception in docker event watcher: {exc}", exc
                )
                await asyncio.sleep(5)
    finally:
        await docker.close()


def no_serve():
    global_state = dynamic.GlobalState()
    load_config(CONFIG_FILE, global_state)
    with suppress(KeyboardInterrupt, BrokenPipeError):
        asyncio.ensure_future(watch_fs_changes(global_state))
        asyncio.ensure_future(schedule_print_container_stats(global_state))
        asyncio.ensure_future(schedule_print_state(global_state))
        asyncio.ensure_future(schedule_watch_containers(global_state))
        asyncio.ensure_future(schedule_watch_images(global_state))
        asyncio.ensure_future(schedule_watch_volumes(global_state))
        asyncio.ensure_future(handle_docker_events(global_state))
        asyncio.ensure_future(schedule_cleanup(global_state))
        asyncio.get_event_loop().run_forever()


def serve():
    """"""
    app = Quart(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    global_state = dynamic.GlobalState()
    load_config(CONFIG_FILE, global_state)

    async def generic_response(endpoint: str) -> Response:
        try:
            return await getattr(dynamic, f"response_{endpoint}")(global_state)
        except Exception as exc:
            log().exception("Unhandled exception in response_%s()", endpoint)
            dynamic.report(global_state, "error", f"exception in response_{endpoint}: {exc}", exc)

    async def self_destroy():
        await app.terminator.wait()
        print("BOOM")
        app.shutdown()
        asyncio.get_event_loop().stop()
        print("!!!!")

    @app.route("/shutdown")
    def route_shutdown():
        app.terminator.set()
        return "Server shutting down..."

    @app.route("/<generic>", methods=["GET", "POST"])
    async def route_generic(generic) -> Response:
        if generic == "favicon.ico":
            return ""
        return await generic_response(generic)

    @app.route("/cleanup", methods=["POST"])
    async def route_cleanup():
        return await generic_response("cleanup")

    @app.route("/volumes")
    async def route_volumes():
        return await generic_response("volumes")

    @app.route("/rules")
    async def route_rules():
        return await generic_response("rules")

    @app.route("/messages")
    async def route_messages():
        return await generic_response("messages")

    @app.route("/containers")
    async def route_containers():
        return await generic_response("containers")

    @app.route("/images")
    async def route_images():
        return await generic_response("images")

    @app.route("/dashboard")
    async def route_dashboard():
        return await generic_response("dashboard")

    @app.route("/")
    async def root() -> Response:
        return redirect("dashboard")

    @app.before_serving
    async def create_db_pool():
        asyncio.ensure_future(self_destroy())
        asyncio.ensure_future(watch_fs_changes(global_state))
        # asyncio.ensure_future(print_container_stats(global_state))
        asyncio.ensure_future(schedule_print_state(global_state))
        asyncio.ensure_future(schedule_watch_containers(global_state))
        asyncio.ensure_future(schedule_watch_images(global_state))
        asyncio.ensure_future(schedule_watch_volumes(global_state))
        asyncio.ensure_future(handle_docker_events(global_state))
        asyncio.ensure_future(schedule_cleanup(global_state))

    app.terminator = asyncio.Event()
    app.run(
        host="0.0.0.0",
        port=5432,
        debug=False,
        use_reloader=False,
        loop=asyncio.get_event_loop(),
    )
