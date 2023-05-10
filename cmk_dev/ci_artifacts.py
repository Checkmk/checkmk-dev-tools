#!/usr/bin/env python3

"""Give information about and download named arguments from Nexus
"""

import hashlib
import logging
import os
import sys
import time
from argparse import ArgumentParser
from argparse import Namespace as Args
from collections.abc import Mapping
from configparser import ConfigParser
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from jenkins import Jenkins, JenkinsException

# pylint: disable=too-many-instance-attributes


def parse_args() -> Args:
    """Cool git like multi command argument parser"""
    parser = ArgumentParser("Provide CI artifacts locally")
    parser.add_argument(
        "--log-level",
        "-l",
        choices=["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"],
        type=str.upper,
        default="INFO",
    )

    parser.set_defaults(func=lambda *_: parser.print_usage())
    subparsers = parser.add_subparsers(help="available commands", metavar="CMD")

    parser_info = subparsers.add_parser("info")
    parser_info.set_defaults(func=fn_info)
    parser_info.add_argument("job", type=lambda a: a.strip(" /"))

    parser_fetch = subparsers.add_parser("fetch")
    parser_fetch.set_defaults(func=fn_fetch)
    parser_fetch.add_argument("job", type=str)
    parser_fetch.add_argument(
        "-c",
        "--credentials",
        type=split_params,
        help=(
            "provide 'url', 'username' and 'password' "
            "or 'username_env', 'url_env' and 'password_env' respectively."
            " If no credentials are provided, the JJB config at "
            " ~/.config/jenkins_jobs/jenkins_jobs.ini is being used."
        ),
    )
    parser_fetch.add_argument("-p", "--params", type=split_params, action="append")
    parser_fetch.add_argument(
        "-t",
        "--time-constraints",
        type=str,
        default="today",
    )
    parser_fetch.add_argument(
        "-o",
        "--out-dir",
        default="out",
        type=Path,
    )

    return parser.parse_args()


def split_params(string: str) -> dict[str, str]:
    """Splits a 'string packed map' like 'foo=23,bar=42 into a dict"""
    return {k: v for p in string.split(",") for k, v in (p.split("="),)}


def logger():
    """Convenience function retrieves 'our' logger"""
    return logging.getLogger("ci-artifacts")


