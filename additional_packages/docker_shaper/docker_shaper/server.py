#!/usr/bin/env python3

import asyncio
import importlib
import logging
import sys
from contextlib import suppress
from pathlib import Path

from apparat import fs_changes
from quart import Quart, Response, redirect

from docker_shaper import dynamic, utils

CONFIG_FILE = dynamic.BASE_DIR / "config.py"


def log() -> logging.Logger:
    """Logger for this module"""
    return logging.getLogger("docker-shaper")


async def schedule_print_container_stats(global_state: dynamic.GlobalState) -> None:
    while True:
        try:
            await asyncio.ensure_future(dynamic.print_container_stats(global_state))
            await asyncio.sleep(global_state.intervals.get("container_stats", 1))
        except Exception:
            dynamic.report(global_state)
            await asyncio.sleep(5)


async def schedule_print_state(global_state: dynamic.GlobalState):
    while True:
        try:
            await asyncio.ensure_future(dynamic.dump_global_state(global_state))
            await asyncio.sleep(global_state.intervals.get("state", 1))
        except Exception:
            dynamic.report(global_state)
            await asyncio.sleep(5)


async def schedule_cleanup(global_state: dynamic.GlobalState):
    while True:
        try:
            while True:
                if (
                    interval := global_state.intervals.get("cleanup", 3600)
                ) and global_state.cleanup_fuse > interval:
                    global_state.cleanup_fuse = 0
                    break
                if (interval - global_state.cleanup_fuse) % 60 == 0:
                    log().debug(
                        "cleanup: %s seconds to go.." % (interval - global_state.cleanup_fuse)
                    )
                await asyncio.sleep(1)
                global_state.cleanup_fuse += 1
            await asyncio.ensure_future(dynamic.cleanup(global_state))
        except Exception:
            dynamic.report(global_state)
            await asyncio.sleep(5)


def load_config(path: Path, global_state: dynamic.GlobalState) -> None:
    module = utils.load_module(path)
    try:
        module.modify(global_state)
        dynamic.reconfigure(global_state)
    except AttributeError:
        log().warning("File %s does not provide a `modify(global_state)` function")


async def watch_fs_changes(global_state: dynamic.GlobalState):
    """Watch for changes on imported files and reload them on demand"""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    async for changes in (
        relevant_changes
        async for chunk in fs_changes(
            Path(dynamic.__file__).parent, CONFIG_FILE.parent, min_interval=2, postpone=True
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
            ],
        )
    ):
        for changed_file, module in changes:
            try:
                if changed_file == CONFIG_FILE:
                    log().info("config file %s changed - apply changes", changed_file)
                    load_config(CONFIG_FILE, global_state)
                else:
                    log().info("file %s changed - reload module", changed_file)
                    assert module
                    importlib.reload(module)
            except Exception:  # pylint: disable=broad-except
                dynamic.report(global_state)
                await asyncio.sleep(5)
        try:
            dynamic.setup_introspection()
        except Exception:  # pylint: disable=broad-except
            dynamic.report(global_state)
            await asyncio.sleep(5)
    assert False


def no_serve():
    global_state = dynamic.GlobalState()
    load_config(CONFIG_FILE, global_state)
    dynamic.setup_introspection()
    with suppress(KeyboardInterrupt, BrokenPipeError):
        asyncio.ensure_future(global_state.docker_state.run())
        asyncio.ensure_future(dynamic.run_listen_messages(global_state))
        asyncio.ensure_future(watch_fs_changes(global_state))
        asyncio.ensure_future(schedule_print_container_stats(global_state))
        asyncio.ensure_future(schedule_print_state(global_state))
        asyncio.ensure_future(schedule_cleanup(global_state))
        asyncio.get_event_loop().run_forever()


def serve():
    """"""
    app = Quart(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    global_state = dynamic.GlobalState()
    load_config(CONFIG_FILE, global_state)
    dynamic.setup_introspection()

    async def generic_response(endpoint: str) -> Response:
        if not hasattr(dynamic, f"response_{endpoint}"):
            return f"Not known: {endpoint}"
        try:
            return await getattr(dynamic, f"response_{endpoint}")(global_state)
        except Exception:
            dynamic.report(global_state, "exception", f"exception in response_{endpoint}:")
            raise

    @app.route("/shutdown")
    def route_shutdown():
        app.terminator.set()
        return "Server shutting down..."

    @app.route("/<generic>", methods=["GET", "POST"])
    async def route_generic(generic) -> Response:
        if generic in {"favicon.ico", "favicon2.so"}:
            return ""
        return await generic_response(generic)

    @app.route("/cleanup", methods=["POST"])
    async def route_cleanup():
        return await generic_response("cleanup")

    @app.route("/rules")
    async def route_rules():
        return await generic_response("rules")

    @app.route("/messages")
    async def route_messages():
        return await generic_response("messages")

    @app.route("/delete_network")
    async def route_delete_network():
        return await generic_response("delete_network")

    @app.route("/inspect_network")
    async def route_inspect_network():
        return await generic_response("inspect_network")

    @app.route("/networks")
    async def route_networks():
        return await generic_response("networks")

    @app.route("/delete_volume")
    async def route_delete_volume():
        return await generic_response("delete_volume")

    @app.route("/inspect_volume")
    async def route_inspect_volume():
        return await generic_response("inspect_volume")

    @app.route("/volumes")
    async def route_volumes():
        return await generic_response("volumes")

    @app.route("/delete_container")
    async def route_delete_container():
        return await generic_response("delete_container")

    @app.route("/inspect_container")
    async def route_inspect_container():
        return await generic_response("inspect_container")

    @app.route("/containers")
    async def route_containers():
        return await generic_response("containers")

    @app.route("/remove_image_ident")
    async def route_remove_image_ident():
        return await generic_response("remove_image_ident")

    @app.route("/inspect_image")
    async def route_inspect_image():
        return await generic_response("inspect_image")

    @app.route("/images")
    async def route_images():
        return await generic_response("images")

    @app.route("/dashboard")
    async def route_dashboard():
        return await generic_response("dashboard")

    @app.route("/")
    async def root() -> Response:
        return redirect("dashboard")

    @app.websocket("/control")
    async def control() -> None:
        """Provides websocket for updates on changes
        see https://pgjones.gitlab.io/quart/how_to_guides/websockets.html
        """
        await dynamic.response_control_ws(global_state)

    @app.before_serving
    async def start_background_tasks():
        asyncio.ensure_future(global_state.docker_state.run())
        asyncio.ensure_future(dynamic.run_listen_messages(global_state))
        asyncio.ensure_future(watch_fs_changes(global_state))
        # asyncio.ensure_future(print_container_stats(global_state))
        asyncio.ensure_future(schedule_print_state(global_state))
        asyncio.ensure_future(schedule_cleanup(global_state))

    dynamic.report(global_state, "info", "docker-shaper started")

    app.terminator = asyncio.Event()
    app.run(
        host="0.0.0.0",
        port=5432,
        debug=False,
        use_reloader=False,
        loop=asyncio.get_event_loop(),
    )
