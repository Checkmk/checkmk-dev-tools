#!/usr/bin/env python3

"""Provide information about CI artifacts and make them available locally
Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
"""

# pylint: disable=too-many-branches
# pylint: disable=too-many-arguments
# pylint: disable=fixme

import asyncio
import json
import logging
import os
import sys
import time
from argparse import ArgumentParser
from argparse import Namespace as Args
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import datetime
from itertools import chain
from pathlib import Path
from subprocess import check_output
from typing import Any, Literal, cast

from jenkins import Jenkins
from trickkiste.logging_helper import apply_common_logging_cli_args, setup_logging
from trickkiste.misc import compact_dict, cwd, md5from, split_params

from .jenkins_utils import (
    AugmentedJenkinsClient,
    Build,
    BuildId,
    GenMap,
    Job,
    JobParams,
    JobParamValue,
    QueueId,
    apply_common_jenkins_cli_args,
    extract_credentials,
    params_from,
)
from .utils import Fatal
from .version import __version__

# Todo: warn about missing parameters
# Todo: default to `$REPO/package_download` rather than `out`
# Todo: decent error message when providing wrong path

PathHashes = Mapping[str, str]
shared_build_info: asyncio.Queue[str] = asyncio.Queue(maxsize=1)


def parse_args() -> Args:
    """Cool git like multi command argument parser"""
    parser = ArgumentParser(__doc__)

    apply_common_logging_cli_args(parser)
    apply_common_jenkins_cli_args(parser)

    parser.set_defaults(func=lambda *_: parser.print_usage())
    subparsers = parser.add_subparsers(help="available commands", metavar="CMD")

    parser.add_argument("--version", action="version", version=__version__)

    parser_info = subparsers.add_parser("info")
    parser_info.set_defaults(func=_fn_info)
    parser_info.add_argument(
        "job",
        type=lambda a: a.strip(" /"),
        help="Print some useful but informal information about a job",
    )

    def apply_common_args(subparser: ArgumentParser) -> None:
        subparser.add_argument("job", type=str)
        subparser.add_argument(
            "-b",
            "--base-dir",
            default=".",
            type=lambda p: Path(p).expanduser(),
            help="The base directory used to fetch directory/file hashes (see. --dependency-paths)",
        )

    def apply_request_args(subparser: ArgumentParser) -> None:
        subparser.add_argument(
            "-p",
            "--params",
            type=split_params,
            action="append",
            help="Job parameters used to check existing builds and to start new jobs with",
        )
        subparser.add_argument(
            "--params-no-check",
            type=split_params,
            action="append",
            help="Parameters used to start new jobs with, but not used to check existing builds",
        )
        subparser.add_argument(
            "-d",
            "--dependency-paths",
            type=str,
            action="append",
            help="Provide list of files/directories git hashes should be compared against a build",
        )
        subparser.add_argument(
            "-t",
            "--time-constraints",
            type=str,
            help=(
                "Provide a string (currently only 'today') which specifies the max age of a"
                " build to be considered valid."
            ),
        )
        subparser.add_argument(
            "-f",
            "--force-new-build",
            action="store_true",
            help="Don't check for existing matching builds, instead start a new build immediately",
        )

    def apply_download_args(subparser: ArgumentParser) -> None:
        subparser.add_argument(
            "-o",
            "--out-dir",
            default="out",
            type=Path,
            help="Directory to put artifacts to - relative to --base-dir if relative",
        )
        subparser.add_argument(
            "--no-remove-others",
            action="store_true",
            help="If set, existing files not part of artifacts won't be deleted",
        )

    parser_request = subparsers.add_parser(
        "request", help="Request a build or identify an existing one."
    )
    parser_request.set_defaults(func=_fn_request_build)
    apply_common_args(parser_request)
    apply_request_args(parser_request)
    parser_request.add_argument(
        "--passive",
        action="store_true",
        help=(
            "Instead of actually triggering new builds, write out the information needed to"
            " trigger a build instead. To be used inside pipline scripts in order to keep track"
            " of issuer."
        ),
    )

    parser_await_result = subparsers.add_parser(
        "await-result", help="Wait for existing build to finish, don't download."
    )
    parser_await_result.set_defaults(func=_fn_await_and_handle_build, download=False)
    apply_common_args(parser_await_result)
    parser_await_result.add_argument("build_number", type=int, nargs="?")

    parser_download = subparsers.add_parser(
        "download",
        help="Download artifacts of existing build. Wait for the build to finish if necessary.",
    )
    parser_download.set_defaults(func=_fn_await_and_handle_build, download=True)
    apply_common_args(parser_download)
    apply_download_args(parser_download)
    parser_download.add_argument("build_number", type=int, nargs="?")

    parser_fetch = subparsers.add_parser(
        "fetch",
        help="Trigger or identify matching build, wait for it to finish, download artifacts.",
    )
    parser_fetch.set_defaults(func=_fn_fetch, download=True)
    apply_common_args(parser_fetch)
    apply_request_args(parser_fetch)
    apply_download_args(parser_fetch)
    parser_fetch.add_argument(
        "-n",
        "--omit-new-build",
        action="store_true",
        help="Don't issue new builds, even if no matching build could be found",
    )

    parser_validate = subparsers.add_parser(
        "validate", help="Request, identify matching or trigger build and wait for it to finish."
    )
    parser_validate.set_defaults(func=_fn_fetch, download=False, omit_new_build=False)
    apply_common_args(parser_validate)
    apply_request_args(parser_validate)
    apply_download_args(parser_validate)

    return parser.parse_args()


