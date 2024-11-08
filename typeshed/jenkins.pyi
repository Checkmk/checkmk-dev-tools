#!/usr/bin/env python3
from collections.abc import Mapping, Sequence
from typing import TypeAlias, Union

from requests import Response

GenMapVal: TypeAlias = Union[None, bool, str, float, int, "GenMapArray", "GenMap"]
GenMapArray: TypeAlias = Sequence[GenMapVal]
GenMap: TypeAlias = Mapping[str, GenMapVal]


class Session:
    def get(self, url: str, stream: bool = False) -> Response:
        ...


class Auth:
    username: bytes


class JenkinsException(BaseException):
    ...


class Jenkins:
    _session: Session
    auth: Auth

    def __init__(self, url: str, username: str, password: str, timeout: int) -> None:
        ...

    def get_whoami(self) -> dict[str, str]:
        ...

    def get_jobs(self) -> GenMap:
        ...

    def get_job_info(self, job_name: str) -> GenMap:
        ...

    def get_build_info(self, job_name: str, number: int) -> GenMap:
        ...

    def build_job(self, job_full_path: str, parameters: None | GenMap) -> int:
        ...

    def get_queue_item(self, queue_id: int) -> dict[str, dict[str, int]]:
        ...

    def get_queue_info(self) -> Sequence[GenMap]:
        ...

    def get_build_stages(self, job_name: str, number: int) -> Sequence[GenMap]:
        ...

    def get_running_builds(self) -> Sequence[GenMap]:
        ...

    def get_nodes(self) -> Sequence[GenMap]:
        ...

    def get_node_info(self, node: str) -> GenMap:
        ...

    def run_script(self, script: str) -> str:
        ...

    def stop_build(self, name: str, number: int) -> None:
        ...
