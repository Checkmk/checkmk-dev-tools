#!/usr/bin/env python3

# Copyright (C) 2026 Checkmk GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.
#
"""Manage remote artifact storage based on Pulp"""

import json
import logging
import re
import subprocess
import sys
import time
from argparse import Action, ArgumentParser
from argparse import Namespace as Args
from collections.abc import Sequence
from copy import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, overload

import requests

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)-8s] [%(funcName)-5s:%(lineno)4s] %(message)s",
    level=logging.INFO,
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 3
LIST_LIMIT = 10000
UNSUPPORTED_LABEL_CHARS_REGEX = r"[^a-zA-Z0-9]"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
BASE_TIME_ZONE = timezone.utc  # datetime.UTC requires Python 3.11+

class LabelAction(Action):
    # quite complex way of preserving the order of args and values for labels passed as CLI args
    def __call__(
        self,
        parser: ArgumentParser,
        namespace: Args,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        labels = getattr(namespace, "labels", None)

        if labels is None:
            labels = []
            setattr(namespace, "labels", labels)

        labels.append((option_string, values))

def parse_args() -> Args:
    parser = ArgumentParser(description="Manage remote artifact storage based on Pulp")

    # common option flags
    parser.add_argument(
        "--domain",
        required=False,
        choices=["backup", "nobackup", "default"],
        type=str,
        default="nobackup",
        help="Pulp domain to interact with, using value 'default' can cause unwanted backup behaviour",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't do anything for real on the local machine like download or upload. GET and Read operations are still performed.",
    )
    parser.add_argument(
        "--pretty",
        required=False,
        action="store_true",
        help="Pretty print the result",
    )
    parser.add_argument(
        "--print-response",
        required=False,
        action="store_true",
        help="Print response of operation to console",
    )
    parser.add_argument(
        "--pulp-dry-run",
        action="store_true",
        help="Don't do anything for real on the remote artifact storage (for debugging purposes only, script will exit right after execution)",
    )
    parser.add_argument(
        "-p",
        "--pulp-verbose",
        action="count",
        default=0,
        help="Pulp verbose mode (for even more output use -pp)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Script verbose mode (for even more output use -vv)",
    )

    subparsers = parser.add_subparsers(
        metavar="CMD",
        required=True,
        help="Available subcommands",
    )

    parser_download = subparsers.add_parser(
        "download",
        help="Download a file from the remote artifact storage",
    )
    parser_download.set_defaults(func=_fn_download)
    parser_download.add_argument(
        "--repository",
        required=True,
        type=str,
        help="Repository to download the file from",
    )
    add_label_key_argument(parser=parser_download, allow_query_chars=True, action=LabelAction, required=True)
    add_label_value_argument(parser=parser_download, allow_query_chars=True, action=LabelAction, required=True)
    parser_download.add_argument(
        "--limit",
        required=False,
        type=int,
        default=LIST_LIMIT,
        help="Limit the number of file content to show",
    )
    parser_download.add_argument(
        "--out-dir",
        default="download",
        type=Path,
        help="Directory to download files with the same path as on remote into",
    )
    parser_download.add_argument(
        "--total-download-timeout",
        type=int,
        default=240,
        help="Time in seconds a download for a single file may take before rising a TimeoutError",
    )

    parser_label = subparsers.add_parser(
        "label",
        help="Set new or overwrite existing labels of a file stored in a remote artifact storage",
    )
    parser_label.set_defaults(func=_fn_label)
    parser_label.add_argument(
        "--href",
        required=True,
        type=str,
        help="Unique href of the file to show the info for",
    )
    add_label_key_argument(parser=parser_label)
    add_label_value_argument(parser=parser_label)
    parser_label.add_argument(
        "--overwrite",
        required=False,
        action="store_true",
        default=False,
        help="Overwrite existing label values",
    )

    parser_list = subparsers.add_parser(
        "list",
        help="List all files in a remote artifact storage repository",
    )
    parser_list.set_defaults(func=_fn_list)
    parser_list.add_argument(
        "--repository",
        required=True,
        type=str,
        help="Repository to list all files for",
    )
    parser_list.add_argument(
        "--limit",
        required=False,
        type=int,
        default=LIST_LIMIT,
        help="Limit the number of file content to show",
    )

    parser_prune = subparsers.add_parser(
        "prune",
        help="Prune all files in a remote artifact storage repository older than <TIME>",
    )
    parser_prune.set_defaults(func=_fn_prune)
    parser_prune.add_argument(
        "--repository",
        required=True,
        type=str,
        help="Repository to prune all files matching condition",
    )
    parser_prune.add_argument(
        "--based-on",
        required=True,
        type=str,
        choices=["created", "last_updated", "retention", "label"],
        help="Remove file based on the timestamp or label",
    )
    parser_prune.add_argument(
        "--protection-time",
        required=False,
        type=int,
        default=1440,
        help="Time in minutes hold orphan content before becoming candidates for deletion",
    )
    add_label_key_argument(parser=parser_prune, allow_query_chars=True, action=LabelAction)
    add_label_value_argument(parser=parser_prune, allow_query_chars=True, action=LabelAction)
    parser_prune.add_argument(
        "--age",
        required=False,
        type=int,
        default=None,
        help="Age of artifact",
    )
    parser_prune.add_argument(
        "--age-from",
        required=False,
        type=int,
        default=None,
        help="Minimum file age to prune (inclusive)",
    )
    parser_prune.add_argument(
        "--age-to",
        required=False,
        type=int,
        default=None,
        help="Maximum file age to prune (inclusive)",
    )
    parser_prune.add_argument(
        "--age-unit",
        required=False,
        choices=["seconds", "minutes", "hours", "days", "weeks"],
        type=str,
        default=None,
        help="Unit used for --age, --age-from, and --age-to",
    )
    parser_prune.add_argument(
        "--limit",
        required=False,
        type=int,
        default=LIST_LIMIT,
        help="Limit the number of file content to show",
    )

    parser_query = subparsers.add_parser(
        "query",
        help="Search a file on a remote artifact storage",
    )
    parser_query.set_defaults(func=_fn_query)
    add_label_key_argument(parser=parser_query, allow_query_chars=True, action=LabelAction)
    add_label_value_argument(parser=parser_query, allow_query_chars=True, action=LabelAction)
    parser_query.add_argument(
        "--limit",
        required=False,
        type=int,
        default=LIST_LIMIT,
        help="Limit the number of file content to show",
    )

    parser_remove = subparsers.add_parser(
        "remove",
        help="Remove a file from a remote artifact storage",
    )
    parser_remove.set_defaults(func=_fn_remove)
    parser_remove.add_argument(
        "--repository",
        required=True,
        type=str,
        help="Repository to remove the file from",
    )
    parser_remove.add_argument(
        "--remote-path",
        required=True,
        type=Path,
        help="Remote path of file to remove",
    )
    parser_remove.add_argument(
        "--sha256",
        required=True,
        type=str,
        help="SHA256 of file to remove",
    )
    parser_remove.add_argument(
        "--schedule-cleanup",
        required=False,
        action="store_true",
        help="Schedule a cleanup to finally remove the deleted files",
    )
    parser_remove.add_argument(
        "--protection-time",
        required=False,
        type=int,
        default=0,
        help="Time in minutes hold orphan content before becoming candidates for deletion",
    )

    parser_show = subparsers.add_parser(
        "show",
        help="Get details of a file stored in a remote artifact storage",
    )
    parser_show.set_defaults(func=_fn_show)
    parser_show.add_argument(
        "--href",
        required=True,
        type=str,
        help="Unique href of the file to show the info for",
    )

    parser_upload = subparsers.add_parser(
        "upload",
        help="Upload a file to a remote artifact storage",
    )
    parser_upload.set_defaults(func=_fn_upload)
    parser_upload.add_argument(
        "--repository",
        required=True,
        type=str,
        help="Repository to upload the file to",
    )
    parser_upload.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path to file to upload",
    )
    parser_upload.add_argument(
        "--remote-path",
        required=True,
        type=Path,
        help="Remote path of file to upload",
    )
    add_label_key_argument(parser=parser_upload)
    add_label_value_argument(parser=parser_upload)
    parser_upload.add_argument(
        "--retention-duration",
        required=False,
        type=int,
        default=7,
        help="Retention duration of artifact",
    )
    parser_upload.add_argument(
        "--retention-unit",
        required=False,
        choices=["seconds", "minutes", "hours", "days", "weeks"],
        type=str,
        default="days",
        help="Retention time unit",
    )

    args = parser.parse_args()
    if getattr(args, "func", None) is _fn_prune:
        validate_prune_args(parser_prune, args)

    return args