def log() -> logging.Logger:
    """Convenience function retrieves 'our' logger"""
    return logging.getLogger("trickkiste.cmk-dev.cia")


def flatten(params: None | Sequence[JobParams]) -> None | JobParams:
    """Turns a list of job parameter dicts into one"""
    return {key: value for param in params for key, value in param.items()} if params else None


async def _fn_info(args: Args) -> None:
    """Entry point for information about job artifacts"""
    async with AugmentedJenkinsClient(
        **extract_credentials(args.credentials), timeout=args.timeout
    ) as jenkins:
        job_info = await jenkins.raw_job_info(args.job)
        class_name = job_info["_class"]
        if class_name == "com.cloudbees.hudson.plugins.folder.Folder":
            print(f"Folder({job_info['name']}, jobs: {len(cast(list[Any], job_info['jobs']))})")
        elif class_name == "org.jenkinsci.plugins.workflow.job.WorkflowJob":
            job = await Job.model_validate(job_info).expand(jenkins)
            print(job)
            for build_nr, build in job.build_infos.items():
                print(f"  - {build_nr}: {build}")
        else:
            raise Fatal(f"Don't know class type {class_name}")


def git_commit_id(git_dir: Path, path: None | Path | str = None) -> str:
    """Returns the git hash of combination of given paths. First one must be a directory, the
    second one is then considered relative"""
    if not git_dir.is_dir():
        raise Fatal(f"Provided path '{git_dir}', considered a git-checkout-dir is not a directory")

    if path and not (git_dir / path).exists():
        raise Fatal(f"There is no path to '{path}' inside '{git_dir}'")
    with cwd(git_dir):
        return check_output(
            # use the full hash - short hashes cannot be checked out and they are not
            # unique among machines
            ["git", "log", "--pretty=tformat:%H", "-n1"] + ([str(path)] if path else []),
            text=True,
        ).strip("\n")


def extract_path_hashes(parameters: GenMap) -> PathHashes:
    """Returns parameter dict generated from provided job parameters"""
    return {
        key: str(value)
        for key, value in split_params(str(parameters.get("DEPENDENCY_PATH_HASHES") or "")).items()
    }


