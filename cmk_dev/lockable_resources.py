#!/usr/bin/env python3
# Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

"""List, lock and free lockable resources on Jenkins

Usage:

lockable-resources -vvv list

lockable-resources -vvv \
    [reserve, unreserve] first_resource second_resource third_resource

Lock all resources of the known label, e.g. "my_lock"
lockable-resources \
    list | \
    jq -c .my_lock[] | \
    xargs -I {} lockable-resources -vvv reserve {}
"""

import json
import logging
from argparse import ArgumentParser
from argparse import Namespace as Args
from typing import Dict, List, Literal

from requests import codes

from .jenkins_utils import (
    AugmentedJenkinsClient,
    apply_common_jenkins_cli_args,
    extract_credentials,
)
from .version import __version__

LockStateTypes = Literal["reserve", "unreserve"]
RESOURCE_LOCKED_STATUS_CODE: int = 423

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)-8s] [%(funcName)-5s:%(lineno)4s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def parse_args() -> Args:
    parser = ArgumentParser(description=__doc__)

    apply_common_jenkins_cli_args(parser)

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Verbose mode (for even more output use -vvvv)",
    )

    # option flags
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't do anything dangerous (for debugging purposes)",
    )
    parser.add_argument(
        "--fail-already-locked",
        action="store_true",
        help="Fail and raise a ResourceWarning exception if the resource is already locked (maybe by another job, user or previous lock request)",
    )
    parser.add_argument("--version", action="version", version=__version__)

    subparsers = parser.add_subparsers(dest="subcommand")

    list_parser = subparsers.add_parser("list", help="List all available resources")  # noqa: F841

    reserve_parser = subparsers.add_parser("reserve", help="Resource to reserve")
    unreserve_parser = subparsers.add_parser("unreserve", help="Resource to free")

    for subparser in [reserve_parser, unreserve_parser]:
        subparser.add_argument("resource_names", action="store", nargs="+", help="Resource to handle")

    return parser.parse_args()


def lock(args: Args) -> None:
    api_call(state="reserve", args=args)


def unlock(args: Args) -> None:
    api_call(state="unreserve", args=args)


def api_call(state: LockStateTypes, args: Args) -> None:
    with AugmentedJenkinsClient(**extract_credentials(), timeout=args.timeout) as jenkins:
        for resource_name in args.resource_names:
            if args.dry_run:
                logger.debug(f"Would {state} the resource {resource_name}")
                continue

            # https://github.com/jenkinsci/lockable-resources-plugin/issues/103
            url = f"{jenkins.client.server}/lockable-resources/{state}?resource={resource_name}"   # type: ignore[attr-defined]
            result = jenkins.client._session.post(url) # type: ignore[attr-defined]
            if result.status_code == RESOURCE_LOCKED_STATUS_CODE:
                logger.warning(f"Resource {resource_name} already locked")
                if args.fail_already_locked:
                    raise ResourceWarning(f"Resource {resource_name} already locked")
            elif result.status_code == codes.ok:
                logger.debug(f"{resource_name} {state}d successfully")
            else:
                logger.info(f"{result}")
                raise ValueError(f"{state} of {resource_name} failed due to: {result.reason}")

def list_resources(args: Args) -> None:
    # https://JENKINS_URL/lockable-resources/api/json
    with AugmentedJenkinsClient(**extract_credentials(), timeout=args.timeout) as jenkins:
        url = f"{jenkins.client.server}/lockable-resources/api/json?tree=resources[*]" # type: ignore[attr-defined]
        result = jenkins.client._session.get(url).json()
        logger.debug(f"Found {len(result['resources'])} lockable resources")

    buckets: Dict[str, List[str]] = {}
    for resource in result["resources"]:
        for label in resource["labelsAsList"]:
            buckets.setdefault(label, []).append(resource["name"])

    print(json.dumps(buckets))


def main() -> None:
    args: Args = parse_args()

    logger.setLevel(["WARNING", "INFO", "DEBUG"][min(args.verbose, 2)])
    logger.debug(args)

    match args.subcommand:
        case "reserve":
            lock(args=args)
        case "unreserve":
            unlock(args=args)
        case "list":
            list_resources(args=args)


if __name__ == '__main__':
    main()