def validate_prune_args(parser: ArgumentParser, args: Args) -> None:
    if args.based_on == "label":
        if not args.labels:
            parser.error("'--label-key' and '--label-value' are required when '--based-on=label'")

        if any([args.age, args.age_from, args.age_to, args.age_unit]):
            parser.error("'--age', '--age-from', '--age-to', and '--age-unit' cannot be used with '--based-on=label'")
    else:
        if args.labels:
            parser.error("'--label-key' and '--label-value' can only be used with '--based-on=label'")

        if args.age is None and args.age_from is None and args.age_to is None:
            parser.error("At least one of '--age', '--age-from', or '--age-to' is required when '--based-on' is not 'label'")

        if args.age_unit is None:
            parser.error("'--age-unit' is required when '--age', '--age-from', or '--age-to' is used")

def add_label_key_argument(parser: ArgumentParser, allow_query_chars: bool = False, action: str | type[Action] = "append", required: bool = False) -> None:
    unsupported_regex = UNSUPPORTED_LABEL_CHARS_REGEX
    if allow_query_chars:
        # how ugly this can be, we want to support "!" because this could be part of the key query syntax
        unsupported_regex = UNSUPPORTED_LABEL_CHARS_REGEX[:-1] + "!]"
    help = f"Key of label of file, characters not matching '{unsupported_regex}' will be replaced by '_'"

    parser.add_argument(
        "--label-key",
        required=required,
        action=action,
        type=lambda x: re.sub(unsupported_regex, "_", x),
        help=help,
    )
    if action is LabelAction:
        # LabelAction only creates 'labels' once a label option is passed
        parser.set_defaults(labels=None)

