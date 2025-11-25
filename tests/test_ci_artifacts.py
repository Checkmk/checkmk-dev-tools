#!/usr/bin/env python3

import json
import os
from pathlib import Path
from subprocess import check_output
from typing import Any, Dict, Generator, List
from unittest import mock

import pytest

from cmk_devops.jenkins_utils import extract_credentials, filter_by_prefix

CI_ARTIFACTS_COMMAND = "ci-artifacts"
LOG_COMMAND = ["--log-level", "debug"]
VALIDATION_JOB = "python-packages/checkmk_dev_tools/cv/test-job-checkmk-dev-tools"


def current_commit(path: Path = Path()) -> str:
    return check_output(["git", "rev-parse", "HEAD"], text=True).strip("\n")


def jenkins_config_file_exists(path: Path = Path(".config/jenkins_jobs/jenkins_jobs.ini")) -> bool:
    return (Path.home() / path).exists()


def build_base_command(subcommand: str, with_influxdb: bool = False) -> List[str]:
    command = [CI_ARTIFACTS_COMMAND, *LOG_COMMAND]
    if not jenkins_config_file_exists():
        command.extend(
            [
                "--credentials",
                f"url_env=JENKINS_URL,username_env=JENKINS_USERNAME,password_env=JENKINS_PASSWORD{',influxdb_url_env=INFLUX_URL,influxdb_password_env=INFLUXDB_READ_TOKEN' if with_influxdb else ''}",
            ]
        )
    command.append(subcommand)
    command.append(VALIDATION_JOB)
    command.append(
        f"--params=CUSTOM_GIT_REF={current_commit()},CIPARAM_BISECT_COMMENT={subcommand}"
    )
    if with_influxdb:
        command.append("--influxdb")

    return command


def parse_response(command: List[str]) -> Dict[str, Any]:
    command_output = check_output(command, text=True)
    response = json.loads(command_output.strip("\n"))
    assert isinstance(response, dict)
    return response


def test_filter_by_prefix() -> None:
    single_entry_dict = {
        "username": "jenkinsUser",
        "password": "jenkinsPassword",
        "url": "jenkins.url",
    }
    assert (
        filter_by_prefix(
            dictionary=single_entry_dict, unallowed_prefixes=["asdf_", "qwertz_"], strip_prefix=""
        )
        == single_entry_dict
    )

    other_prefix = "influxdb"
    other_entry_dict = {
        "password": "anotherPassword",
        "url": "http://build-vm",
        "port": "8086",
    }
    multi_entry_dict = {
        **single_entry_dict,
        **({f"{other_prefix}_{key}": value for key, value in other_entry_dict.items()}),
    }
    assert (
        filter_by_prefix(
            dictionary=multi_entry_dict, unallowed_prefixes=[f"{other_prefix}_"], strip_prefix=""
        )
        == single_entry_dict
    )

    # special test
    # The config section of Jenkins has no prefix like "jenkins_" at their keys like "user", "password", ...
    # as these entries are known they are added as unallowed prefixes, in the end they match by 100%
    assert (
        filter_by_prefix(
            dictionary=multi_entry_dict,
            unallowed_prefixes=[f"{x}" for x in single_entry_dict],
            strip_prefix=f"{other_prefix}_",
        )
        == other_entry_dict
    )


def test_extract_credentials_from_file() -> None:
    jenkins_jobs_file = Path(__file__).parent.resolve() / "jenkins_jobs.ini"
    assert jenkins_jobs_file.exists()

    jenkins_creds = {
        "username": "lord.ci",
        "password": "magicPasswordHere",
        "url": "https://ci.lan.tribe29.com",
    }
    influxdb_testing_creds = {
        "password": "anotherPassword",
        "url": "http://build-vm",
        "port": "8086",
    }
    influxdb_creds = {
        "password": "yetAnotherSecret",
        "url": "https://ci-influx.lan.checkmk.net",
    }

    assert extract_credentials(credentials_file=str(jenkins_jobs_file)) == jenkins_creds
    assert (
        extract_credentials(
            credentials_file=str(jenkins_jobs_file), config_section="influxdb_testing"
        )
        == influxdb_testing_creds
    )
    assert (
        extract_credentials(credentials_file=str(jenkins_jobs_file), config_section="influxdb")
        == influxdb_creds
    )