def download_artifacts(
    client: Jenkins,
    build: Build,
    out_dir: Path,
    no_remove_others: bool = False,
) -> tuple[Sequence[str], Sequence[str]]:
    """Downloads all artifacts listed for given job/build to @out_dir"""
    # pylint: disable=protected-access
    # pylint: disable=too-many-locals

    downloaded_artifacts, skipped_artifacts = [], []

    # https://bugs.launchpad.net/python-jenkins/+bug/1973243
    # https://bugs.launchpad.net/python-jenkins/+bug/2018576

    # Beware! files with same content are mistakenly stored with the same filename.
    #         Also artifact directories are omitted in fingerprint list.
    # see: https://stackoverflow.com/questions/45555108
    # Our workaround: replace fingerprint names with those of the artifacts

    fp_url = f"{build.url}api/json?tree=fingerprint[hash]"
    log().debug("fetch artifact fingerprints from %s", fp_url)

    if not build.artifacts:
        raise Fatal("Job has no artifacts!")

    # create new fingerprints from artifact names an fingerprint hashes, keeping their order
    artifact_hashes = dict(
        zip(
            sorted(build.artifacts),
            (fprint["hash"] for fprint in client._session.get(fp_url).json()["fingerprint"]),
        )
    )

    if len(artifact_hashes) != len(build.artifacts):
        log().error(
            "inconsistent values for len(artifact_hashes)=%d != len(build.artifacts)=%d",
            len(artifact_hashes),
            len(build.artifacts),
        )

    if not artifact_hashes:
        raise Fatal(f"no (fingerprinted) artifacts found at {build.url}")

    existing_files = set(
        p.relative_to(out_dir).as_posix() for p in out_dir.glob("**/*") if p.is_file()
    )

    for artifact in build.artifacts:
        existing_files -= {artifact}
        fp_hash = artifact_hashes[artifact]
        log().debug("handle artifact: %s (md5: %s)", artifact, fp_hash)
        artifact_filename = out_dir / artifact
        local_hash = md5from(artifact_filename)

        if local_hash == fp_hash:
            log().debug("file is already available locally: %s (md5: %s)", artifact, fp_hash)
            skipped_artifacts.append(artifact)
            continue

        if local_hash and local_hash != fp_hash:
            log().debug(
                "update locally existing file %s - hashes differ (%s != %s)",
                artifact,
                local_hash,
                fp_hash,
            )

        with client._session.get(f"{build.url}artifact/{artifact}", stream=True) as reply:
            log().debug("download: %s", artifact)
            reply.raise_for_status()
            artifact_filename.parent.mkdir(parents=True, exist_ok=True)
            with open(artifact_filename, "wb") as out_file:
                for chunk in reply.iter_content(chunk_size=1 << 16):
                    out_file.write(chunk)
            downloaded_artifacts.append(artifact)

    if not no_remove_others:
        for path in existing_files - set(downloaded_artifacts) - set(skipped_artifacts):
            log().debug("Remove superfluous file %s", path)
            with suppress(FileNotFoundError):
                (out_dir / path).unlink()
    log().info(
        "%d artifacts available in '%s' (%d skipped, because they were up to date locally)",
        len(downloaded_artifacts) + len(skipped_artifacts),
        out_dir,
        len(skipped_artifacts),
    )

    return downloaded_artifacts, skipped_artifacts


def path_hashes_match(actual: PathHashes, required: PathHashes) -> bool:
    """Returns True if two given path hash mappings are semantically equal, i.e. at least one hash
    is prefix of the other (to handle short hashes, as returned with %h)
    >>> path_hashes_match({}, None)
    True
    >>> path_hashes_match(None, {"a": "abc"})
    False
    >>> path_hashes_match({"a": "abc"}, {"a": "abc"})
    True
    >>> path_hashes_match({"a": "abc"}, {"a": "abcde"})
    True
    """
    if not required:
        return True
    if required and not actual:
        return False
    if required.keys() - actual.keys():
        return False
    return all(
        hash_required.startswith(hash_actual) or hash_actual.startswith(hash_required)
        for key, hash_required in required.items()
        for hash_actual in (actual[key],)
    )


def find_mismatching_parameters(
    first: GenMap, second: GenMap
) -> Sequence[tuple[str, JobParamValue, JobParamValue]]:
    """Returns list of key and mismatching values in mapping @first which also occur in @second"""
    # TODO: find solution for unprovided parameters and default/empty values
    return [
        (key, cast(JobParamValue, first.get(key, "")), cast(JobParamValue, second.get(key, "")))
        for key in set(first.keys() | second.keys()) - {"DISABLE_CACHE"}
        if first.get(key) and first.get(key, "") != second.get(key, "")
    ]