def add_label_value_argument(parser: ArgumentParser, allow_query_chars: bool = False, action: str | type[Action] = "append", required: bool = False) -> None:
    unsupported_regex = UNSUPPORTED_LABEL_CHARS_REGEX
    if allow_query_chars:
        # how ugly this can be, we want to support "!~" because this could be part of the value query syntax
        unsupported_regex = UNSUPPORTED_LABEL_CHARS_REGEX[:-1] + "!~]"
    help = f"Value of label of file (to be used together with --label-key), characters not matching '{unsupported_regex}' will be replaced by '_'"

    parser.add_argument(
        "--label-value",
        required=required,
        action=action,
        type=lambda x: re.sub(unsupported_regex, "_", x),
        help=help,
    )

def _pulp_base_arg(args: Args) -> list[str]:
    return [
        "pulp",
        *([f"--domain={args.domain}"] if args.domain else []),
        *(["--dry-run"] if args.pulp_dry_run else []),
        *(["-" + "v"*args.pulp_verbose] if args.pulp_verbose else []),
    ]

def _download_file(
    url: str,
    download_dir: Path,
    artifact_filename: Path,
    total_download_timeout: int = 240,
    dry_run: bool = False,
) -> None:
    for attempts_left in reversed(range(MAX_RETRY_ATTEMPTS + 1)):
        time_start = time.time()
        try:
            logger.debug("Download %s into %s", url, (download_dir / artifact_filename).resolve())
            if dry_run:
                logger.debug("DRY RUN: download: %s - successful (took 0.0s)", download_dir / artifact_filename)
                break
            (download_dir / artifact_filename.parent).mkdir(parents=True, exist_ok=True)
            current_dl_duration = 0.0
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            with open(download_dir / artifact_filename, mode="wb") as out_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if (
                        current_dl_duration := (time.time() - time_start)
                    ) > total_download_timeout:
                        raise TimeoutError(
                            f"Downloading of {url} took longer than {total_download_timeout}s"
                        )
                    if chunk:  # filter for keep-alive chunks
                        out_file.write(chunk)
            logger.debug("download: %s - successful (took %.2fs)", download_dir / artifact_filename, current_dl_duration)
            break
        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            TimeoutError,
        ) as exc:
            if not attempts_left:
                raise
            logger.warning(
                "_download_file() caught %r (%s/%s retries left)", exc, attempts_left, MAX_RETRY_ATTEMPTS
            )

