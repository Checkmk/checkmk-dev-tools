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
from collections.abc import (
    AsyncIterable,
    Iterable,
    Mapping,
    MutableMapping,
    Sequence,
    Set,
)
from configparser import ConfigParser
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Union, cast

from jenkins import Jenkins, JenkinsException
from pydantic import BaseModel, ConfigDict, Json, model_validator
from trickkiste.misc import async_retry, asyncify, compact_dict, date_str, dur_str, split_params

from cmk_dev.utils import Fatal

GenMapVal = Union[None, bool, str, float, int, "GenMapArray", "GenMap"]
GenMapArray = Sequence[GenMapVal]
GenMap = Mapping[str, GenMapVal]

JobParamValue = Union[int, str, bool]
JobParams = MutableMapping[str, JobParamValue]

QueueId = int
BuildId = int

JobResult = Literal["FAILURE", "SUCCESS", "ABORTED", "UNSTABLE", "PROGRESS", "RUNNING"]

MAX_ATTEMPTS = 3


def log() -> logging.Logger:
    """Convenience function retrieves 'our' logger"""
    return logging.getLogger("trickkiste.cmk-dev.jenkins")


class PedanticBaseModel(BaseModel):
    """Even more pedantic.."""

    # Set to "forbid" in ordert to enforce a stricter pydantic validation which
    # raises on unknown attributes. Activate it in development only since it will
    # break runtimes when Jenkins API changes again.
    model_config = ConfigDict(extra="ignore")

    _ignored_keys: ClassVar[Set[str]] = set()
    type: str

    @model_validator(mode="before")
    @classmethod
    def correct_base(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        # Most (not all unfortunately) objects retrieved by the Jenkins API have
        # a _class element, which is a hierarchical class identifier, e.g.
        # 'org.jenkinsci.plugins.workflow.job.WorkflowJob'
        # This string is hard to work with and attributes with a '_' prefix are
        # neither pythonic nor pydantic, so we extract the interesting part and
        # rename it to 'type'
        return {
            # pass through all keys but "_class" and all keys in `_ignored_keys`
            **{
                key: value
                for key, value in obj.items()
                if key not in cls._ignored_keys and key != "_class"
            },
            # and turn "_class" into "type" if availabe (taking only the last part)
            **({"type": obj["_class"].rsplit(".", 1)[-1]} if "_class" in obj else {}),
        }


class JobTreeElement(PedanticBaseModel):
    """Models a Jenkins job build"""

    name: str | None


class Folder(JobTreeElement):
    """Dummy folder element"""

    type: str = "Folder"

    _ignored_keys = {
        "url",
        "jobs",
        "fullname",
    }


class SimpleBuild(PedanticBaseModel):
    """Minimal information we can get about a build"""

    number: int
    url: str
    node: None | str = None

    # remove `type` (get_running_builds() doesn't get us _class)
    type: str = "undefined"

    _ignored_keys = {"executor", "name"}


class Cause(PedanticBaseModel):
    """Cause
    actually type is one of
        'Cause$UpstreamCause' =>            'Started by upstream project..'
        'BuildUpstreamCause'  =>            'Started by upstream project..'
        'TimerTrigger$TimerTriggerCause' => 'Started by timer'
        'Cause$UserIdCause' =>              'Started by user <user>'
        'ReplayCause' =>                    'Replayed #<id>'
        'RebuildCause' =>                   'Rebuilds build <id>'
        'SCMTrigger$SCMTriggerCause' =>     'Started by an SCM change'
        'GerritCause' =>                    'Triggered by Gerrit'
        'GerritUserCause' =>                'Retriggered by user <user>'
    but we don't need to be pydantic here..
    """

    type: str
    shortDescription: str
    upstreamProject: None | str = None
    upstreamBuild: None | int = None
    userId: None | str = None

    _ignored_keys = {"upstreamUrl", "userName"}


class Build(SimpleBuild):
    """Models a Jenkins job build"""

    timestamp: int  # easier to handle than NaiveDatetime
    duration: int  # easier to handle than timedelta
    result: None | JobResult
    path_hashes: Mapping[str, str]
    artifacts: Sequence[str]
    inProgress: bool
    parameters: Mapping[str, str | bool]
    causes: Sequence[Cause]
    nextBuild: None | SimpleBuild = None
    type: str

    _ignored_keys = {
        "actions",
        "building",
        "changeSets",
        "culprits",
        "description",
        "displayName",
        "estimatedDuration",
        "executor",
        "fullDisplayName",
        "id",
        "keepLog",
        "previousBuild",
        "queueId",
    }

    @model_validator(mode="before")
    @classmethod
    def correct_build(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our expectations"""

        if obj.get("result") not in {
            None,
            "FAILURE",
            "SUCCESS",
            "ABORTED",
            "UNSTABLE",
            "RUNNING",
            "PROGRESS",
        }:
            log().error("Build result has unexpected value %s", obj.get("result"))

        # hack to create Job build with InfluxDB data
        if "parameters" in obj:
            this_parameters = obj["parameters"]
        else:
            this_parameters = params_from(
                build_info=obj, action_name="ParametersAction", item_name="parameters"
            )
        causes = obj.get("causes") or [
            Cause.model_validate(cause)
            for cause in cast(
                Sequence[Mapping[str, str]],
                params_from(build_info=obj, action_name="CauseAction", item_name="causes"),
            )
        ]
        path_hashes = split_params(cast(str, this_parameters.get("DEPENDENCY_PATH_HASHES", "")))

        return {
            **obj,
            "timestamp": obj["timestamp"] // 1000,
            "duration": obj["duration"] // 1000,
            "parameters": this_parameters,
            "causes": causes,
            "path_hashes": path_hashes,
            "artifacts": [
                cast(Mapping[str, str], a)["relativePath"]
                for a in cast(GenMapArray, obj["artifacts"])
            ],
        }

    def __repr__(self) -> str:
        return f"<Build {compact_dict(self.model_dump(), maxlen=None)}>"

    def __str__(self) -> str:
        return (
            f"Build(nr={self.number}, {'completed' if self.completed else 'running'}/{self.result}"
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
    type: Literal["WorkflowJob", "FreeStyleProject", "MatrixProject"]


class Job(SimpleJob):
    """Models a full Jenkins job"""

    path: str
    builds: Sequence[SimpleBuild] = []
    build_infos: Mapping[int, Build] = {}
    lastSuccessfulBuild: None | SimpleBuild = None
    lastCompletedBuild: None | SimpleBuild = None

    _ignored_keys = {
        "actions",
        "buildable",
        "concurrentBuild",
        "description",
        "disabled",
        "displayName",
        "displayNameOrNull",
        "downstreamProjects",
        "firstBuild",
        "fullDisplayName",
        "fullName",
        "healthReport",
        "inQueue",
        "keepDependencies",
        "labelExpression",
        "lastBuild",
        "lastFailedBuild",
        "lastStableBuild",
        "lastUnstableBuild",
        "lastUnsuccessfulBuild",
        "nextBuildNumber",
        "property",
        "queueItem",
        "resumeBlocked",
        "scm",
        "upstreamProjects",
    }

    def __str__(self) -> str:
        return f"Job('{self.path}', {len(self.builds or [])} builds)"

    @model_validator(mode="before")
    @classmethod
    def correct_job(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our expectations"""
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
        }

    async def expand(
        self,
        jenkins_client: "AugmentedJenkinsClient",
        max_build_infos: None | int = None,
    ) -> "Job":
        """Fetches elements which are not part of the simple job instance"""

        async def resilient_build_info(path: str, number: int) -> Build | None:
            with suppress(JenkinsException):
                return await jenkins_client.build_info(path, number)
            return None

        self.build_infos = {
            build.number: build
            for b in self.builds[:max_build_infos]
            if (build := await resilient_build_info(self.path, b.number))
        }
        return self


class BuildNode(PedanticBaseModel):
    """A build node model"""

    name: str
    displayName: str

    # BuildNode instances never have a _class, but we want to derive PedanticBaseModel
    type: str = "BuildNode"

    _ignored_keys = {
        "offline",  # : bool
        "absoluteRemotePath",  # : None | str = None
        "actions",  # : None | Sequence[dict[str, Any]] = None
        "assignedLabels",  # : None | Sequence[dict[str, Any]] = None
        "description",  # : None | str = None
        "executors",  # : None | Sequence[dict[str, Any]] = None
        "iconClassName",  # : None | str = None
        "icon",  # : None | str = None
        "idle",  # : None | bool = None
        "jnlpAgent",  # : None | bool = None
        "launchSupported",  # : None | bool = None
        "loadStatistics",  # : None | dict[str, Any] = None
        "manualLaunchAllowed",  # : None | bool = None
        "monitorData",  # : None | dict[str, Any] = None
        "numExecutors",  # : None | int = None
        "offlineCause",  # : None | dict[str, Any] = None
        "offlineCauseReason",  # : None | str = None
        "oneOffExecutors",  # : None | Sequence[dict[str, Any]] = None
        "temporarilyOffline",  # : None | bool = None
    }

    @model_validator(mode="before")
    @classmethod
    def correct_node(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our expectations"""
        return {
            **obj,
            "name": obj.get("name") or obj.get("displayName"),
            "displayName": obj.get("name") or obj.get("displayName"),
        }


class StageInfo(PedanticBaseModel):
    """Historic information about a pipeline stage"""

    name: str
    begin: int
    duration: int
    execNode: str

    # StageInfo instances not always have a _class, but we want to derive PedanticBaseModel
    type: str = "StageInfo"

    # not the same as JobResult unfortunately
    status: Literal["FAILED", "IN_PROGRESS", "SUCCESS", "ABORTED", "NOT_EXECUTED", "UNSTABLE"]

    @model_validator(mode="before")
    @classmethod
    def correct_stage(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our expectations"""
        return {
            "name": obj["name"],
            "begin": obj["startTimeMillis"] // 1000,
            "duration": obj["durationMillis"] // 1000,
            "execNode": obj["execNode"],
            "status": obj["status"],
        }


class BuildStages(PedanticBaseModel):
    """Information about build stages"""

    stages: Sequence[StageInfo]
    begin: int
    duration: int
    end: int
    id: str
    name: str
    status: str

    # StageInfo instances not always have a _class, but we want to derive PedanticBaseModel
    type: Literal["undefined"] = "undefined"

    # ignore: pauseDurationMillis, queueDurationMillis, _links

    @model_validator(mode="before")
    @classmethod
    def correct_buildstages(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our expectations"""
        return {
            "id": obj["id"],
            "name": obj["name"],
            "begin": obj["startTimeMillis"] // 1000,
            "duration": obj["durationMillis"] // 1000,
            "end": obj["endTimeMillis"] // 1000,
            "status": obj["status"],
            "stages": obj["stages"],
        }


class Change(PedanticBaseModel):
    """Infos about git change"""

    id: str
    author: str
    message: str
    author_email: str
    url: str
    affected: Sequence[str] = []
    type: str = "Change"

    def markdown(self) -> str:
        """Returns a nice looking rich.Text representation"""
        return f"[{self.id[:12]}]({self.url}) - {self.author} - `{self.message}`"

    @model_validator(mode="before")
    @classmethod
    def correct_change(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our expectations"""
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
            else:
                return cast(GenMap, action[item_name])
    return {}


class Task(PedanticBaseModel):
    url: None | str = None

    _ignored_keys = {
        "actions",
        "buildable",
        "builds",
        "color",
        "concurrentBuild",
        "description",
        "disabled",
        "displayName",
        "displayNameOrNull",
        "firstBuild",
        "fullDisplayName",
        "fullName",
        "healthReport",
        "inQueue",
        "keepDependencies",
        "lastBuild",
        "lastCompletedBuild",
        "lastFailedBuild",
        "lastStableBuild",
        "lastSuccessfulBuild",
        "lastUnstableBuild",
        "lastUnsuccessfulBuild",
        "name",
        "nextBuildNumber",
        "property",
        "queueItem",
        "resumeBlocked",
    }


class Executable(PedanticBaseModel):
    """An 'executable' element of a QueueItem"""

    number: int
    url: str

    _ignored_keys = {
        "actions",
        "artifacts",
        "building",
        "changeSets",
        "culprits",
        "description",
        "displayName",
        "duration",
        "estimatedDuration",
        "executor",
        "fullDisplayName",
        "id",
        "inProgress",
        "keepLog",
        "nextBuild",
        "previousBuild",
        "queueId",
        "result",
        "timestamp",
    }


class QueueItem(PedanticBaseModel):
    """An item on the Jenkins build queue
    https://javadoc.jenkins-ci.org/hudson/model/Queue.Item.html
    """

    id: int
    blocked: bool
    buildable: bool
    cancelled: None | bool = None
    executable: None | Executable = None
    inQueueSince: datetime
    parameters: Mapping[str, str | bool]
    pending: None | bool = None
    stuck: bool
    task: Task
    why: None | str = None

    _ignored_keys = {
        "actions",  # might have parameters
        "buildableStartMilliseconds",
        "params",
        "timestamp",
        "url",  # queue/item/<id>/
    }

    @model_validator(mode="before")
    @classmethod
    def correct_queueitem(cls, obj: Json[dict[str, Any]]) -> Json[dict[str, Any]]:
        """Refactor init to match our expectations"""
        return {
            **obj,
            "parameters": params_from(
                build_info=obj, action_name="ParametersAction", item_name="parameters"
            ),
        }


def apply_common_jenkins_cli_args(parser: ArgumentParser) -> None:
    """Decorates given @parser with arguments for credentials and timeout"""
    parser.add_argument(
        "-c",
        "--credentials",
        type=split_params,
        help=(
            "Provide 'url', 'username' and 'password' "
            "or 'username_env', 'url_env' and 'password_env' respectively."
            "Optionally 'influxdb_url', 'influxdb_password' and 'influxdb_port' "
            "or 'influxdb_url_env', 'influxdb_password_env' and 'influxdb_port_env' if '--influxdb' is used"
            " If no credentials are provided, the JJB config at "
            " ~/.config/jenkins_jobs/jenkins_jobs.ini is being used."
        ),
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="Timeout in seconds for Jenkins API requests"
    )


def filter_by_prefix(
    dictionary: MutableMapping[str, str], unallowed_prefixes: list[str], strip_prefix: str
) -> Mapping[str, str]:
    """Return a new dictionary containing only keys without their prefix that do not start with any of the given prefixes."""
    return {
        key.replace(strip_prefix, ""): value
        for key, value in dictionary.items()
        if not any(key.startswith(prefix) for prefix in unallowed_prefixes)
    }


def extract_credentials(
    credentials: None | Mapping[str, str] = None,
    credentials_file: str = "~/.config/jenkins_jobs/jenkins_jobs.ini",
    config_section: str = "jenkins",
) -> Mapping[str, str]:
    """Turns the information provided via --credentials into actual values"""
    extracted_creds: MutableMapping[str, str] = {}
    section_settings: Mapping[str, Mapping[str, tuple[str, ...]]] = {
        "jenkins": {
            "required_keys": ("url", "username", "password"),
        },
        "influxdb": {
            "required_keys": ("url", "password"),
        },
        "influxdb_testing": {
            "required_keys": ("url", "password"),
        },
    }

    if credentials:
        creds_keys = [key.removesuffix("_env") for key in credentials.keys()]
        try:
            for key in creds_keys:
                extracted_creds[key] = (
                    credentials.get(key) or os.environ[credentials.get(f"{key}_env", "")]
                )
        except KeyError as exc:
            raise Fatal(f"Requested environment variable {exc} is not defined") from exc

        log().debug(f"pure extracted_creds: {extracted_creds}")
        # Ensure all keys required to interact with a remote service are extracted
        if all(key in extracted_creds for key in section_settings[config_section]["required_keys"]):
            # AugmentedJenkinsClient only accepts a specific set of keywords
            unallowed_prefixes = [f"{x}_" for x in section_settings.keys() if x != config_section]
            if config_section != "jenkins":
                # remove all keys defined for jenkins. This is a fix for not prefixing them initially while this tool was created
                unallowed_prefixes += list(section_settings["jenkins"]["required_keys"])
            return filter_by_prefix(
                dictionary=extracted_creds,
                unallowed_prefixes=unallowed_prefixes,
                strip_prefix=f"{config_section}_",
            )
        else:
            log().error("Not all required keys have been loaded from env")

    log().debug(
        "Credentials haven't been (fully) provided via --credentials, trying JJB config instead"
    )
    loaded_config = ConfigParser()
    loaded_config.read(Path(credentials_file).expanduser())

    # forget whatever you extracted from env variables
    extracted_creds.clear()
    extracted_creds = {
        "url": loaded_config[config_section]["url"],
        "password": loaded_config[config_section]["password"],
        # special handling for a may configured InfluxDB port
        **(
            {"port": loaded_config[config_section]["port"]}
            if "port" in loaded_config[config_section]
            else {}
        ),
        # very special handling as the Jenkins user is called "user" in the config file, but the AugmentedJenkinsClient expects "username"
        **(
            {"username": loaded_config[config_section]["user"]}
            if (config_section == "jenkins" and "user" in loaded_config[config_section])
            else {}
        ),
    }

    if not all(key in extracted_creds for key in section_settings[config_section]["required_keys"]):
        raise Fatal("Not all required keys could be loaded. Neither from env nor from file")

    return extracted_creds


class AugmentedJenkinsClient:
    """Provides typed interface to a JenkinsClient instance"""

    def __init__(self, url: str, username: str, password: str, timeout: int | None = None) -> None:
        """Create a Jenkins client interface using the config file used for JJB"""
        self.client = Jenkins(
            url=url,
            username=username,
            password=password,
            timeout=timeout if timeout is not None else 60,
            retries=5,
        )

    def __enter__(self) -> "AugmentedJenkinsClient":
        """Checks connection by validating sync_whoami()"""
        return self

    def __exit__(self, *args: object) -> None:
        pass

    async def __aenter__(self) -> "AugmentedJenkinsClient":
        """Checks connection by validating sync_whoami()"""
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    @asyncify
    def whoami(self) -> Mapping[str, str]:
        """Async wrapper for whoami"""
        return self.sync_whoami()

    def sync_whoami(self) -> Mapping[str, str]:
        """Synchronous wrapper for whoami"""
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
                elif jtype in {"WorkflowJob", "FreeStyleProject", "MatrixProject"}:
                    yield node_path, SimpleJob.model_validate(raw_job)
                else:
                    log().error("unknown job type %r", jtype)

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
        except JenkinsException as exc:
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

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def raw_jobs(self) -> GenMap:
        """Async wrapper for get_jobs()"""
        return self.client.get_jobs()

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def raw_job_info(self, job_full_name: str) -> GenMap:
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

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def raw_build_info(self, job_full_name: str, build_number: int) -> GenMap:
        """Returns raw Jenkins job info for @job_full_name"""
        log().debug("fetch build info for %s:%d", job_full_name, build_number)
        return self.client.get_build_info(job_full_name, build_number)

    async def build_info(self, job_full_name: str | Sequence[str], build_number: int) -> Build:
        """Fetches Jenkins build info for @job_full_name#@build_number"""
        return Build.model_validate(
            await self.raw_build_info(
                job_full_name if isinstance(job_full_name, str) else "/".join(job_full_name),
                build_number,
            )
        )

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def queue_info(self) -> Sequence[QueueItem]:
        """Async wrapper for get_queue_info()"""
        return list(map(QueueItem.model_validate, self.client.get_queue_info()))

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def queue_item(self, number: int, depth: int = 1) -> QueueItem:
        """Async wrapper for get_queue_item()"""
        return QueueItem.model_validate(self.client.get_queue_item(number, depth=depth))

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def build_stages(self, job: str | Sequence[str] | Job, build_number: int) -> BuildStages:
        """Returns validated build stages info"""
        return BuildStages.model_validate(
            self.client.get_build_stages(
                (
                    job
                    if isinstance(job, str)
                    else job.path
                    if isinstance(job, Job)
                    else "/".join(job)
                ),
                build_number,
            )
        )

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def build_console_output(self, job: str | Sequence[str] | Job, build_number: int) -> str:
        """Returns the build log for a given build"""
        return str(
            self.client.get_build_console_output(
                (
                    job
                    if isinstance(job, str)
                    else job.path
                    if isinstance(job, Job)
                    else "/".join(job)
                ),
                build_number,
            )
        )

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def fetch_jvm_ressource_stats(self) -> Mapping[str, int]:
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

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def running_builds(self) -> Sequence[SimpleBuild]:
        """Async validating wrapper for Jenkins.get_running_builds()"""
        return list(map(SimpleBuild.model_validate, self.client.get_running_builds()))

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def build_nodes(self) -> Sequence[BuildNode]:
        """Async validating wrapper for Jenkins.get_nodes()"""
        return list(map(BuildNode.model_validate, self.client.get_nodes()))

    @async_retry(tries=MAX_ATTEMPTS, delay=1, logger=log())
    async def node_info(self, name: str) -> BuildNode:
        """Async validating wrapper for Jenkins.get_node_info()"""
        return BuildNode.model_validate(self.client.get_node_info(name))

    async def stages(self, job: str | Sequence[str] | Job) -> Mapping[int, Sequence[StageInfo]]:
        """Fetch stage information about recently executed builds"""
        ## pylint: disable=protected-access
        job_info = (
            job
            if isinstance(job, Job)
            else await self.job_info(job if isinstance(job, str) else "/".join(job))
        )
        log().debug("fetch stage information for %s", job_info.path)
        run_info = self.client._session.get(f"{job_info.url}/wfapi/runs").json()
        return {
            int(run["id"]): [StageInfo.model_validate(stage) for stage in run["stages"]]
            for run in run_info
        }


async def main() -> None:  # pylint: disable=too-many-locals
    """Just a non-invasive test function"""
    logging.basicConfig(level=logging.WARNING)
    log().setLevel(logging.DEBUG)

    async with AugmentedJenkinsClient(**extract_credentials(), timeout=60) as jenkins_client:
        for build in await jenkins_client.running_builds():
            print(build)

        for build_node in await jenkins_client.build_nodes():
            print(build_node)
            info = await jenkins_client.node_info(build_node.name)
            assert info.name == build_node.name
            print(info)

        async for job_path, job in jenkins_client.traverse_job_tree("checkmk"):
            if job.type == "Folder":
                continue

            assert isinstance(job, SimpleJob)  # can only be a SimpleJob now..
            job_info = await jenkins_client.job_info(job_path)

            status = job_info.color.split("_")[0]

            # if status in {"disabled", "notbuilt", "blue"}:
            #    continue

            job_stages = await jenkins_client.stages(job_path)
            print(job_stages)

            assert status in {
                "red",
                "yellow",
                "disabled",
                "notbuilt",
                "blue",
                "aborted",
            }, job_info.color

            print(f"{job_info}, url={job_info.url}")
            (
                last_successful,
                first_failing,
                last_build,
            ) = await jenkins_client.failing_transition_numbers(job_info)
            assert bool(first_failing) is not bool(last_successful == last_build) or last_build == 1
            if first_failing:
                print(last_successful, first_failing, last_build)
                change_set = await jenkins_client.change_sets(job_info, first_failing)
                print(change_set)
                build_stages = await jenkins_client.build_stages(job_info, first_failing)
                print(build_stages)

            await job_info.expand(jenkins_client)
            for build_nr, build_info in job_info.build_infos.items():
                assert build_nr == build_info.number
                print(f"  {build_info}, url={build_info.url}")


if __name__ == "__main__":
    asyncio.run(main())