def meets_constraints(
    build: Build,
    params: None | JobParams,
    time_constraints: None | str,
    path_hashes: PathHashes,
    *,
    now: datetime = datetime.now(),
) -> bool:
    """Checks if a set of requirements are met for a given build"""

    # TODO: discuss: should only the last job be taken into account?

    result = True

    # Prune if the build already failed (might still be ongoing)
    if build.result not in {None, "SUCCESS"}:
        log().debug("build #%s result was: %s", build.number, build.result)
        return False

    if mismatching_parameters := find_mismatching_parameters(params or {}, build.parameters):
        log().debug(
            "build #%s has mismatching parameters: %s", build.number, mismatching_parameters
        )
        result = False

    expected_path_hashes = extract_path_hashes(build.parameters)

    if expected_path_hashes and not path_hashes:
        log().warning(
            "strange: build #%s has expected path hashes set but we don't care?", build.number
        )

    if build.completed:
        if expected_path_hashes and not build.path_hashes:
            log().warning(
                "strange: build #%s has expected path hashes but didn't store the actual ones!",
                build.number,
            )

        if bool(path_hashes) != bool(build.path_hashes):
            log().warning(
                "strange: build #%s %s",
                build.number,
                (
                    "provides path hashes but we ignore them"
                    if not path_hashes
                    else "does not provide path hashes but we want to check them"
                ),
            )

        if not path_hashes_match(build.path_hashes, path_hashes):
            log().debug(
                "build #%s has mismatching path hashes: %s != %s",
                build.number,
                build.path_hashes,
                path_hashes,
            )
            result = False
    elif not path_hashes_match(expected_path_hashes, path_hashes):
        log().debug(
            "build #%s has been started with mismatching expected path hashes: %s != %s",
            build.number,
            expected_path_hashes,
            path_hashes,
        )
        result = False

    if time_constraints is None:
        pass

    elif time_constraints == "today":
        if datetime.fromtimestamp(build.timestamp).date() != datetime.now().date():
            log().debug(
                "build #%s does not meet time constraints: %s != %s",
                build.number,
                datetime.fromtimestamp(build.timestamp).date(),
                now.date(),
            )
            if result:
                log().warning(
                    "build #%s seems to have no relevant changes, but is invalidated by time"
                    " constraint only! You might want to check build conditions.",
                    build.number,
                )
            result = False
    else:
        raise Fatal(f"Don't understand time constraint specifier {time_constraints!r}")

    return result


def build_id_from_queue_item(client: Jenkins, queue_id: QueueId) -> BuildId:
    """Waits for queue item with given @queue_id to be scheduled and returns Build instance"""
    queue_item = client.get_queue_item(queue_id)
    log().info(
        "waiting for queue item %s to be scheduled (%s%s)",
        queue_id,
        queue_item["task"]["url"],
        queue_item["url"],
    )

    while True:
        queue_item = client.get_queue_item(queue_id)
        if executable := queue_item.get("executable"):
            return executable["number"]
        log().debug("still waiting in queue, because %s", queue_item["why"])
        time.sleep(1)


async def find_matching_queue_item(
    jenkins_client: AugmentedJenkinsClient,
    job: Job,
    params: None | JobParams,
    path_hashes: PathHashes,
) -> None | BuildId:
    """Looks for a queued build matching job and parameters and returns the QueueId"""
    for queue_item in await jenkins_client.queue_info():
        if not cast(str, queue_item.get("_class", "")).startswith("hudson.model.Queue"):
            continue
        if cast(str, cast(GenMap, queue_item.get("task", {})).get("url", "")) != job.url:
            continue

        queue_item_params = params_from(queue_item, "ParametersAction", "parameters")
        mismatching_parameters = find_mismatching_parameters(
            params or {},
            queue_item_params,
        )
        if mismatching_parameters:
            log().debug(
                "queue item %s has mismatching parameters: %s",
                queue_item.get("id"),
                mismatching_parameters,
            )
            continue

        expected_path_hashes = extract_path_hashes(queue_item_params)

        if expected_path_hashes and not path_hashes:
            log().warning(
                "strange: queued item %s has expected path hashes set but we don't care?",
                queue_item.get("id"),
            )

        if not path_hashes_match(expected_path_hashes, path_hashes):
            log().debug(
                "queued item %s has mismatching expected path hashes: %s != %s",
                queue_item.get("id"),
                expected_path_hashes,
                path_hashes,
            )
            continue
        return build_id_from_queue_item(jenkins_client.client, cast(int, queue_item.get("id")))

    return None


