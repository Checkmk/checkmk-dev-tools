#!/usr/bin/env python3
"""Provide information about CI artifacts and make them available locally

Copyright (C) 2024 Checkmk GmbH - License: GNU General Public License v2
This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
conditions defined in the file COPYING, which is part of this source code package.
"""
# pylint: disable=too-few-public-methods
# pylint: disable=fixme

import asyncio
import json
import logging
import os
from argparse import ArgumentParser
from collections.abc import AsyncIterable, Iterable, Mapping, Sequence
from configparser import ConfigParser
from pathlib import Path
from typing import Any, Literal, Union, cast

from pydantic import BaseModel, Extra, Json, model_validator
from trickkiste.misc import asyncify, compact_dict, date_str, dur_str, split_params

import jenkins
from cmk_dev.utils import Fatal
from jenkins import Jenkins

GenMapVal = Union[None, bool, str, float, int, "GenMapArray", "GenMap"]
GenMapArray = Sequence[GenMapVal]
GenMap = Mapping[str, GenMapVal]

JobParamValue = Union[int, str]
JobParams = Mapping[str, JobParamValue]

QueueId = int
BuildId = int

JobResult = Literal["FAILURE", "SUCCESS", "ABORTED", "UNSTABLE"]


def log() -> logging.Logger:
    """Convenience function retrieves 'our' logger"""
    return logging.getLogger("trickkiste.cmk-dev.jenkins")


class PedanticBaseModel(BaseModel):
    """Even more pedandic.."""

    class Config:
        """Mandatory docstring"""

    #     extra = Extra.forbid


