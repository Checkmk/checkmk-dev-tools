#!/usr/bin/env python3

"""Functionality that might change during runtime
"""
# pylint: disable=invalid-name  # names come from aiodocker, not my fault
# pylint: disable=too-many-instance-attributes,too-few-public-methods

import asyncio
import json
import logging
from contextlib import suppress

from aiodocker import Docker


async def main() -> None:
    async with Docker() as docker_client:
        subscriber = docker_client.events.subscribe()
        while True:
            e = await subscriber.get()
            del e["time"]
            print(json.dumps(e))


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