@dataclass
class Build:
    """Models a Jenkins job build"""

    number: int
    timestamp: datetime
    result: str
    building: str
    artifacts: list[str]
    parameters: str
    in_progress: bool

    def __init__(self, raw_build_info):
        def params_from(build_info):
            """Return job parameters of provided @build_info as dict"""
            for action in build_info["actions"]:
                if action.get("_class") == "hudson.model.ParametersAction":
                    return {p["name"]: p["value"] for p in action["parameters"]}
            return {}

        self.number = raw_build_info["number"]
        self.timestamp = datetime.fromtimestamp(raw_build_info["timestamp"] // 1000)
        self.result = raw_build_info["result"]
        self.building = raw_build_info["building"]
        self.artifacts = [a["relativePath"] for a in raw_build_info["artifacts"]]
        self.parameters = params_from(raw_build_info)
        self.url = raw_build_info["url"]
        self.in_progress = raw_build_info["inProgress"]


@dataclass
class Job:
    """Models a Jenkins job"""

    name: str
    fullname: str
    builds: Mapping[int, Build]

    def __init__(self, raw_job_info, raw_build_infos):
        self.name = raw_job_info["name"]
        self.fullname = raw_job_info["fullName"]
        self.builds = {bi["id"]: Build(bi) for bi in raw_build_infos}


@dataclass
class Folder:
    """Models a Jenkins folder"""

    name: str
    fullname: str
    jobs: str

    def __init__(self, raw_job_info):
        self.name = raw_job_info["name"]
        self.fullname = raw_job_info["fullName"]
        self.jobs = [j["name"] for j in raw_job_info["jobs"]]


def extract_credentials(credentials: None | Mapping[str, str]) -> Mapping[str, str]:
    """Turns the information provided via --credentials into actual values"""
    if credentials and (
        any(key in credentials for key in ("url", "url_env"))
        and any(key in credentials for key in ("username", "username_env"))
        and any(key in credentials for key in ("password", "password_env"))
    ):
        return {
            "url": credentials.get("url") or os.environ[credentials.get("url_env")],
            "username": credentials.get("username") or os.environ[credentials.get("username_env")],
            "password": credentials.get("password") or os.environ[credentials.get("password_env")],
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
def jenkins_client(url: str, username: str, password: str, timeout: int = 20) -> Jenkins:
    """Create a Jenkins client interface using the config file used for JJB"""
    client = Jenkins(url=url, username=username, password=password, timeout=timeout)
    whoami = client.get_whoami()
    if not whoami["id"] == username:
        logger().warning("client.get_whoami() does not match jenkins_config['user']")

    yield client


def fn_info(args: Args) -> None:
    """Entry point for information about job artifacts"""
    with jenkins_client(**extract_credentials(args.credentials)) as client:
        class_name = (job_info := client.get_job_info(args.job))["_class"]
        if class_name == "com.cloudbees.hudson.plugins.folder.Folder":
            # print(yaml.dump(job_info))
            print(Folder(job_info))
        elif class_name == "org.jenkinsci.plugins.workflow.job.WorkflowJob":
            build_infos = [client.get_build_info(args.job, b["number"]) for b in job_info["builds"]]
            print(Job(job_info, build_infos))
        else:
            print(f"Don't know class type {class_name}", file=sys.stderr)
            raise SystemExit(-1)


def meets_constraints(build, params, time_constraints):
    """Checks if a set of requirements are met for a given build"""
    if build.parameters != params:
        logger().debug("don't match: %s != %s", build.parameters, params)
        return False

    if time_constraints == "today":
        if build.timestamp.date() != datetime.now().date():
            logger().debug(
                "time constraints not met: %s != %s", build.timestamp.date(), datetime.now().date()
            )
            return False
    else:
        raise RuntimeError(f"Don't understand {time_constraints}")

    return True


def md5from(filepath: Path) -> str | None:
    """Returns an MD5 sum from contents of file provided"""
    with suppress(FileNotFoundError):
        with open(filepath, "rb") as input_file:
            file_hash = hashlib.md5()
            while chunk := input_file.read(1 << 16):
                file_hash.update(chunk)
            return file_hash.hexdigest()
    return None


def download_artifacts(client: Jenkins, build_url: str, out_dir: Path) -> None:
    """Downloads all artifacts listed for given job/build to @out_dir"""
    # pylint: disable=protected-access
    out_dir.mkdir(parents=True, exist_ok=True)

    # https://bugs.launchpad.net/python-jenkins/+bug/1973243
    # https://bugs.launchpad.net/python-jenkins/+bug/2018576
    fingerprints = client._session.get(
        f"{build_url}api/json?tree=fingerprint[fileName,hash]"
    ).json()["fingerprint"]

    if not fingerprints:
        print("No artifacts")
        return

    for fingerprint in fingerprints:
        fp_filename, fp_hash = fingerprint["fileName"], fingerprint["hash"]
        logger().debug("Handle artifact: %s (md5: %s)", fp_filename, fp_hash)
        artifact_filename = out_dir / fp_filename
        local_hash = md5from(artifact_filename)

        if local_hash == fp_hash:
            logger().debug("File is already available locally: %s (md5: %s)", fp_filename, fp_hash)
            continue

        if local_hash and local_hash != fp_hash:
            logger().warning(
                "File exists locally but hashes differ: %s %s != %s",
                fp_filename,
                local_hash,
                fp_hash,
            )

        with client._session.get(f"{build_url}artifact/{fp_filename}", stream=True) as reply:
            logger().debug("Download: %s", fp_filename)
            reply.raise_for_status()
            with open(artifact_filename, "wb") as out_file:
                for chunk in reply.iter_content(chunk_size=1 << 16):
                    out_file.write(chunk)


def find_matching_build(job: Job, params: Mapping[str, str | int], time_constraints: str) -> Build:
    """Goes through a job's build items and returns the first one to match certain criteria or None
    if none is found"""
    for build_id, build in job.builds.items():
        if meets_constraints(build, params, time_constraints):
            print(
                f"Found matching build: {build_id}"
                f" {build.timestamp} {build.result} {build.parameters}"
            )
            for key, value in build.__dict__.items():
                print(f"  {key}: {value}")
            return build
    return None


def create_new(client, job_full_path, params: Mapping[str, str | int]) -> Build:
    """Starts a job specified by @job_full_path using @params and returns its build info object"""
    queue_id = client.build_job(job_full_path, params)
    while True:
        queue_item = client.get_queue_item(queue_id)
        print(yaml.dump(queue_item))
        if executable := queue_item.get("executable"):
            print(yaml.dump(executable))
            return Build(client.get_build_info(job_full_path, executable["number"]))
        time.sleep(1)


def fn_fetch(args: Args) -> None:
    """Entry point for fetching artifacts"""
    params = {k: v for p in (args.params or []) for k, v in p.items()}
    logger().debug("Parsed params: %s", params)
    fetch_job_artifacts(args.job, params, args.time_constraints, args.credentials, args.out_dir)


def fetch_job_artifacts(job_full_path, params, time_constraints, credentials, out_dir) -> None:
    """Returns artifacts of Jenkins job specified by @job_full_path matching @params and
    @time_constraints. If none of the existing builds match the conditions a new build will be
    issued. If the existing build has not finished yet it will be waited for."""
    with jenkins_client(**extract_credentials(credentials)) as client:
        if not (job_info := client.get_job_info(job_full_path))["_class"].endswith("WorkflowJob"):
            logger().error("'%s' is not a WorkflowJob", job_full_path)
            raise SystemExit(-1)
        job = Job(
            job_info,
            (client.get_build_info(job_full_path, b["number"]) for b in job_info["builds"]),
        )

        build_candidate = find_matching_build(job, params, time_constraints) or create_new(
            client, job_full_path, params
        )

        if build_candidate.in_progress or build_candidate.building:
            print(f"Waiting for job #{build_candidate.number} to finish..")
            while True:
                build_candidate = Build(
                    client.get_build_info(job_full_path, build_candidate.number)
                )
                if build_candidate.in_progress or build_candidate.building:
                    logger().debug(
                        "build.in_progress=%s build.building=%s",
                        build_candidate.in_progress,
                        build_candidate.building,
                    )
                    time.sleep(2)
                    continue
                break
            if not build_candidate.artifacts:
                raise RuntimeError("Job has no artifacts!")

        download_artifacts(client, build_candidate.url, out_dir)


def main() -> None:
    """Entry point for everything else"""
    try:
        args = parse_args()
        logging.basicConfig(
            format="%(levelname)s %(asctime)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            level=getattr(logging, args.log_level),
        )
        logger().debug("Parsed args: %s", args)
        args.func(args)
    except JenkinsException as exc:
        logger().error("%r", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