def compose_path_hashes(base_dir: Path, dependency_paths: Sequence[str]) -> PathHashes:
    """Returns local git hashes for each element in @dependency_paths"""
    return {
        path: git_commit_id(base_dir, path)
        for composite_paths in (dependency_paths or [])
        if composite_paths
        for path in composite_paths.split(",")
        if path
    }


def compose_out_dir(base_dir: Path, out_dir: Path) -> Path:
    """Returns out-dir from combined @base_dir and @out_dir. Raises if exists and is no dir."""
    out_dir = base_dir / (out_dir or "")
    if out_dir.exists() and not out_dir.is_dir():
        raise Fatal(f"Output directory path '{out_dir}' exists but is not a directory!")
    return out_dir


async def _fn_request_build(args: Args) -> None:
    """Entry point for a build request
    If none of the existing builds match the conditions a new build will be
    issued.
    This can get complicated since we don't know the outcome of unfinished or
    queued elements yet (result and dependency path hashes).
    """
    async with AugmentedJenkinsClient(
        **extract_credentials(args.credentials), timeout=args.timeout
    ) as jenkins_client:
        if not (job := await jenkins_client.job_info(args.job)).type == "WorkflowJob":
            raise Fatal(f"{args.job} is not a WorkflowJob")
        # In case we force a new build anyway we don't have to look for an existing one
        if matching_build := (
            None
            if args.force_new_build
            else await identify_matching_build(
                job,
                jenkins_client=jenkins_client,
                params=flatten(args.params),
                path_hashes=compose_path_hashes(args.base_dir, args.dependency_paths),
                time_constraints=args.time_constraints,
            )
        ):
            print(
                json.dumps(
                    {
                        "existing": {
                            "path": job.path,
                            "number": matching_build.number,
                            "url": matching_build.url,
                            "result": matching_build.result,
                            "new_build": False,
                            # "parameters": matching_build.parameters,
                            # "path_hashes": matching_build.path_hashes,
                        }
                    }
                )
            )
            return

        new_build_params = compose_build_params(
            params=flatten(args.params),
            params_no_check=flatten(args.params_no_check),
            path_hashes=compose_path_hashes(args.base_dir, args.dependency_paths),
        )
        if args.passive:
            print(
                json.dumps(
                    {
                        "new_build": {
                            "path": job.path,
                            "params": new_build_params,
                        }
                    }
                )
            )
        else:
            new_build = await trigger_build(jenkins_client, job, new_build_params)
            print(
                json.dumps(
                    {
                        "triggered_build": {
                            "path": job.path,
                            "number": new_build.number,
                            "url": new_build.url,
                            "params": new_build_params,
                        }
                    }
                )
            )


async def _fn_await_and_handle_build(args: Args) -> None:
    """Entry point for artifacts download only"""
    out_dir = args.base_dir / (getattr(args, "out_dir", "") or "")
    if out_dir.exists() and not out_dir.is_dir():
        raise Fatal(f"Output directory path '{out_dir}' exists but is not a directory!")

    split_job_arg = args.job.split(":")
    job_name = split_job_arg[0]
    job_number = int(split_job_arg[1]) if len(split_job_arg) > 1 else args.build_number

    if len(split_job_arg) > 1 and args.build_number:
        raise Fatal("Provide only one of separate build number or composite build name")

    if not job_number:
        raise Fatal("No build number provided. Use `<job-name>:<number>`.")

    async with AugmentedJenkinsClient(
        **extract_credentials(args.credentials), timeout=args.timeout
    ) as jenkins_client:
        completed_build = await await_build(
            job_name,
            job_number,
            jenkins_client=jenkins_client,
            check_result=True,
            path_hashes=None,
            allow_to_cancel=False,
        )
        print(
            json.dumps(
                {
                    "result": completed_build.result,
                    "artifacts": (
                        list(
                            chain(
                                *download_artifacts(
                                    jenkins_client.client,
                                    completed_build,
                                    out_dir,
                                    args.no_remove_others,
                                )
                            )
                        )
                        if args.download
                        else None
                    ),
                }
            )
        )


