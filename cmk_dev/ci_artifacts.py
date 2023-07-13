#!/usr/bin/env python3

"""Provide information about CI artifacts and make them available locally"""

import hashlib
import logging
import os
import sys
import time
from argparse import ArgumentParser
from argparse import Namespace as Args
from configparser import ConfigParser
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from itertools import chain
from pathlib import Path
from subprocess import check_output
from typing import Iterator, Mapping, Sequence, Tuple, Union, cast

from jenkins import Jenkins

# pylint: disable=too-many-instance-attributes
# pylint: disable=fixme

GenMapVal = Union[None, bool, str, float, int, "GenMapArray", "GenMap"]
GenMapArray = Sequence[GenMapVal]
GenMap = Mapping[str, GenMapVal]

PathHashes = Mapping[str, str]
JobParamValue = Union[int, str]
JobParams = Mapping[str, JobParamValue]

QueueId = int
BuildId = int


class Fatal(RuntimeError):
    """Rien ne va plus"""


def parse_args() -> Args:
    """Cool git like multi command argument parser"""
    parser = ArgumentParser(__doc__)
    parser.add_argument(
        "--log-level",
        "-l",
        choices=["ALL_DEBUG", "DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"],
        help="Sets the logging level - ALL_DEBUG sets all other loggers to DEBUG, too",
        type=str.upper,
        default="INFO",
    )
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

    parser.set_defaults(func=lambda *_: parser.print_usage())
    subparsers = parser.add_subparsers(help="available commands", metavar="CMD")

    parser_info = subparsers.add_parser("info")
    parser_info.set_defaults(func=_fn_info)
    parser_info.add_argument(
        "job",
        type=lambda a: a.strip(" /"),
        help="Print some useful but informal information about a job",
    )

    parser_fetch = subparsers.add_parser("fetch")
    parser_fetch.set_defaults(func=_fn_fetch)
    parser_fetch.add_argument("job", type=str)
    parser_fetch.add_argument(
        "-p",
        "--params",
        type=split_params,
        action="append",
        help="Job parameters used to check existing builds and to start new jobs with",
    )
    parser_fetch.add_argument(
        "--params-no-check",
        type=split_params,
        action="append",
        help="Job parameters used to start new jobs with, but not used to check existing builds",
    )
    parser_fetch.add_argument(
        "-d",
        "--dependency-paths",
        type=str,
        action="append",
        help=(
            "Provide list of files/directories git hashes should be compared against a build"
            "Important: provide the same relative directories as used in the respective build jobs!"
        ),
    )
    parser_fetch.add_argument(
        "-t",
        "--time-constraints",
        type=str,
        help=(
            "Provide a string (currently only 'today') which specifies the max age of a"
            " build to be considered valid."
        ),
    )
    parser_fetch.add_argument(
        "-b",
        "--base-dir",
        type=lambda p: Path(p).expanduser(),
        help="The base directory used to fetch directory/file hashes (see. --dependency-paths)",
    )
    parser_fetch.add_argument(
        "-o",
        "--out-dir",
        default="out",
        type=Path,
        help="Directory to put artifacts to - relative to --base-dir if relative",
    )
    parser_fetch.add_argument(
        "-f",
        "--force-new-build",
        action="store_true",
        help="Don't check for existing matching builds, but start a new build immediately instead",
    )
    parser_fetch.add_argument(
        "-n",
        "--omit-new-build",
        action="store_true",
        help="Don't issue new builds, even if no matching build could be found",
    )

    return parser.parse_args()


def logger() -> logging.Logger:
    """Convenience function retrieves 'our' logger"""
    return logging.getLogger("ci-artifacts")


def split_params(string: str) -> Mapping[str, str]:
    """Splits a 'string packed map' into a dict
    >>> split_params("foo=23,bar=42")
    {'foo': '23', 'bar': '42'}
    """
    return {k: v for p in string.split(",") if p for k, v in (p.split("="),)}


def compact_dict(mapping: GenMap) -> str:
    """Splits a 'string packed map' into a dict
    >>> compact_dict({'foo': '23', 'bar': '42'})
    'foo=23, bar=42'
    """

    def short(string: str) -> str:
        return string if len(string) <= 12 else f"{string[:10]}.."

    return ", ".join(f"{k}={short_str}" for k, v in mapping.items() if (short_str := short(str(v))))


