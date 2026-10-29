#!/usr/bin/env python3

from typing import Any

import pytest

from cmk_dev.jenkins_utils import Build


def build(**kwargs: Any) -> Build:
    return Build(
        type="WorkflowRun",
        url="https://ci.example.com/job/some-job/1/",
        number=1,
        timestamp=0,
        duration=0,
        path_hashes={},
        artifacts=[],
        parameters={},
        causes=[],
        **kwargs,
    )


@pytest.mark.parametrize(
    "in_progress, result, expected",
    [
        pytest.param(True, None, False, id="running"),
        pytest.param(False, None, False, id="left-queue-but-not-started"),
        pytest.param(False, "SUCCESS", True, id="succeeded"),
        pytest.param(False, "FAILURE", True, id="failed"),
    ],
)
def test_build_completed(in_progress: bool, result: str | None, expected: bool) -> None:
    assert build(inProgress=in_progress, result=result).completed is expected