async def _fn_fetch(args: Args) -> None:
    """Entry point for fetching (request and download combined) artifacts"""
    out_dir = compose_out_dir(args.base_dir, args.out_dir)
    path_hashes = compose_path_hashes(args.base_dir, args.dependency_paths)
    async with AugmentedJenkinsClient(
        **extract_credentials(args.credentials), timeout=args.timeout
    ) as jenkins_client:
        if not (job := await jenkins_client.job_info(args.job)).type == "WorkflowJob":
            raise Fatal(f"{args.job} is not a WorkflowJob")
        # In case we force a new build anyway we don't have to look for an existing one
        matching_build = (
            None
            if args.force_new_build
            else await identify_matching_build(
                job,
                jenkins_client=jenkins_client,
                params=flatten(args.params),
                path_hashes=compose_path_hashes(args.base_dir, args.dependency_paths),
                time_constraints=args.time_constraints,
            )
        )
        if args.omit_new_build and not matching_build:
            raise Fatal(f"No matching build found for job '{job.name}' but new builds are omitted.")

        build_candidate = matching_build or await trigger_build(
            jenkins_client,
            job,
            compose_build_params(
                params=flatten(args.params),
                params_no_check=flatten(args.params_no_check),
                path_hashes=compose_path_hashes(args.base_dir, args.dependency_paths),
            ),
        )

        for key, value in build_candidate.__dict__.items():
            log().debug("  %s: %s", key, value)

        completed_build = await await_build(
            job.path,
            build_candidate.number,
            jenkins_client=jenkins_client,
            check_result=True,
            path_hashes=path_hashes,
        )
        downloaded_artifacts = (
            list(
                chain(
                    *download_artifacts(
                        jenkins_client.client,
                        completed_build,
                        out_dir,
                        args.no_remove_others,
                    )
                )
            )
            if args.download
            else []
        )

        print(
            json.dumps(
                {
                    "result": completed_build.result,
                    "artifacts": downloaded_artifacts,
                }
            )
        )


async def identify_matching_build(
    job: Job,
    *,
    jenkins_client: AugmentedJenkinsClient,
    params: None | JobParams,
    path_hashes: PathHashes,
    time_constraints: None | str,
) -> None | Build:
    """Find an existing build (finished, still running or queued) which matches our
    requirements specified by @job_full_path matching @params and
    @time_constraints.
    """
    # pylint: disable=too-many-locals

    # fetch a job's build history first
    await job.expand(jenkins_client)

    # Look for finished builds
    for build in filter(lambda b: b.completed, job.build_infos.values()):
        if meets_constraints(build, params, time_constraints, path_hashes):
            log().info("found matching finished build: %s (%s)", build.number, build.url)
            return build

    # Look for still unfinished builds
    for build in filter(lambda b: not b.completed, job.build_infos.values()):
        if meets_constraints(build, params, time_constraints, path_hashes):
            log().info("found matching unfinished build: %s (%s)", build.number, build.url)
            return build

    if matching_item := await find_matching_queue_item(jenkins_client, job, params, path_hashes):
        return await jenkins_client.build_info(job.path, matching_item)

    return None


def compose_build_params(
    params: None | JobParams,
    params_no_check: None | JobParams,
    path_hashes: PathHashes,
) -> JobParams:
    """Convenience function combining job parameters"""
    return {
        **(params or {}),
        **(params_no_check or {}),
        **(
            {
                "DEPENDENCY_PATH_HASHES": ",".join(
                    f"{key}={value}" for key, value in path_hashes.items()
                )
            }
            if path_hashes
            else {}
        ),
    }