@dataclass
class Build:
    """Models a Jenkins job build"""

    url: str
    number: int
    timestamp: datetime
    result: Union[None, str]
    finished: bool
    parameters: GenMap
    path_hashes: Mapping[str, str]
    artifacts: Sequence[str]

    def __str__(self) -> str:
        return (
            f"Build({self.result}, {self.timestamp}, params={{{compact_dict(self.parameters)}}}, "
            f"hashes={{{compact_dict(self.path_hashes)}}})"
        )

    @staticmethod
    def from_build_info(build_info: GenMap) -> "Build":
        """Creates Build instance from Jenkins API reply structure"""
        result = cast(str, build_info["result"])
        in_progress = cast(bool, build_info["inProgress"])
        # TODO: what's the difference between .in_progress and .building?
        assert in_progress == cast(bool, build_info["building"])
        assert result in {None, "FAILURE", "SUCCESS"}
        # assert result in {None, "SUCCESS", "FAILURE"}

        assert result or in_progress

        return Build(
            url=cast(str, build_info["url"]),
            number=cast(int, build_info["number"]),
            timestamp=datetime.fromtimestamp(cast(int, build_info["timestamp"]) // 1000),
            result=result,
            finished=not in_progress,
            parameters=params_from(build_info, "ParametersAction", "parameters"),
            path_hashes=cast(
                Mapping[str, str],
                params_from(build_info, "CustomBuildPropertiesAction", "properties").get(
                    "path_hashes", {}
                ),
            ),
            artifacts=[
                cast(Mapping[str, str], a)["relativePath"]
                for a in cast(GenMapArray, build_info["artifacts"])
            ]
            # SCM could be retrieved via 'hudson.plugins.git.util.BuildData'
        )


@dataclass
class Job:
    """Models a Jenkins job"""

    name: str
    fullname: str
    builds: Mapping[int, Build]
    url: str

    def __str__(self) -> str:
        return f"Job('{self.fullname}', {len(self.builds)} builds)"

    def __init__(self, raw_job_info: GenMap, raw_build_infos: GenMapArray):
        assert not raw_job_info.get("queueItem") and not raw_job_info.get("inQueue")
        self.name = cast(str, raw_job_info["name"])
        self.fullname = cast(str, raw_job_info["fullName"])
        self.url = cast(str, raw_job_info["url"])
        self.builds = {
            cast(int, bi["id"]): Build.from_build_info(bi)
            for bi in map(lambda a: cast(GenMap, a), raw_build_infos)
        }


@dataclass
class Folder:
    """Models a Jenkins folder"""

    name: str
    fullname: str
    jobs: Sequence[str]

    def __init__(self, raw_job_info: GenMap):
        self.name = cast(str, raw_job_info["name"])
        self.fullname = cast(str, raw_job_info["fullName"])
        self.jobs = [cast(str, j["name"]) for j in cast(Sequence[GenMap], raw_job_info["jobs"])]


def extract_credentials(credentials: Union[None, Mapping[str, str]]) -> Mapping[str, str]:
    """Turns the information provided via --credentials into actual values"""
    if credentials and (
        any(key in credentials for key in ("url", "url_env"))
        and any(key in credentials for key in ("username", "username_env"))
        and any(key in credentials for key in ("password", "password_env"))
    ):
        return {
            "url": credentials.get("url") or os.environ[credentials.get("url_env", "")],
            "username": credentials.get("username")
            or os.environ[credentials.get("username_env", "")],
            "password": credentials.get("password")
            or os.environ[credentials.get("password_env", "")],
        }
    logger().debug(
        "Credentials haven't been (fully) provided via --credentials, trying JJB config instead"
    )
    jjb_config = ConfigParser()
    jjb_config.read(Path("~/.config/jenkins_jobs/jenkins_jobs.ini").expanduser())
    return {
        "url": jjb_config["jenkins"]["url"],
        "username": jjb_config["jenkins"]["user"],
        "password": jjb_config["jenkins"]["password"],
    }


@contextmanager
def jenkins_client(
    url: str, username: str, password: str, timeout: Union[None, int] = None
) -> Iterator[Jenkins]:
    """Create a Jenkins client interface using the config file used for JJB"""
    client = Jenkins(
        url=url,
        username=username,
        password=password,
        timeout=timeout if timeout is not None else 20,
    )
    whoami = client.get_whoami()
    if not whoami["id"] == username:
        logger().warning("client.get_whoami() does not match jenkins_config['user']")

    yield client


def _fn_info(args: Args) -> None:
    """Entry point for information about job artifacts"""
    credentials = extract_credentials(args.credentials)
    with jenkins_client(
        credentials["url"], credentials["username"], credentials["password"]
    ) as client:
        class_name = (job_info := client.get_job_info(args.job))["_class"]
        if class_name == "com.cloudbees.hudson.plugins.folder.Folder":
            print(Folder(job_info))
        elif class_name == "org.jenkinsci.plugins.workflow.job.WorkflowJob":
            job = Job(
                job_info,
                [
                    client.get_build_info(args.job, cast(int, cast(GenMap, b)["number"]))
                    for b in cast(GenMapArray, job_info["builds"])
                ],
            )
            print(job)
            for build_nr, build in job.builds.items():
                print(f"  - {build_nr}: {build}")
        else:
            raise Fatal(f"Don't know class type {class_name}")


def md5from(filepath: Path) -> Union[str, None]:
    """Returns an MD5 sum from contents of file provided"""
    with suppress(FileNotFoundError):
        with open(filepath, "rb") as input_file:
            file_hash = hashlib.md5()
            while chunk := input_file.read(1 << 16):
                file_hash.update(chunk)
            return file_hash.hexdigest()
    return None


@contextmanager
def cwd(path: Path) -> Iterator[None]:
    """Changes working directory and returns to previous on exit."""
    prev_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev_cwd)