def _fn_download(args: Args) -> None:
    distribution_info = subprocess.check_output(_pulp_base_arg(args=args) + [
        "file",
        "distribution",
        "show",
        f"--name={args.repository}",
    ]).decode("utf-8")
    distribution_info_json = json.loads(distribution_info)
    logger.debug("Distribution infos: %s", json.dumps(distribution_info_json, indent=4 if args.pretty else None))

    distribution_base_url = distribution_info_json.get("base_url")
    logger.debug("Distribution base URL: %s", distribution_base_url)

    file_query_results = _fn_query(args=args, return_result=True)

    for single_file_info in file_query_results:
        artifact_filename = Path(single_file_info["relative_path"])
        _download_file(
            url=f"{distribution_base_url}{artifact_filename}",
            download_dir=args.out_dir,
            artifact_filename=artifact_filename,
            total_download_timeout=args.total_download_timeout,
            dry_run=args.dry_run,
        )

@overload
def _fn_list(args: Args, return_result: Literal[True]) -> list[dict[str, Any]]: ...
@overload
def _fn_list(args: Args, return_result: Literal[False] = False) -> None: ...
def _fn_list(args: Args, return_result: bool = False) -> list[dict[str, Any]] | None:
    repo_list_result = subprocess.check_output(_pulp_base_arg(args=args) + [
        "file",
        "repository",
        "content",
        "list",
        f"--limit={getattr(args, 'limit', LIST_LIMIT)}",
        f"--repository={args.repository}",
    ]).decode("utf-8")
    repo_list_result_json: list[dict[str, Any]] = json.loads(repo_list_result)
    logger.debug("Repo list result: %s", json.dumps(repo_list_result_json, indent=4 if args.pretty else None))

    if return_result:
        return repo_list_result_json

    if args.print_response:
        print(json.dumps(repo_list_result_json, indent=4 if args.pretty else None))

    return None

def _labels_to_set(args: Args, set_retention: bool = False) -> dict[str, str]:
    all_key = args.label_key
    all_values = args.label_value
    all_key_values = {}

    if all_values and all_key:
        all_key_values = dict(zip(all_key, all_values))

    if set_retention:
        duration = timedelta(**{args.retention_unit: args.retention_duration})
        all_key_values.setdefault("retention", (datetime.now(BASE_TIME_ZONE) + duration).strftime(TIMESTAMP_FORMAT))

    return all_key_values

def _fn_label(args: Args, set_retention: bool = False, overwrite_labels: bool = False) -> None:
    # see https://pulpproject.org/pulpcore/docs/user/guides/manage-labels/
    all_key_values = _labels_to_set(args=args, set_retention=set_retention)
    overwrite = getattr(args, "overwrite", False) or overwrite_labels

    # getting all labels could be skipped if "--overwrite" is not set
    # but the info message about overwriting could then not be displayed
    result = _fn_show(args=args, href=args.href, return_result=True)
    existing_labels = result.get("pulp_labels", {})
    logger.debug("Existing labels for %s: %s", args.href, json.dumps(existing_labels, indent=4 if args.pretty else None))

    logger.debug("Labels for %s will be to set/extended to: %s", args.href, all_key_values)
    if all([len(all_key_values), args.href]):
        for key, val in all_key_values.items():
            if key in existing_labels and not overwrite:
                logger.info("Skipping existing label '%s'. Use '--overwrite' to replace it", key)
                continue

            if args.dry_run:
                logger.debug("DRY RUN: Set key '%s' with value '%s' for href '%s'", key, val, args.href)
                continue

            label_message = subprocess.check_output(_pulp_base_arg(args=args) + [
                "file",
                "content",
                "label",
                "set",
                f"--key={key}",
                f"--value={val}",
                f"--href={args.href}",
                # do not use too many identifiers which might be not exclusivly unique
            ]).decode("utf-8")
            if label_message:
                logger.debug("Label output: %s", label_message)