async def trigger_build(
    jenkins_client: AugmentedJenkinsClient,
    job: Job,
    params: JobParams,
) -> Build:
    """Convenience function triggering a build with given @params and waiting for build number"""
    log().info("start new build for %s", job.path)
    log().info("  params=%s", compact_dict(params))

    return await jenkins_client.build_info(
        job.path,
        build_id_from_queue_item(
            jenkins_client.client,
            jenkins_client.client.build_job(job.path, parameters=params),
        ),
    )


async def await_build(
    job_full_path: str,
    build_number: int,
    *,
    jenkins_client: AugmentedJenkinsClient,
    check_result: bool,
    path_hashes: None | PathHashes,
    allow_to_cancel: bool = True,
) -> Build:
    """Awaits a Jenkins job build specified by @job_full_path and @build_number and returns the
    awaited Build object. Unexpected build failures or non-matching path hashes will be raised on.
    """
    current_build_info = await jenkins_client.build_info(job_full_path, build_number)
    if not current_build_info.completed:
        log().info("build #%s still in progress (%s)", build_number, current_build_info.url)
        if allow_to_cancel:
            await shared_build_info.put(json.dumps({"path": job_full_path, "number": build_number}))
        while True:
            if not current_build_info.completed:
                log().debug("build %s in progress", build_number)
                time.sleep(10)
                current_build_info = await jenkins_client.build_info(job_full_path, build_number)
                continue
            break

        log().info("build finished with result=%s", current_build_info.result)

    if check_result and current_build_info.result != "SUCCESS":
        raise Fatal(
            "The build we started has "
            f"result={current_build_info.result} ({current_build_info.url})"
        )

    if path_hashes and not path_hashes_match(current_build_info.path_hashes, path_hashes):
        raise Fatal(
            f"most recent build #{current_build_info.number} has mismatching path hashes: "
            f"{current_build_info.path_hashes} != {path_hashes}"
        )

    return current_build_info


async def stop_build(args: Args, ongoing_build_info: Mapping[str, str | int]) -> None:
    """Convenience function stopping a running or queued build"""
    job_name = ongoing_build_info["path"]
    job_number = ongoing_build_info["number"]
    assert isinstance(job_name, str)
    assert isinstance(job_number, int)

    log().info("stop build %d of %s", job_number, job_name)

    async with AugmentedJenkinsClient(
        **extract_credentials(args.credentials), timeout=args.timeout
    ) as jenkins_client:
        jenkins_client.client.stop_build(name=job_name, number=job_number)


def query_yes_no(
    question: str = "Continue?", default: None | Literal["yes", "y", "ye", "no", "n"] = "no"
) -> bool:
    """Ask a yes/no question via raw_input() and return their answer."""
    valid = {"yes": True, "y": True, "ye": True, "no": False, "n": False}
    if default is None:
        prompt = " [y/n] "
    elif default == "yes":
        prompt = " [Y/n] "
    elif default == "no":
        prompt = " [y/N] "
    else:
        raise ValueError(f"invalid default answer: '{default}'")

    while True:
        sys.stdout.write(question + prompt)
        choice = input().lower()
        if default is not None and choice == "":
            return valid[default]
        if choice in valid:
            return valid[choice]
        sys.stdout.write("Please respond with 'yes' or 'no' (or 'y' or 'n').\n")


def main() -> None:
    """Entry point for everything else"""
    try:
        args = parse_args()

        # for some reasons terminal type and properties are not recognized correctly by rich,
        # so 'temporarily' we force width and color
        if "CI" in os.environ:
            os.environ["FORCE_COLOR"] = "true"
            os.environ["COLUMNS"] = "200"

        setup_logging(
            logger=log(),
            level=args.log_level,
            show_time=False,
            show_name=False,
            show_funcname=False,
        )

        log().debug("Parsed args: %s", args)
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        if not shared_build_info.empty():
            if query_yes_no(question="Cancel ongoing build?"):
                ongoing_build_info = json.loads(shared_build_info.get_nowait())
                asyncio.run(stop_build(args=args, ongoing_build_info=ongoing_build_info))
                log().debug("Stopping ongoing job")
    except Fatal as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(-1) from exc


if __name__ == "__main__":
    main()