@pytest.fixture(scope="function")
def mock_settings_env_vars(scope: str = "function") -> Generator[Dict[str, Any], None, None]:
    env_dict = {
        "JENKINS_URL": "jenkins.url",
        "JENKINS_USERNAME": "jenkinsUser",
        "JENKINS_PASSWORD": "jenkinsPassword",
        "INFLUX_URL": "influx.url",
        "INFLUX_PASSWORD": "influxPassword",
    }
    with mock.patch.dict(os.environ, env_dict) as patched_env:
        yield patched_env


def test_extract_credentials_from_env(
    mock_settings_env_vars: Generator[Dict[str, Any], None, None],
) -> None:
    credential_args = {
        "url_env": "JENKINS_URL",
        "username_env": "JENKINS_USERNAME",
        "password_env": "JENKINS_PASSWORD",
        "influxdb_url_env": "INFLUX_URL",
        "influxdb_password_env": "INFLUX_PASSWORD",
    }

    jenkins_creds = {
        "username": "jenkinsUser",
        "password": "jenkinsPassword",
        "url": "jenkins.url",
    }
    influxdb_creds = {
        "password": "influxPassword",
        "url": "influx.url",
    }

    assert extract_credentials(credentials=credential_args) == jenkins_creds
    assert (
        extract_credentials(credentials=credential_args, config_section="influxdb")
        == influxdb_creds
    )


def test_validate() -> None:
    command = build_base_command(subcommand="validate")
    response = parse_response(command=command)

    expectation = {
        "result": "SUCCESS",
        "artifacts": [],
    }

    assert expectation == response


def test_request() -> None:
    subcommand = "request"
    # not known and not checked here
    expected_build_number = 6
    command = build_base_command(subcommand=subcommand)

    first_response = parse_response(command=command)
    # on the first run there should but might not be a build
    # conditionally check first response, build might already exist
    if "triggered_build" in first_response:
        first_expectation = {
            "triggered_build": {
                "path": VALIDATION_JOB,
                "number": expected_build_number,
                "url": f"https://ci.lan.tribe29.com/job/python-packages/job/checkmk_dev_tools/job/cv/job/test-job-checkmk-dev-tools/{expected_build_number}/",
                "params": {
                    "CIPARAM_BISECT_COMMENT": subcommand,
                    "CUSTOM_GIT_REF": current_commit(),
                },
            }
        }
        assert isinstance(first_response, dict)
        assert first_response.get("triggered_build", False)
        assert (
            first_response.get("triggered_build", {}).keys()
            == first_expectation.get("triggered_build", {}).keys()
        )

    second_expectation = {
        "existing": {
            "path": VALIDATION_JOB,
            "number": expected_build_number,
            "url": f"https://ci.lan.tribe29.com/job/python-packages/job/checkmk_dev_tools/job/cv/job/test-job-checkmk-dev-tools/{expected_build_number}/",
            "result": "SUCCESS",
            "new_build": False,
        }
    }
    second_response = parse_response(command=command)

    assert isinstance(second_response, dict)
    assert second_response.get("existing", False)
    assert (
        second_response.get("existing", {}).keys() == second_expectation.get("existing", {}).keys()
    )

    command = build_base_command(subcommand=subcommand, with_influxdb=True)
    influxdb_response = parse_response(command=command)

    assert isinstance(influxdb_response, dict)
    assert influxdb_response.get("existing", False)
    assert (
        influxdb_response.get("existing", {}).keys()
        == second_expectation.get("existing", {}).keys()
    )


def test_fetch() -> None:
    command = build_base_command(subcommand="fetch")
    response = parse_response(command=command)
    expectation = {
        "result": "SUCCESS",
        "artifacts": ["test-file.txt"],
    }

    assert expectation == response