class JobTreeElement(PedanticBaseModel):
    """Models a Jenkins job build"""

    type: str
    name: str | None

    @model_validator(mode="before")
    @classmethod
    def correct(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our excpectations"""
        if "_class" in obj:
            obj["type"] = obj["_class"].rsplit(".", 1)[-1]
            del obj["_class"]
        return obj


class Folder(JobTreeElement):
    """Dummy folder element"""

    type: str = "Folder"
    # ignore: url, jobs


class SimpleBuild(PedanticBaseModel):
    """Minimal information we can get about a build"""

    number: int
    url: str
    node: None | str = None

    # ignore: name, executor, type


class Build(SimpleBuild):
    """Models a Jenkins job build"""

    number: int
    timestamp: int  # easier to handle than NaiveDatetime
    duration: int  # easier to handle than timedelta
    result: None | JobResult
    path_hashes: Mapping[str, str]
    artifacts: Sequence[str]
    inProgress: bool
    parameters: Mapping[str, str | bool]
    nextBuild: None | SimpleBuild = None

    # ignore: executor

    @model_validator(mode="before")
    @classmethod
    def correct(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our excpectations"""

        if not obj.get("result") in {None, "FAILURE", "SUCCESS", "ABORTED", "UNSTABLE"}:
            log().error("Build result has unexpected value %s", obj.get("result"))

        return {
            **obj,
            **{
                "timestamp": obj["timestamp"] // 1000,
                "duration": obj["duration"] // 1000,
                "parameters": params_from(obj, "ParametersAction", "parameters"),
                "path_hashes": cast(
                    Mapping[str, str],
                    params_from(obj, "CustomBuildPropertiesAction", "properties").get(
                        "path_hashes", {}
                    ),
                ),
                "artifacts": [
                    cast(Mapping[str, str], a)["relativePath"]
                    for a in cast(GenMapArray, obj["artifacts"])
                ],
                # SCM could be retrieved via 'hudson.plugins.git.util.BuildData'
                # "executor": (executor_value := obj.get("executor")) and executor_value["_class"],
            },
        }

    def __repr__(self) -> str:
        return f"<Build {compact_dict(self.model_dump(), maxlen=None)}>"

    def __str__(self) -> str:
        return (
            f"Build(nr={self.number}, {'completed' if  self.completed else 'running'}/{self.result}"
            f", started: {date_str(self.timestamp)}"
            f", took {dur_str(self.duration, fixed=True)}"
            f", params={{{compact_dict(self.parameters)}}}"
            f", hashes={{{compact_dict(self.path_hashes)}}})"
        )

    @property
    def completed(self) -> bool:
        """Convenience.."""
        # see core/src/main/java/hudson/model/Run.java#L543
        # @ https://github.com/jenkinsci/jenkins
        return not self.inProgress


class SimpleJob(JobTreeElement):
    """Minimal information we can get about a Jenkins job"""

    color: str
    name: None | str = None
    url: str


class Job(SimpleJob):
    """Models a Jenkins job"""

    path: str
    builds: Sequence[SimpleBuild] = []
    build_infos: Mapping[int, Build] = {}
    lastSuccessfulBuild: None | SimpleBuild = None
    lastCompletedBuild: None | SimpleBuild = None

    # ignore: actions: None | Sequence[dict[str, Any]] = None
    # ignore: description, displayName, displayNameOrNull, fullDisplayName, fullName, buildable

    def __str__(self) -> str:
        return f"Job('{self.path}', {len(self.builds or [])} builds)"

    @model_validator(mode="before")
    @classmethod
    def correct(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our excpectations"""
        if bool(obj.get("queueItem")) != obj.get("inQueue"):
            log().error(
                "Inconsistent values for job_info.get('queueItem')=%s and"
                " job_info.get('inQueue')=%s",
                obj.get("queueItem"),
                obj.get("inQueue"),
            )
        return {
            **obj,
            "path": obj.get("fullname") or obj.get("fullName"),
            "type": obj.get("type") or obj.get("_class") and obj["_class"].rsplit(".", 1)[-1],
        }

    async def expand(
        self,
        jenkins_client: "AugmentedJenkinsClient",
        max_build_infos: None | int = None,
    ) -> "Job":
        """Fetches elements which are not part of the simple job instance"""
        self.build_infos = {
            (build := await jenkins_client.build_info(self.path, b.number)).number: build
            for b in self.builds[:max_build_infos]
        }
        return self


class Change(PedanticBaseModel):
    """Infos about git change"""

    id: str
    author: str
    message: str
    author_email: str
    url: str
    affected: Sequence[str] = []

    def markdown(self) -> str:
        """Returns a nice looking rich.Text representation"""
        return f"[{self.id[:12]}]({self.url}) - {self.author} - `{self.message}`"

    @model_validator(mode="before")
    @classmethod
    def correct(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our excpectations"""
        return {
            "id": obj["id"],
            "author": obj["author"]["fullName"],
            "message": obj["msg"],
            "author_email": obj["authorEmail"],
            "url": f"https://review.lan.tribe29.com/gitweb?p=check_mk.git&a=commit&h={obj['id']}",
            "affected": obj["affectedPaths"],
        }


def params_from(build_info: GenMap, action_name: str, item_name: str) -> GenMap:
    """Return job parameters of provided @build_info as dict"""
    actions = cast(GenMapArray, build_info.get("actions") or [])
    for action in map(lambda a: cast(GenMap, a), actions):
        if cast(str, action.get("_class") or "").rsplit(".", 1)[-1] == action_name:
            if action_name == "ParametersAction":
                return {
                    str(p["name"]): p["value"]
                    for p in map(lambda a: cast(GenMap, a), cast(GenMapArray, action[item_name]))
                }
            if action_name == "CustomBuildPropertiesAction":
                return cast(GenMap, action[item_name])
    return {}


def apply_common_jenkins_cli_args(parser: ArgumentParser) -> None:
    """Decorates given @parser with arguments for credentials and timeout"""
    parser.add_argument(
        "-c",
        "--credentials",
        type=split_params,
        help=(
            "Provide 'url', 'username' and 'password' "
            "or 'username_env', 'url_env' and 'password_env' respectively."
            " If no credentials are provided, the JJB config at "
            " ~/.config/jenkins_jobs/jenkins_jobs.ini is being used."
        ),
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="Timeout in seconds for Jenkins API requests"
    )


def extract_credentials(credentials: None | Mapping[str, str] = None) -> Mapping[str, str]:
    """Turns the information provided via --credentials into actual values"""
    if credentials and (
        any(key in credentials for key in ("url", "url_env"))
        and any(key in credentials for key in ("username", "username_env"))
        and any(key in credentials for key in ("password", "password_env"))
    ):
        try:
            return {
                "url": credentials.get("url") or os.environ[credentials.get("url_env", "")],
                "username": credentials.get("username")
                or os.environ[credentials.get("username_env", "")],
                "password": credentials.get("password")
                or os.environ[credentials.get("password_env", "")],
            }
        except KeyError as exc:
            raise Fatal(f"Requested environment variable {exc} is not defined") from exc

    log().debug(
        "Credentials haven't been (fully) provided via --credentials, trying JJB config instead"
    )
    jjb_config = ConfigParser()
    jjb_config.read(Path("~/.config/jenkins_jobs/jenkins_jobs.ini").expanduser())
    return {
        "url": jjb_config["jenkins"]["url"],
        "username": jjb_config["jenkins"]["user"],
        "password": jjb_config["jenkins"]["password"],
    }


class AugmentedJenkinsClient:
    """Provides typed interface to a JenkinsClient instance"""

    def __init__(self, url: str, username: str, password: str, timeout: int | None = None) -> None:
        """Create a Jenkins client interface using the config file used for JJB"""
        self.client = Jenkins(
            url=url,
            username=username,
            password=password,
            timeout=timeout if timeout is not None else 60,
        )

    async def __aenter__(self) -> "AugmentedJenkinsClient":
        """Checks connection by validating whoami()"""
        whoami = (await self.whoami())["id"]
        username = self.client.auth and self.client.auth.username.decode() or ""
        if not whoami == username:
            log().warning(
                "client.get_whoami()=%s does not match jenkins_config['user']=%s", whoami, username
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    @asyncify
    def whoami(self) -> Mapping[str, str]:
        """Async wrapper for whoami"""
        # First API call gives us
        #   ERROR    │ requests_kerberos.kerberos_ │ handle_other(): Mutual authentication \
        #   unavailable on 403 response
        # no clue why. So we deactivate this level temporarily until we know better
        level = logging.getLogger("requests_kerberos.kerberos_").level
        logging.getLogger("requests_kerberos.kerberos_").setLevel(logging.FATAL)
        whoami = self.client.get_whoami()
        logging.getLogger("requests_kerberos.kerberos_").setLevel(level)
        return whoami

    async def traverse_job_tree(
        self,
        job_pattern: None | str | Sequence[str] = None,
        ignored_pattern: None | Iterable[str] = None,
    ) -> AsyncIterable[tuple[tuple[str, ...], Folder | SimpleJob]]:
        """Conveniently traverse through a Jenkins job structure recursively"""

        def recursive_traverse(
            jobs: Iterable[dict[str, Any]], parent_path: tuple[str, ...]
        ) -> Iterable[tuple[tuple[str, ...], Folder | SimpleJob]]:
            """recursively visit all @jobs and maintain @parent_path"""
            for raw_job in sorted(
                jobs,
                key=lambda j: j["name"].rsplit("_")[-1].replace(".", ""),
            ):
                node_path = parent_path + (raw_job["name"],)
                node_name = "/".join(node_path)
                jtype = raw_job["_class"].rsplit(".", 1)[-1]

                if any(p in node_name for p in ignored_pattern or []):
                    continue

                if jtype == "Folder":
                    yield node_path, Folder.model_validate(raw_job)
                    yield from recursive_traverse(raw_job.get("jobs", []), node_path)
                elif jtype in {"WorkflowJob", "FreeStyleProject"}:
                    yield node_path, SimpleJob.model_validate(raw_job)
                else:
                    raise RuntimeError(f"unknown job type {jtype}")

        log().info("fetch existing jobs..")
        all_jobs = cast(Iterable[dict[str, Any]], await self.raw_jobs())

        # find root folder for given pattern
        for pattern in [job_pattern] if isinstance(job_pattern, str) else job_pattern or [""]:
            path: tuple[str, ...] = ()
            sub_jobs = all_jobs
            for folder in (pattern and pattern.split("/")) or []:
                try:
                    sub_jobs = cast(
                        Iterable[dict[str, Any]],
                        next(j for j in sub_jobs if j["name"] == folder)["jobs"],
                    )
                except StopIteration as exc:
                    raise KeyError(pattern) from exc
                path = path + (folder,)
                yield path, Folder(name=folder)

            for element in recursive_traverse(sub_jobs, path):
                yield element

    async def build_time(self, job: str | Job, build_nr: None | int) -> None | int:
        """Returns the buildtime timestamp in seconds"""
        if build_nr is None:
            return None
        build_info = await self.raw_build_info(job if isinstance(job, str) else job.path, build_nr)
        return cast(int, build_info["timestamp"]) // 1000

    async def change_sets(self, job: str | Job, build_nr: None | int) -> Iterable[Change]:
        """ "Returns the list of change sets of a given build"""
        if build_nr is None:
            return []
        try:
            all_change_sets = (
                await self.raw_build_info(job if isinstance(job, str) else job.path, build_nr)
            )["changeSets"]
        except jenkins.JenkinsException as exc:
            log().error("Could not fetch change sets: %s", exc)
            return []

        git_change_sets = cast(
            Iterable[Mapping[str, Any]],
            (
                all_change_sets[0]["items"]  # type: ignore[index, call-overload]
                if all_change_sets
                else []
            ),
        )
        return [Change.model_validate(change) for change in git_change_sets]

    async def failing_transition_numbers(
        self, job: str | Job | Sequence[str]
    ) -> tuple[None | int, None | int, None | int]:
        """Returns build numbers of the first failing job and it's predecessor"""
        job_info = (
            job
            if isinstance(job, Job)
            else await self.job_info(job if isinstance(job, str) else "/".join(job))
        )
        last_successful = job_info.lastSuccessfulBuild
        if job_info.lastCompletedBuild and job_info.lastCompletedBuild == last_successful:
            return last_successful.number, None, last_successful.number

        first_failing = (
            (await self.build_info(job_info.path, last_successful.number)).nextBuild
            if last_successful
            else None
        )

        last_build = job_info.lastCompletedBuild
        return (
            last_successful.number if last_successful else None,
            first_failing.number if first_failing else None,
            last_build.number if last_build else None,
        )

    @asyncify
    def raw_jobs(self) -> GenMap:
        """Async wrapper for get_jobs()"""
        return self.client.get_jobs()

    @asyncify
    def raw_job_info(self, job_full_name: str) -> GenMap:
        """Fetches Jenkins job info for @job_full_name"""
        log().debug("fetch job info for %s", job_full_name)
        return self.client.get_job_info(job_full_name)

    async def job_info(self, job_full_name: str | Sequence[str]) -> Job:
        """Fetches Jenkins job info for @job_full_name"""
        return Job.model_validate(
            await self.raw_job_info(
                job_full_name if isinstance(job_full_name, str) else "/".join(job_full_name)
            )
        )

    @asyncify
    def raw_build_info(self, job_full_name: str, build_number: int) -> GenMap:
        """Returns raw Jenkins job info for @job_full_name"""
        log().debug("fetch build log for %s:%d", job_full_name, build_number)
        return self.client.get_build_info(job_full_name, build_number)

    async def build_info(self, job_full_name: str | Sequence[str], build_number: int) -> Build:
        """Fetches Jenkins build info for @job_full_name#@build_number"""
        return Build.model_validate(
            await self.raw_build_info(
                job_full_name if isinstance(job_full_name, str) else "/".join(job_full_name),
                build_number,
            )
        )

    @asyncify
    def queue_info(self) -> Sequence[GenMap]:
        """Async wrapper for get_queue_info()"""
        return self.client.get_queue_info()

    @asyncify
    def fetch_jvm_ressource_stats(self) -> Mapping[str, int]:
        """Returns information about available and used memory in JVM"""
        log().debug("fetch JVM ressource stats via script")
        return {
            key: int(value)
            for key, value in json.loads(
                self.client.run_script(
                    """
                import groovy.json.JsonOutput;

                Runtime runtime = Runtime.getRuntime();

                json = JsonOutput.toJson([
                    freeMemory: runtime.freeMemory(),
                    maxMemory: runtime.maxMemory(),
                ])
                println(json);
                """
                )
            ).items()
        }


async def main() -> None:  # pylint: disable=too-many-locals
    """Just a non-invasive test function"""
    logging.basicConfig(level=logging.WARNING)
    log().setLevel(logging.DEBUG)

    async with AugmentedJenkinsClient(**extract_credentials(), timeout=60) as jenkins_client:
        async for job_path, job in jenkins_client.traverse_job_tree("checkmk"):
            if job.type == "Folder":
                continue

            assert isinstance(job, SimpleJob)  # can only be a SimpleJob now..
            job_info = await jenkins_client.job_info(job_path)

            print(f"{job_info}, url={job_info.url}")
            last_successful, first_failing, last_build = (
                await jenkins_client.failing_transition_numbers(job_info)
            )
            assert bool(first_failing) is not bool(last_successful == last_build)
            if first_failing:
                print(last_successful, first_failing, last_build)
                change_set = await jenkins_client.change_sets(job_info, first_failing)
                print(change_set)

            await job_info.expand(jenkins_client)
            for build_nr, build_info in job_info.build_infos.items():
                assert build_nr == build_info.number
                print(f"  {build_info}, url={build_info.url}")


if __name__ == "__main__":
    asyncio.run(main())