def _matches_prune_time_criteria(file_details: dict[str, Any], args: Args, start_time: datetime | None, end_time: datetime | None) -> bool:
    match args.based_on:
        case "retention":
            reference = file_details.get("pulp_labels", {}).get("retention")
        case "created":
            reference = file_details.get("pulp_created")
        case "last_updated":
            reference = file_details.get("pulp_last_updated")
        case _:
            logger.error("Unknown timestamp selection")
            return False

    if not reference:
        return False

    timestamp = datetime.strptime(reference, TIMESTAMP_FORMAT).replace(tzinfo=BASE_TIME_ZONE)

    if args.age is not None:
        logger.debug("File timestamp < specified end time: %s < %s", timestamp, end_time)
        return end_time is not None and timestamp < end_time

    if start_time is not None and timestamp < start_time:
        logger.debug("File timestamp < specified start_time: %s < %s", timestamp, start_time)
        return False

    if end_time is not None and timestamp > end_time:
        logger.debug("File timestamp > specified end_time: %s > %s", timestamp, end_time)
        return False

    logger.debug("File timestamp within specified range, prune the file")
    return True

def _fn_orphan_cleanup(args: Args) -> None:
    # see https://pulpproject.org/pulpcore/restapi/#tag/Orphans:-Cleanup/operation/orphans_cleanup_cleanup
    cleanup_command = _pulp_base_arg(args=args) + [
        "orphan",
        "cleanup",
        f"--protection-time={getattr(args, 'protection_time', 0)}",
    ]

    if args.dry_run or args.pulp_dry_run:
        logger.debug("DRY RUN: Would call '%s'", cleanup_command)
        return

    cleanup_info = subprocess.check_output(cleanup_command).decode("utf-8")
    logger.debug("Cleanup output result: %s", cleanup_info)

def _fn_prune(args: Args) -> None:
    deleted_count = 0
    matching_files: list[dict[str, Any]] = []

    match args.based_on:
        case "retention" | "created" | "last_updated":
            start_time = None
            end_time = None
            age_unit = args.age_unit
            now = datetime.now(BASE_TIME_ZONE)
            logger.debug("Now: %s", now)

            if args.age is not None:
                start_time = None
                end_time = now - timedelta(**{age_unit: args.age})
                logger.debug("Remove everything older than %s %s (%s and older) %s", args.age, args.age_unit, end_time, args.based_on)
            else:
                if args.age_to is not None:
                    start_time = now - timedelta(**{age_unit: args.age_to})

                if args.age_from is not None:
                    end_time = now - timedelta(**{age_unit: args.age_from})

                logger.debug("Prune timestamp range: %s (%s %s) - %s (%s %s) %s", start_time, args.age_to, args.age_unit, end_time, args.age_from, args.age_unit, args.based_on)

            matching_files = _fn_list(args=args, return_result=True)
        case "label":
            matching_files = _fn_query(args=args, return_result=True)
        case _:
            # the choices list should prevent getting here, but better safe than sorry
            logger.error("Unknown based_on selection: %s", args.based_on)
            return

    for file_details in matching_files:
        if args.based_on != "label" and not _matches_prune_time_criteria(file_details=file_details, args=args, start_time=start_time, end_time=end_time):
            continue

        logger.debug("Found file to prune: %s", json.dumps(file_details, indent=4 if args.pretty else None))

        tmp_args = copy(args)
        tmp_args.sha256 = file_details.get("sha256")
        tmp_args.remote_path = file_details.get("relative_path")
        tmp_args.schedule_cleanup = False
        logger.debug("tmp args: %s", tmp_args)

        _fn_remove(args=tmp_args)

        deleted_count += 1

    if deleted_count:
        _fn_orphan_cleanup(args=args)
        logger.debug("Found and deleted %d items", deleted_count)