def git_commit_id(git_dir: Path, path: Union[None, Path, str] = None) -> str:
    """Returns the git hash of combination of given paths. First one must be a directory, the
    second one is then considered relative"""
    assert git_dir.is_dir()
    if path and not (git_dir / path).exists():
        raise Fatal(f"There is no path to '{path}' inside '{git_dir}'")
    with cwd(git_dir):
        return check_output(
            # use the full hash - short hashes cannot be checked out and they are not
            # unique among machines
            ["git", "log", "--pretty=tformat:%H", "-n1"] + ([str(path)] if path else []),
            text=True,
        ).strip("\n")


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


def download_artifacts(
    client: Jenkins, build: Build, out_dir: Path
) -> Tuple[Sequence[Path], Sequence[Path]]:
    """Downloads all artifacts listed for given job/build to @out_dir"""
    # pylint: disable=protected-access

    downloaded_artifacts, skipped_artifacts = [], []

    # https://bugs.launchpad.net/python-jenkins/+bug/1973243
    # https://bugs.launchpad.net/python-jenkins/+bug/2018576
    fingerprints = {
        fingerprint["fileName"]: fingerprint["hash"]
        for fingerprint in client._session.get(
            f"{build.url}api/json?tree=fingerprint[fileName,hash]"
        ).json()["fingerprint"]
    }

    assert len(fingerprints) == len(build.artifacts)

    if not fingerprints:
        raise Fatal(f"no (fingerprinted) artifacts found at {build.url}")

    for artifact in build.artifacts:
        fp_hash = fingerprints[artifact.split("/")[-1]]
        logger().debug("handle artifact: %s (md5: %s)", artifact, fp_hash)
        artifact_filename = out_dir / artifact
        local_hash = md5from(artifact_filename)

        if local_hash == fp_hash:
            logger().debug("file is already available locally: %s (md5: %s)", artifact, fp_hash)
            skipped_artifacts.append(artifact_filename)
            continue

        if local_hash and local_hash != fp_hash:
            logger().warning(
                "file exists locally but hashes differ: %s %s != %s",
                artifact,
                local_hash,
                fp_hash,
            )

        with client._session.get(f"{build.url}artifact/{artifact}", stream=True) as reply:
            logger().debug("download: %s", artifact)
            reply.raise_for_status()
            artifact_filename.parent.mkdir(parents=True, exist_ok=True)
            with open(artifact_filename, "wb") as out_file:
                for chunk in reply.iter_content(chunk_size=1 << 16):
                    out_file.write(chunk)
            downloaded_artifacts.append(artifact_filename)

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
) -> Sequence[Tuple[str, JobParamValue, JobParamValue]]:
    """Returns list of key and mismatching values in mapping @first which also occur in @second"""
    # TODO: find solution for unprovided parameters and default/empty values
    return [
        (key, cast(JobParamValue, first.get(key, "")), cast(JobParamValue, second.get(key, "")))
        for key in set(first.keys() | second.keys()) - {"DISABLE_CACHE"}
        if first.get(key) and first.get(key, "") != second.get(key, "")
    ]


