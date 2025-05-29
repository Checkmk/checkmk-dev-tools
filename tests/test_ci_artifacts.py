#!/usr/bin/env python3

import json
from pathlib import Path
from subprocess import check_output
from typing import Any, Dict, List

CI_ARTIFACTS_COMMAND = "ci-artifacts"
LOG_COMMAND = ["--log-level", "info"]
VALIDATION_JOB = "python-packages/checkmk_dev_tools/cv/test-job-checkmk-dev-tools"

def current_commit(path: Path = Path(".")) -> str:
    return check_output(["git", "rev-parse", "HEAD"], text=True).strip("\n")

def jenkins_config_file_exists(path: Path = Path(".config/jenkins_jobs/jenkins_jobs.ini")) -> bool:
    return (Path.home() / path).exists()

def build_base_command(subcommand: str) -> List[str]:
    command = [CI_ARTIFACTS_COMMAND] + LOG_COMMAND
    if not jenkins_config_file_exists():
        command.extend(["--credentials", "url_env=JENKINS_URL,username_env=JENKINS_USERNAME,password_env=JENKINS_PASSWORD"])
    command.append(subcommand)
    command.append(VALIDATION_JOB)
    command.append(f"--params=CUSTOM_GIT_REF={current_commit()},CIPARAM_BISECT_COMMENT={subcommand}")

    return command

def parse_response(command: List[str]) -> Dict[str, Any]:
    command_output = check_output(command, text=True)
    response = json.loads(command_output.strip("\n"))
    assert isinstance(response, dict)
    return response

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
    if "triggered_build" in first_response.keys():
        first_expectation = {
            "triggered_build": {
                "path": VALIDATION_JOB,
                "number": expected_build_number,
                "url": f"https://ci.lan.tribe29.com/job/python-packages/job/checkmk_dev_tools/job/cv/job/test-job-checkmk-dev-tools/{expected_build_number}/",
                "params": {
                    "CIPARAM_BISECT_COMMENT": subcommand,
                    "CUSTOM_GIT_REF": current_commit(),
                }
            }
        }
        assert isinstance(first_response, dict)
        assert first_response.get("triggered_build", False)
        assert first_response.get("triggered_build", {}).keys() == first_expectation.get("triggered_build", {}).keys()

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
    assert second_response.get("existing", {}).keys() == second_expectation.get("existing", {}).keys()

def test_fetch() -> None:
    command = build_base_command(subcommand="fetch")
    response = parse_response(command=command)
    expectation = {
        "result": "SUCCESS",
        "artifacts": [
            "test-file.txt"
        ],
    }

    assert expectation == response