@overload
def _fn_query(args: Args, return_result: Literal[True]) -> list[dict[str, Any]]: ...
@overload
def _fn_query(args: Args, return_result: Literal[False] = False) -> None: ...
def _fn_query(args: Args, return_result: bool = False) -> list[dict[str, Any]] | None:
    # see https://pulpproject.org/pulpcore/docs/user/guides/manage-labels/#filtering
    labels = args.labels or []
    label_parts = []
    pending_key = None

    for option, value in labels:
        if option == "--label-key":
            if pending_key is not None:
                label_parts.append(pending_key)

            pending_key = value

        elif option == "--label-value":
            if pending_key is not None:
                label_parts.append(_format_label(pending_key, value))
                pending_key = None

    if pending_key is not None:
        label_parts.append(pending_key)

    label_select_arg = ",".join(label_parts)

    logger.debug("Label selector: %s", label_select_arg)

    file_query_result = subprocess.check_output(_pulp_base_arg(args=args) + [
        "file",
        "content",
        "list",
        f"--limit={getattr(args, 'limit', LIST_LIMIT)}",
        f"--label-select={label_select_arg}",
    ]).decode("utf-8")

    file_query_result_json: list[dict[str, Any]] = json.loads(file_query_result)
    logger.debug("File query result: %s", json.dumps(file_query_result_json, indent=4 if args.pretty else None))

    if return_result:
        return file_query_result_json

    if args.print_response:
        print(json.dumps(file_query_result_json, indent=4 if args.pretty else None))

    return None

def _format_label(key: str, value: str) -> str:
    match value[:1]:
        case "!":
            match_operator = "!="
            value = value[1:]
        case "~":
            match_operator = "~"
            value = value[1:]
        case _:
            match_operator = "="

    return f"{key}{match_operator}{value}"

def _fn_remove(args: Args) -> None:
    remove_command = _pulp_base_arg(args=args) + [
        "file",
        "repository",
        "content",
        "remove",
        f"--repository={args.repository}",
        f"--sha256={args.sha256}",
        f"--relative-path={args.remote_path}",
    ]

    if args.dry_run or args.pulp_dry_run:
        logger.debug("DRY RUN: Would call '%s'", remove_command)
        return

    remove_info = subprocess.check_output(remove_command).decode("utf-8")
    logger.debug("Remove output result: %s", remove_info)

    if args.schedule_cleanup:
        _fn_orphan_cleanup(args=args)

@overload
def _fn_show(args: Args, href: str = "", *, return_result: Literal[True]) -> dict[str, Any]: ...
@overload
def _fn_show(args: Args, href: str = "", return_result: Literal[False] = False) -> None: ...
def _fn_show(args: Args, href: str = "", return_result: bool = False) -> dict[str, Any] | None:
    file_show_result = subprocess.check_output(_pulp_base_arg(args=args) + [
        "file",
        "content",
        "show",
        f"--href={getattr(args, 'href', None) or href}",
    ]).decode("utf-8")
    file_show_result_json: dict[str, Any] = json.loads(file_show_result)
    logger.debug("File show result: %s", json.dumps(file_show_result_json, indent=4 if args.pretty else None))

    if return_result:
        return file_show_result_json

    print(json.dumps(file_show_result_json, indent=4 if args.pretty else None))
    return None

def _fn_upload(args: Args) -> None:
    # see https://pulpproject.org/pulp_file/docs/user/guides/upload/
    upload_command = _pulp_base_arg(args=args) + [
        "file",
        "content",
        "upload",
        f"--repository={args.repository}",
        f"--file={args.file}",
        f"--relative-path={args.remote_path}",
    ]

    if args.dry_run or args.pulp_dry_run:
        logger.debug("DRY RUN: Would call '%s'", upload_command)
        # there is no href of an uploaded file which could be labeled
        logger.debug("DRY RUN: Would set labels: %s", _labels_to_set(args=args, set_retention=True))
        return

    upload_message = subprocess.check_output(upload_command).decode("utf-8")
    upload_message_json = json.loads(upload_message)
    logger.debug("Upload output: %s", json.dumps(upload_message_json, indent=4 if args.pretty else None))

    # get the most unique file identifiers for later usage
    href = upload_message_json.get("pulp_href", "")
    args.href = href

    _fn_label(args=args, set_retention=True, overwrite_labels=True)

    if args.print_response:
        _fn_show(args=args, href=href, return_result=False)

def main() -> None:
    args: Args = parse_args()

    logger.setLevel(["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"][min(args.verbose, 4)])
    logger.debug(args)

    if args.domain == "default":
        logger.warning("Using the default domain can cause unwanted behaviour, data might or might not be part of a backup directory")

    args.func(args=args)

if __name__ == '__main__':
    main()