def meets_constraints(
    build: Build,
    params: Union[None, JobParams],
    time_constraints: Union[None, str],
    path_hashes: PathHashes,
    *,
    now: datetime = datetime.now(),
) -> bool:
    """Checks if a set of requirements are met for a given build"""

    # TODO: discuss: should only the last job be taken into account?

    result = True

    # Prune if the build already failed (might still be ongoing)
    if build.result not in {None, "SUCCESS"}:
        logger().debug("build #%s result was: %s", build.number, build.result)
        return False

    if mismatching_parameters := find_mismatching_parameters(params or {}, build.parameters):
        logger().debug(
            "build #%s has mismatching parameters: %s", build.number, mismatching_parameters
        )
        result = False

    expected_path_hashes = split_params(str(build.parameters.get("DEPENDENCY_PATH_HASHES") or ""))
    if expected_path_hashes and not path_hashes:
        logger().warning(
            "strange: build #%s has expected path hashes set but we don't care?", build.number
        )

    if build.finished:
        if expected_path_hashes and not build.path_hashes:
            logger().warning(
                "strange: build #%s has expected path hashes but didn't store the actual ones!",
                build.number,
            )
        if bool(path_hashes) != bool(build.path_hashes):
            logger().warning(
                "strage: build #%s path hashes provided: %s but we expected them: %s",
                build.number,
                bool(build.path_hashes),
                bool(path_hashes),
            )

        if not path_hashes_match(build.path_hashes, path_hashes):
            logger().debug(
                "build #%s has mismatching path hashes: %s != %s",
                build.number,
                build.path_hashes,
                path_hashes,
            )
            result = False
    else:
        if not path_hashes_match(expected_path_hashes, path_hashes):
            logger().debug(
                "build #%s has been started with mismatching expected path hashes: %s != %s",
                build.number,
                expected_path_hashes,
                path_hashes,
            )
            result = False

    if time_constraints is None or time_constraints == "today":
        if build.timestamp.date() != datetime.now().date():
            logger().debug(
                "build #%s does not meet time constraints: %s != %s",
                build.number,
                build.timestamp.date(),
                now.date(),
            )
            if result:
                logger().warning(
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
    logger().info("waiting for queue item %s to be scheduled", queue_id)
    while True:
        queue_item = client.get_queue_item(queue_id)
        if executable := queue_item.get("executable"):
            return executable["number"]
        logger().debug("still waiting in queue, because %s", queue_item["why"])
        time.sleep(1)


def find_matching_queue_item(
    client: Jenkins,
    job: Job,
    params: Union[None, JobParams],
    path_hashes: PathHashes,
) -> Union[BuildId, None]:
    """Looks for a queued build matching job and parameters and returns the QueueId"""
    for queue_item in client.get_queue_info():
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
            logger().debug(
                "queue item %s has mismatching parameters: %s",
                queue_item.get("id"),
                mismatching_parameters,
            )
            continue
        expected_path_hashes = split_params(
            str(queue_item_params.get("DEPENDENCY_PATH_HASHES") or "")
        )
        if expected_path_hashes and not path_hashes:
            logger().warning(
                "strange: queued item %s has expected path hashes set but we don't care?",
                queue_item.get("id"),
            )
        if not path_hashes_match(expected_path_hashes, path_hashes):
            logger().debug(
                "queued item %s has mismatching expected path hashes: %s != %s",
                queue_item.get("id"),
                expected_path_hashes,
                path_hashes,
            )
            continue
        return build_id_from_queue_item(client, cast(int, queue_item.get("id")))

    return None


def _fn_fetch(args: Args) -> None:
    """Entry point for fetching artifacts"""
    # logger().debug("Parsed params: %s", params)
    fetch_job_artifacts(
        args.job,
        credentials=args.credentials,
        params=args.params and {k: v for p in (args.params) for k, v in p.items()},
        params_no_check=(
            args.params_no_check and {k: v for p in (args.params_no_check) for k, v in p.items()}
        ),
        dependency_paths=(
            list(filter(bool, args.dependency_paths or []))
            and [path for paths in (args.dependency_paths) for path in paths.split(",")]
        ),
        base_dir=args.base_dir,
        time_constraints=args.time_constraints,
        out_dir=args.out_dir,
        omit_new_build=args.omit_new_build,
        force_new_build=args.force_new_build,
    )


def fetch_job_artifacts(
    job_full_path: str,
    *,
    credentials: Union[None, Mapping[str, str]] = None,
    params: Union[None, JobParams] = None,
    params_no_check: Union[None, JobParams] = None,
    dependency_paths: Union[None, Sequence[str]] = None,
    base_dir: Union[None, Path] = None,
    time_constraints: Union[None, str] = None,
    out_dir: Union[None, Path] = None,
    omit_new_build: bool = False,
    force_new_build: bool = False,
) -> Sequence[Path]:
    """Returns artifacts of Jenkins job specified by @job_full_path matching @params and
    @time_constraints. If none of the existing builds match the conditions a new build will be
    issued. If the existing build has not finished yet it will be waited for."""
    # pylint: disable=too-many-locals

    used_base_dir = base_dir or Path(".")
    creds = extract_credentials(credentials)
    with jenkins_client(creds["url"], creds["username"], creds["password"]) as client:
        if not str((job_info := client.get_job_info(job_full_path))["_class"]).endswith(
            "WorkflowJob"
        ):
            raise Fatal(f"{job_full_path} is not a WorkflowJob")

        def elect_build_candidate() -> Build:
            """Find an existing build (finished, still running or queued) which matches our
            requirements. This can get complicated since we don't know the outcome of unfinished or
            queued elements yet (result and dependency path hashes).
            """
            # In case we force a new build anyway we don't have to look for an existing one
            if not force_new_build:
                # fetch a job's build history first
                job = Job(
                    job_info,
                    [
                        client.get_build_info(job_full_path, cast(int, cast(GenMap, b)["number"]))
                        for b in cast(GenMapArray, job_info["builds"])
                    ],
                )

                # Look for finished builds
                for build in filter(lambda b: b.finished, job.builds.values()):
                    if meets_constraints(build, params, time_constraints, path_hashes):
                        logger().info(
                            "found matching finished build: %s (%s)", build.number, build.url
                        )
                        return build

                # Look for still unfinished builds
                for build in filter(lambda b: not b.finished, job.builds.values()):
                    if meets_constraints(build, params, time_constraints, path_hashes):
                        logger().info(
                            "found matching unfinished build: %s (%s)", build.number, build.url
                        )
                        return build

                if matching_queue_item := find_matching_queue_item(
                    client, job, params, path_hashes
                ):
                    return Build.from_build_info(
                        client.get_build_info(job_full_path, matching_queue_item)
                    )

            if omit_new_build:
                raise Fatal(
                    f"No matching build found for job '{job.name}' but new builds are omitted."
                )

            parameters = {
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

            logger().info("start new build for %s", job_full_path)
            logger().info("  params=%s", parameters)

            return Build.from_build_info(
                client.get_build_info(
                    job_full_path,
                    build_id_from_queue_item(
                        client,
                        client.build_job(
                            job_full_path,
                            parameters=parameters,
                        ),
                    ),
                )
            )

        path_hashes = {
            path: git_commit_id(used_base_dir, path) for path in (dependency_paths or [])
        }

        build_candidate = elect_build_candidate()
        for key, value in build_candidate.__dict__.items():
            logger().debug("  %s: %s", key, value)

        if not build_candidate.finished:
            logger().info(
                "build #%s still in progress (%s)", build_candidate.number, build_candidate.url
            )
            while True:
                build_candidate = Build.from_build_info(
                    client.get_build_info(job_full_path, build_candidate.number)
                )
                if not build_candidate.finished:
                    logger().debug("build %s in progress", build_candidate.number)
                    time.sleep(10)
                    continue
                break

            if build_candidate.result != "SUCCESS":
                raise Fatal(
                    "The build we started has "
                    f"result={build_candidate.result} ({build_candidate.url})"
                )
            logger().info("build finished successfully")

        if not path_hashes_match(build_candidate.path_hashes, path_hashes):
            raise Fatal(
                f"most recent build #{build_candidate.number} has mismatching path hashes: "
                f"{build_candidate.path_hashes} != {path_hashes}"
            )

        if not build_candidate.artifacts:
            raise Fatal("Job has no artifacts!")

        full_out_dir = used_base_dir / (out_dir or "")
        downloaded_artifacts, skipped_artifacts = download_artifacts(
            client, build_candidate, full_out_dir
        )
        logger().info(
            "%d artifacts available in %s (%d skipped, because it existed already)",
            len(downloaded_artifacts) + len(skipped_artifacts),
            full_out_dir,
            len(skipped_artifacts),
        )

        return list(chain(downloaded_artifacts, skipped_artifacts))


def main() -> None:
    """Entry point for everything else"""
    try:
        args = parse_args()
        logging.basicConfig(
            format="%(name)s %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=logging.DEBUG if args.log_level == "ALL_DEBUG" else logging.WARNING,
        )
        logger().setLevel(getattr(logging, args.log_level.split("_")[-1]))
        logger().debug("Parsed args: %s", args)
        args.func(args)
    except Fatal as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(-1) from exc


if __name__ == "__main__":
    main()
