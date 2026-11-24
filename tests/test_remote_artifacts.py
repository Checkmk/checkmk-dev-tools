#!/usr/bin/env python3

# ruff: noqa: PLR2004

"""Unit tests for cmk_dev.remote_artifacts

All interaction with the `pulp` CLI (subprocess) and HTTP downloads (requests) is mocked,
no remote artifact storage is required to run these tests.
"""

import json
import subprocess
import time
from argparse import Namespace
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import requests

from cmk_dev import remote_artifacts as ra

# --------------------------------------------------------------------------------------
# helpers / fixtures
# --------------------------------------------------------------------------------------


def make_args(**kwargs: Any) -> Namespace:
    """Namespace with the common (top level) options set to their defaults"""
    defaults: dict[str, Any] = {
        "domain": "nobackup",
        "dry_run": False,
        "pretty": False,
        "print_response": False,
        "pulp_dry_run": False,
        "pulp_verbose": 0,
        "verbose": 0,
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


def ts(delta: timedelta) -> str:
    """Pulp formatted timestamp relative to now (negative delta = in the past)"""
    return (datetime.now(ra.BASE_TIME_ZONE) + delta).strftime(ra.TIMESTAMP_FORMAT)


class FakePulp:
    """Records pulp CLI calls and returns canned (JSON) responses based on the sub command"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: dict[tuple[str, ...], Any] = {}

    def set(self, subcommand: tuple[str, ...], response: Any) -> None:
        self.responses[subcommand] = response

    def __call__(self, cmd: list[str]) -> bytes:
        self.calls.append(cmd)
        # strip "pulp" and global options like --domain=..., --dry-run, -vv
        positional = tuple(c for c in cmd[1:] if not c.startswith("-"))
        for key in sorted(self.responses, key=len, reverse=True):
            if positional[: len(key)] == key:
                response = self.responses[key]
                return (response if isinstance(response, str) else json.dumps(response)).encode()
        return b""

    def calls_for(self, *subcommand: str) -> list[list[str]]:
        return [
            c
            for c in self.calls
            if tuple(x for x in c[1:] if not x.startswith("-"))[: len(subcommand)] == subcommand
        ]


@pytest.fixture
def pulp() -> Iterator[FakePulp]:
    fake = FakePulp()
    with mock.patch.object(subprocess, "check_output", side_effect=fake):
        yield fake


@pytest.fixture
def parse(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Namespace]:
    def _parse(*argv: str) -> Namespace:
        monkeypatch.setattr("sys.argv", ["ra", *argv])
        return ra.parse_args()

    return _parse


# --------------------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------------------


def test_parse_args_defaults(parse: Callable[..., Namespace]) -> None:
    args = parse("list", "--repository", "repo")
    assert args.func is ra._fn_list
    assert args.repository == "repo"
    assert args.domain == "nobackup"
    assert args.dry_run is False
    assert args.pulp_dry_run is False
    assert args.pulp_verbose == 0
    assert args.verbose == 0


def test_parse_args_verbosity_counts(parse: Callable[..., Namespace]) -> None:
    args = parse("-vv", "-ppp", "list", "--repository", "repo")
    assert args.verbose == 2
    assert args.pulp_verbose == 3


def test_parse_args_invalid_domain(parse: Callable[..., Namespace]) -> None:
    with pytest.raises(SystemExit):
        parse("--domain", "foo", "list", "--repository", "repo")


@pytest.mark.parametrize(
    "argv, func",
    [
        (
            ["download", "--repository", "r", "--label-key", "k", "--label-value", "v"],
            ra._fn_download,
        ),
        (["label", "--href", "/h/"], ra._fn_label),
        (["list", "--repository", "r"], ra._fn_list),
        (["query"], ra._fn_query),
        (["remove", "--repository", "r", "--remote-path", "a/b", "--sha256", "abc"], ra._fn_remove),
        (["show", "--href", "/h/"], ra._fn_show),
        (["upload", "--repository", "r", "--file", "f", "--remote-path", "a/f"], ra._fn_upload),
    ],
)
def test_parse_args_subcommand_dispatch(
    parse: Callable[..., Namespace], argv: list[str], func: Callable[..., Any]
) -> None:
    assert parse(*argv).func is func


def test_parse_args_subcommand_required(
    parse: Callable[..., Namespace], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        parse("--domain", "backup")
    assert "required: CMD" in capsys.readouterr().err


def test_parse_args_upload_defaults(parse: Callable[..., Namespace]) -> None:
    args = parse("upload", "--repository", "r", "--file", "f.txt", "--remote-path", "x/f.txt")
    assert args.file == Path("f.txt")
    assert args.remote_path == Path("x/f.txt")
    assert args.retention_duration == 7
    assert args.retention_unit == "days"
    assert args.label_key is None
    assert args.label_value is None


def test_parse_args_upload_sanitizes_labels(parse: Callable[..., Namespace]) -> None:
    args = parse(
        "upload", "--repository", "r", "--file", "f", "--remote-path", "f",
        "--label-key", "my-key.1", "--label-value", "v a!l~ue",
        "--label-key", "other", "--label-value", "2.4.0p1",
    )  # fmt: skip
    assert args.label_key == ["my_key_1", "other"]
    assert args.label_value == ["v_a_l_ue", "2_4_0p1"]


def test_parse_args_query_labels_keep_order_and_query_chars(
    parse: Callable[..., Namespace],
) -> None:
    args = parse(
        "query",
        "--label-key", "!old-key",
        "--label-key", "branch",
        "--label-value", "~ma.in",
        "--label-key", "ver",
        "--label-value", "!1.0",
    )  # fmt: skip
    # "!" allowed in keys, "!" and "~" allowed in values, everything else replaced
    assert args.labels == [
        ("--label-key", "!old_key"),
        ("--label-key", "branch"),
        ("--label-value", "~ma_in"),
        ("--label-key", "ver"),
        ("--label-value", "!1_0"),
    ]


def test_parse_args_query_key_does_not_allow_tilde(parse: Callable[..., Namespace]) -> None:
    args = parse("query", "--label-key", "~key")
    assert args.labels == [("--label-key", "_key")]


def test_parse_args_prune_by_label(parse: Callable[..., Namespace]) -> None:
    args = parse(
        "prune", "--repository", "r", "--based-on", "label",
        "--label-key", "k", "--label-value", "v",
    )  # fmt: skip
    assert args.func is ra._fn_prune
    assert args.labels == [("--label-key", "k"), ("--label-value", "v")]
    assert args.age is None
    assert args.age_from is None
    assert args.age_to is None
    assert args.age_unit is None


def test_parse_args_prune_by_age(parse: Callable[..., Namespace]) -> None:
    args = parse(
        "prune", "--repository", "r", "--based-on", "created", "--age", "3", "--age-unit", "days"
    )
    assert args.age == 3
    assert args.age_unit == "days"
    assert args.protection_time == 0


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(
            ["--based-on", "label", "--label-key", "k", "--age", "1"], id="label-with-age"
        ),
        pytest.param(
            ["--based-on", "label", "--label-key", "k", "--age-unit", "days"],
            id="label-with-age-unit",
        ),
        pytest.param(
            ["--based-on", "created", "--age", "1", "--age-unit", "days", "--label-key", "k"],
            id="time-with-label",
        ),
        pytest.param(["--based-on", "retention", "--age-unit", "days"], id="time-without-age"),
    ],
)
def test_parse_args_prune_invalid_combinations(
    parse: Callable[..., Namespace], argv: list[str]
) -> None:
    with pytest.raises(SystemExit):
        parse("prune", "--repository", "r", *argv)


def test_parse_args_prune_label_without_labels(parse: Callable[..., Namespace]) -> None:
    with pytest.raises(SystemExit):
        parse("prune", "--repository", "r", "--based-on", "label")


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({}, ["pulp", "--domain=nobackup"]),
        ({"domain": "backup"}, ["pulp", "--domain=backup"]),
        ({"domain": ""}, ["pulp"]),
        ({"pulp_dry_run": True}, ["pulp", "--domain=nobackup", "--dry-run"]),
        ({"pulp_verbose": 1}, ["pulp", "--domain=nobackup", "-v"]),
        (
            {"domain": "default", "pulp_dry_run": True, "pulp_verbose": 3},
            ["pulp", "--domain=default", "--dry-run", "-vvv"],
        ),
    ],
)
def test_pulp_base_arg(kwargs: dict[str, Any], expected: list[str]) -> None:
    assert ra._pulp_base_arg(make_args(**kwargs)) == expected


@pytest.mark.parametrize(
    "key, value, expected",
    [
        ("k", "v", "k=v"),
        ("k", "!v", "k!=v"),
        ("k", "~v", "k~v"),
        ("k", "", "k="),
        ("k", "v!", "k=v!"),
    ],
)
def test_format_label(key: str, value: str, expected: str) -> None:
    assert ra._format_label(key, value) == expected


# --------------------------------------------------------------------------------------
# query
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "labels, expected_selector",
    [
        pytest.param([], "", id="empty"),
        pytest.param([("--label-key", "k")], "k", id="key-only"),
        pytest.param([("--label-key", "k"), ("--label-value", "v")], "k=v", id="key-value"),
        pytest.param(
            [("--label-key", "a"), ("--label-key", "b"), ("--label-value", "!x")],
            "a,b!=x",
            id="key-only-then-pair",
        ),
        pytest.param(
            [
                ("--label-key", "a"),
                ("--label-value", "~x"),
                ("--label-key", "b"),
                ("--label-value", "y"),
                ("--label-key", "!c"),
            ],
            "a~x,b=y,!c",
            id="mixed",
        ),
        pytest.param([("--label-value", "orphan")], "", id="value-without-key-ignored"),
    ],
)
def test_query_label_selector(
    pulp: FakePulp, labels: list[tuple[str, str]], expected_selector: str
) -> None:
    pulp.set(("file", "content", "list"), [{"relative_path": "a"}])
    result = ra._fn_query(make_args(labels=labels), return_result=True)

    assert result == [{"relative_path": "a"}]
    assert pulp.calls == [
        [
            "pulp",
            "--domain=nobackup",
            "file",
            "content",
            "list",
            f"--limit={ra.LIST_LIMIT}",
            f"--label-select={expected_selector}",
        ]
    ]


def test_query_custom_limit(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "list"), [])
    ra._fn_query(make_args(labels=[], limit=5), return_result=True)
    assert "--limit=5" in pulp.calls[0]


@pytest.mark.parametrize(
    "argv, expected",
    [
        pytest.param(["query"], ra.LIST_LIMIT, id="query-default"),
        pytest.param(["query", "--limit", "5"], 5, id="query-custom"),
        pytest.param(["list", "--repository", "r"], ra.LIST_LIMIT, id="list-default"),
        pytest.param(["list", "--repository", "r", "--limit", "5"], 5, id="list-custom"),
        pytest.param(
            ["download", "--repository", "r", "--label-key", "k", "--label-value", "v"],
            ra.LIST_LIMIT,
            id="download-default",
        ),
        pytest.param(
            ["download", "--repository", "r", "--label-key", "k", "--label-value", "v",
             "--limit", "5"],
            5,
            id="download-custom",
        ),  # fmt: skip
        pytest.param(
            ["prune", "--repository", "r", "--based-on", "created", "--age", "1",
             "--age-unit", "days"],
            ra.LIST_LIMIT,
            id="prune-default",
        ),  # fmt: skip
        pytest.param(
            ["prune", "--repository", "r", "--based-on", "label", "--label-key", "k",
             "--limit", "5"],
            5,
            id="prune-custom",
        ),  # fmt: skip
    ],
)
def test_parse_args_limit(parse: Callable[..., Namespace], argv: list[str], expected: int) -> None:
    assert parse(*argv).limit == expected



def test_query_prints_response(pulp: FakePulp, capsys: pytest.CaptureFixture[str]) -> None:
    pulp.set(("file", "content", "list"), [{"a": 1}])
    assert ra._fn_query(make_args(labels=[], print_response=True)) is None
    assert json.loads(capsys.readouterr().out) == [{"a": 1}]


def test_query_silent_without_print_response(
    pulp: FakePulp, capsys: pytest.CaptureFixture[str]
) -> None:
    pulp.set(("file", "content", "list"), [{"a": 1}])
    ra._fn_query(make_args(labels=[]))
    assert capsys.readouterr().out == ""


def test_parse_args_query_labels_default_to_none(parse: Callable[..., Namespace]) -> None:
    assert parse("query").labels is None


@pytest.mark.parametrize(
    "label_args",
    [
        pytest.param([], id="no-labels"),
        pytest.param(["--label-key", "k"], id="key-only"),
        pytest.param(["--label-value", "v"], id="value-only"),
    ],
)
def test_parse_args_download_requires_labels(
    parse: Callable[..., Namespace], capsys: pytest.CaptureFixture[str], label_args: list[str]
) -> None:
    with pytest.raises(SystemExit):
        parse("download", "--repository", "r", *label_args)
    assert "the following arguments are required" in capsys.readouterr().err


def test_parse_args_download_labels_keep_order(parse: Callable[..., Namespace]) -> None:
    args = parse(
        "download", "--repository", "r",
        "--label-key", "branch", "--label-value", "~ma.in", "--label-key", "!old",
    )  # fmt: skip
    assert args.labels == [
        ("--label-key", "branch"),
        ("--label-value", "~ma_in"),
        ("--label-key", "!old"),
    ]
    assert args.out_dir == Path("download")
    assert args.total_download_timeout == 240


def test_query_without_labels_from_cli(pulp: FakePulp, parse: Callable[..., Namespace]) -> None:
    pulp.set(("file", "content", "list"), [{"a": 1}])
    args = parse("query")
    assert ra._fn_query(args, return_result=True) == [{"a": 1}]
    assert pulp.calls[0][-1] == "--label-select="


# --------------------------------------------------------------------------------------
# list / show
# --------------------------------------------------------------------------------------


def test_list(pulp: FakePulp, capsys: pytest.CaptureFixture[str]) -> None:
    pulp.set(("file", "repository", "content", "list"), [{"x": 1}])
    args = make_args(repository="repo")

    assert ra._fn_list(args, return_result=True) == [{"x": 1}]
    assert pulp.calls[-1] == [
        "pulp", "--domain=nobackup", "file", "repository", "content", "list",
        f"--limit={ra.LIST_LIMIT}", "--repository=repo",
    ]  # fmt: skip

    ra._fn_list(args)
    assert capsys.readouterr().out == ""

    args.print_response = True
    args.pretty = True
    ra._fn_list(args)
    assert capsys.readouterr().out == json.dumps([{"x": 1}], indent=4) + "\n"


def test_list_custom_limit_from_cli(pulp: FakePulp, parse: Callable[..., Namespace]) -> None:
    pulp.set(("file", "repository", "content", "list"), [])
    ra._fn_list(parse("list", "--repository", "repo", "--limit", "5"))
    assert "--limit=5" in pulp.calls[0]


def test_prune_and_download_use_default_limit(pulp: FakePulp, tmp_path: Path) -> None:
    # args built without a 'limit' attribute fall back to the module default
    pulp.set(("file", "repository", "content", "list"), [])
    pulp.set(("file", "content", "list"), [])
    pulp.set(("file", "distribution", "show"), {"base_url": "https://pulp/"})
    ra._fn_prune(prune_args(age=1))
    ra._fn_prune(prune_args(based_on="label", age_unit=None, labels=[("--label-key", "k")]))
    ra._fn_download(make_args(repository="r", labels=None, out_dir=tmp_path,
                              total_download_timeout=1))  # fmt: skip
    list_calls = pulp.calls_for("file", "repository", "content", "list") + pulp.calls_for(
        "file", "content", "list"
    )
    assert len(list_calls) == 3
    assert all(f"--limit={ra.LIST_LIMIT}" in call for call in list_calls)


def test_show_uses_args_href(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "show"), {"pulp_href": "/a/"})
    assert ra._fn_show(make_args(href="/a/"), href="/other/", return_result=True) == {
        "pulp_href": "/a/"
    }
    assert pulp.calls == [["pulp", "--domain=nobackup", "file", "content", "show", "--href=/a/"]]


def test_show_falls_back_to_href_parameter(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "show"), {})
    ra._fn_show(make_args(), href="/fallback/", return_result=True)
    assert pulp.calls[0][-1] == "--href=/fallback/"


def test_show_prints_by_default(pulp: FakePulp, capsys: pytest.CaptureFixture[str]) -> None:
    pulp.set(("file", "content", "show"), {"a": "b"})
    assert ra._fn_show(make_args(href="/a/")) is None
    assert json.loads(capsys.readouterr().out) == {"a": "b"}


# --------------------------------------------------------------------------------------
# remove / orphan cleanup
# --------------------------------------------------------------------------------------


def remove_args(**kwargs: Any) -> Namespace:
    return make_args(
        repository="repo", sha256="abc", remote_path=Path("a/b.txt"), schedule_cleanup=False,
        protection_time=0, **kwargs,
    )  # fmt: skip


def test_remove(pulp: FakePulp) -> None:
    ra._fn_remove(remove_args())
    assert pulp.calls == [
        [
            "pulp", "--domain=nobackup", "file", "repository", "content", "remove",
            "--repository=repo", "--sha256=abc", "--relative-path=a/b.txt",
        ]
    ]  # fmt: skip


def test_remove_with_cleanup(pulp: FakePulp) -> None:
    args = remove_args()
    args.schedule_cleanup = True
    args.protection_time = 5
    ra._fn_remove(args)
    assert len(pulp.calls_for("file", "repository", "content", "remove")) == 1
    assert pulp.calls_for("orphan", "cleanup") == [
        ["pulp", "--domain=nobackup", "orphan", "cleanup", "--protection-time=5"]
    ]


@pytest.mark.parametrize("flag", ["dry_run", "pulp_dry_run"])
def test_remove_dry_run(pulp: FakePulp, flag: str) -> None:
    args = remove_args(**{flag: True})
    args.schedule_cleanup = True
    ra._fn_remove(args)
    assert pulp.calls == []


def test_orphan_cleanup_default_protection_time(pulp: FakePulp) -> None:
    ra._fn_orphan_cleanup(make_args())
    assert pulp.calls == [["pulp", "--domain=nobackup", "orphan", "cleanup", "--protection-time=0"]]


@pytest.mark.parametrize("flag", ["dry_run", "pulp_dry_run"])
def test_orphan_cleanup_dry_run(pulp: FakePulp, flag: str) -> None:
    ra._fn_orphan_cleanup(make_args(**{flag: True}))
    assert pulp.calls == []


# --------------------------------------------------------------------------------------
# label
# --------------------------------------------------------------------------------------


def label_args(**kwargs: Any) -> Namespace:
    defaults: dict[str, Any] = {"href": "/h/", "label_key": None, "label_value": None}
    defaults.update(kwargs)
    return make_args(**defaults)


def set_label_calls(pulp: FakePulp) -> dict[str, str]:
    result = {}
    for call in pulp.calls_for("file", "content", "label", "set"):
        opts = dict(c.split("=", 1) for c in call if c.startswith("--") and "=" in c)
        assert opts["--href"] == "/h/"
        result[opts["--key"]] = opts["--value"]
    return result


def test_label_sets_new_labels(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "show"), {"pulp_labels": {}})
    ra._fn_label(label_args(label_key=["a", "b"], label_value=["1", "2"]))
    assert set_label_calls(pulp) == {"a": "1", "b": "2"}


def test_label_skips_existing_without_overwrite(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "show"), {"pulp_labels": {"a": "old"}})
    ra._fn_label(label_args(label_key=["a", "b"], label_value=["1", "2"]))
    assert set_label_calls(pulp) == {"b": "2"}


@pytest.mark.parametrize(
    "args_overwrite, param_overwrite",
    [(True, False), (False, True)],
)
def test_label_overwrites_existing(
    pulp: FakePulp, args_overwrite: bool, param_overwrite: bool
) -> None:
    pulp.set(("file", "content", "show"), {"pulp_labels": {"a": "old"}})
    ra._fn_label(
        label_args(label_key=["a"], label_value=["new"], overwrite=args_overwrite),
        overwrite_labels=param_overwrite,
    )
    assert set_label_calls(pulp) == {"a": "new"}


def test_label_without_values_does_nothing(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "show"), {"pulp_labels": {}})
    ra._fn_label(label_args(label_key=["a"], label_value=None))
    assert set_label_calls(pulp) == {}
    # existing labels are always fetched
    assert len(pulp.calls_for("file", "content", "show")) == 1


def test_label_dry_run(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "show"), {"pulp_labels": {}})
    ra._fn_label(label_args(label_key=["a"], label_value=["1"], dry_run=True))
    assert set_label_calls(pulp) == {}


def test_label_sets_retention(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "show"), {"pulp_labels": {}})
    before = datetime.now(ra.BASE_TIME_ZONE)
    ra._fn_label(
        label_args(retention_unit="hours", retention_duration=2),
        set_retention=True,
    )
    after = datetime.now(ra.BASE_TIME_ZONE)

    labels = set_label_calls(pulp)
    assert list(labels) == ["retention"]
    retention = datetime.strptime(labels["retention"], ra.TIMESTAMP_FORMAT).replace(
        tzinfo=ra.BASE_TIME_ZONE
    )
    assert before + timedelta(hours=2) <= retention <= after + timedelta(hours=2)


def test_label_explicit_retention_label_wins(pulp: FakePulp) -> None:
    pulp.set(("file", "content", "show"), {"pulp_labels": {}})
    ra._fn_label(
        label_args(
            label_key=["retention"], label_value=["forever"],
            retention_unit="days", retention_duration=1,
        ),
        set_retention=True,
    )  # fmt: skip
    assert set_label_calls(pulp) == {"retention": "forever"}


# --------------------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------------------


def upload_args(**kwargs: Any) -> Namespace:
    defaults: dict[str, Any] = {
        "repository": "repo",
        "file": Path("local/f.txt"),
        "remote_path": Path("remote/f.txt"),
        "label_key": ["k"],
        "label_value": ["v"],
        "retention_duration": 7,
        "retention_unit": "days",
    }
    defaults.update(kwargs)
    return make_args(**defaults)


def test_upload(pulp: FakePulp, capsys: pytest.CaptureFixture[str]) -> None:
    pulp.set(("file", "content", "upload"), {"pulp_href": "/content/1/"})
    pulp.set(("file", "content", "show"), {"pulp_labels": {"k": "old"}})
    args = upload_args()

    ra._fn_upload(args)

    assert pulp.calls[0] == [
        "pulp", "--domain=nobackup", "file", "content", "upload",
        "--repository=repo", "--file=local/f.txt", "--relative-path=remote/f.txt",
    ]  # fmt: skip
    assert args.href == "/content/1/"
    labels = {
        dict(c.split("=", 1) for c in call if "=" in c)["--key"]: call
        for call in pulp.calls_for("file", "content", "label", "set")
    }
    # upload always overwrites labels and adds a retention label
    assert set(labels) == {"k", "retention"}
    assert all("--href=/content/1/" in call for call in labels.values())
    assert capsys.readouterr().out == ""


def test_upload_print_response(pulp: FakePulp, capsys: pytest.CaptureFixture[str]) -> None:
    pulp.set(("file", "content", "upload"), {"pulp_href": "/content/1/"})
    pulp.set(("file", "content", "show"), {"pulp_href": "/content/1/", "pulp_labels": {}})
    ra._fn_upload(upload_args(print_response=True))
    assert json.loads(capsys.readouterr().out)["pulp_href"] == "/content/1/"


@pytest.mark.parametrize("flag", ["dry_run", "pulp_dry_run"])
def test_upload_dry_run(pulp: FakePulp, caplog: pytest.LogCaptureFixture, flag: str) -> None:
    with caplog.at_level("DEBUG", logger=ra.logger.name):
        ra._fn_upload(upload_args(**{flag: True}))
    # nothing uploaded, so there is nothing to show or to label
    assert pulp.calls == []
    assert "Would set labels" in caplog.text
    assert "'k': 'v'" in caplog.text
    assert "'retention'" in caplog.text


def test_upload_dry_run_from_cli(pulp: FakePulp, parse: Callable[..., Namespace]) -> None:
    args = parse(
        "--dry-run", "upload", "--repository", "r", "--file", "f", "--remote-path", "f",
        "--label-key", "k", "--label-value", "v",
    )  # fmt: skip
    ra._fn_upload(args)
    assert pulp.calls == []


def test_labels_to_set() -> None:
    args = upload_args(label_key=["a", "b"], label_value=["1", "2"])
    assert ra._labels_to_set(args) == {"a": "1", "b": "2"}
    assert set(ra._labels_to_set(args, set_retention=True)) == {"a", "b", "retention"}
    assert ra._labels_to_set(upload_args(label_key=None, label_value=None)) == {}


# --------------------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        yield from self.chunks


def test_download_file(tmp_path: Path) -> None:
    with mock.patch.object(
        requests, "get", return_value=FakeResponse([b"hello ", b"", b"world"])
    ) as get:
        ra._download_file("http://x/a/b.txt", tmp_path, Path("a/b.txt"))

    get.assert_called_once_with("http://x/a/b.txt", stream=True, timeout=30)
    assert (tmp_path / "a/b.txt").read_bytes() == b"hello world"


def test_download_file_dry_run(tmp_path: Path) -> None:
    with mock.patch.object(requests, "get") as get:
        ra._download_file("http://x/a.txt", tmp_path / "out", Path("a.txt"), dry_run=True)
    get.assert_not_called()
    assert not (tmp_path / "out").exists()


def test_download_file_retries_then_succeeds(tmp_path: Path) -> None:
    responses = [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ChunkedEncodingError("boom"),
        FakeResponse([b"ok"]),
    ]
    with mock.patch.object(requests, "get", side_effect=responses) as get:
        ra._download_file("http://x/a.txt", tmp_path, Path("a.txt"))
    assert get.call_count == 3
    assert (tmp_path / "a.txt").read_bytes() == b"ok"


def test_download_file_gives_up_after_max_retries(tmp_path: Path) -> None:
    with mock.patch.object(
        requests, "get", side_effect=requests.exceptions.ConnectionError("boom")
    ) as get:
        with pytest.raises(requests.exceptions.ConnectionError):
            ra._download_file("http://x/a.txt", tmp_path, Path("a.txt"))
    assert get.call_count == ra.MAX_RETRY_ATTEMPTS + 1


def test_download_file_http_error_not_retried(tmp_path: Path) -> None:
    response = FakeResponse([], error=requests.exceptions.HTTPError("404"))
    with mock.patch.object(requests, "get", return_value=response) as get:
        with pytest.raises(requests.exceptions.HTTPError):
            ra._download_file("http://x/a.txt", tmp_path, Path("a.txt"))
    assert get.call_count == 1


def test_download_file_timeout(tmp_path: Path) -> None:
    with (
        mock.patch.object(requests, "get", return_value=FakeResponse([b"a", b"b"])) as get,
        mock.patch.object(time, "time", side_effect=[0, 0, 100] * 10),
    ):
        with pytest.raises(TimeoutError):
            ra._download_file("http://x/a.txt", tmp_path, Path("a.txt"), total_download_timeout=10)
    assert get.call_count == ra.MAX_RETRY_ATTEMPTS + 1


def test_fn_download_custom_limit_from_cli(
    pulp: FakePulp, parse: Callable[..., Namespace], tmp_path: Path
) -> None:
    pulp.set(("file", "distribution", "show"), {"base_url": "https://pulp/"})
    pulp.set(("file", "content", "list"), [])
    args = parse(
        "download", "--repository", "r", "--label-key", "k", "--label-value", "v",
        "--limit", "5", "--out-dir", str(tmp_path),
    )  # fmt: skip
    ra._fn_download(args)
    assert pulp.calls_for("file", "content", "list")[0][-2:] == ["--limit=5", "--label-select=k=v"]


def test_fn_download(pulp: FakePulp, tmp_path: Path) -> None:
    pulp.set(("file", "distribution", "show"), {"base_url": "https://pulp/content/repo/"})
    pulp.set(
        ("file", "content", "list"),
        [{"relative_path": "a/one.txt"}, {"relative_path": "two.txt"}],
    )
    args = make_args(
        repository="repo",
        labels=[("--label-key", "k"), ("--label-value", "v")],
        out_dir=tmp_path,
        total_download_timeout=30,
    )

    with mock.patch.object(ra, "_download_file") as download:
        ra._fn_download(args)

    assert pulp.calls_for("file", "distribution", "show")[0][-1] == "--name=repo"
    assert pulp.calls_for("file", "content", "list")[0][-1] == "--label-select=k=v"
    assert download.call_args_list == [
        mock.call(
            url="https://pulp/content/repo/a/one.txt", download_dir=tmp_path,
            artifact_filename=Path("a/one.txt"), total_download_timeout=30, dry_run=False,
        ),
        mock.call(
            url="https://pulp/content/repo/two.txt", download_dir=tmp_path,
            artifact_filename=Path("two.txt"), total_download_timeout=30, dry_run=False,
        ),
    ]  # fmt: skip


# --------------------------------------------------------------------------------------
# prune
# --------------------------------------------------------------------------------------


def prune_args(**kwargs: Any) -> Namespace:
    defaults: dict[str, Any] = {
        "repository": "repo",
        "based_on": "created",
        "protection_time": 0,
        "labels": None,
        "label_key": None,
        "label_value": None,
        "age": None,
        "age_from": None,
        "age_to": None,
        "age_unit": "days",
    }
    defaults.update(kwargs)
    return make_args(**defaults)


@pytest.mark.parametrize(
    "based_on, file_details",
    [
        ("created", {"pulp_created": ts(timedelta(days=-10))}),
        ("last_updated", {"pulp_last_updated": ts(timedelta(days=-10))}),
        ("retention", {"pulp_labels": {"retention": ts(timedelta(days=-10))}}),
    ],
)
def test_matches_prune_time_criteria_reference_field(
    based_on: str, file_details: dict[str, Any]
) -> None:
    now = datetime.now(ra.BASE_TIME_ZONE)
    args = prune_args(based_on=based_on, age=5)
    assert ra._matches_prune_time_criteria(file_details, args, None, now - timedelta(days=5))
    assert not ra._matches_prune_time_criteria(file_details, args, None, now - timedelta(days=15))


@pytest.mark.parametrize(
    "based_on, file_details",
    [
        ("created", {}),
        ("last_updated", {"pulp_created": ts(timedelta(days=-10))}),
        ("retention", {"pulp_labels": {}}),
        ("retention", {}),
        ("label", {"pulp_created": ts(timedelta(days=-10))}),
    ],
)
def test_matches_prune_time_criteria_missing_reference(
    based_on: str, file_details: dict[str, Any]
) -> None:
    args = prune_args(based_on=based_on, age=1)
    assert not ra._matches_prune_time_criteria(
        file_details, args, None, datetime.now(ra.BASE_TIME_ZONE)
    )


def test_matches_prune_time_criteria_age_without_end_time() -> None:
    args = prune_args(age=1)
    assert not ra._matches_prune_time_criteria(
        {"pulp_created": ts(timedelta(days=-10))}, args, None, None
    )


@pytest.mark.parametrize(
    "file_age_days, start_days, end_days, expected",
    [
        (5, 10, 2, True),  # inside range
        (12, 10, 2, False),  # older than start
        (1, 10, 2, False),  # newer than end
        (5, None, 2, True),  # only end (age_from)
        (1, None, 2, False),
        (5, 10, None, True),  # only start (age_to)
        (12, 10, None, False),
        (5, None, None, True),  # no boundaries
    ],
)
def test_matches_prune_time_criteria_range(
    file_age_days: int, start_days: int | None, end_days: int | None, expected: bool
) -> None:
    now = datetime.now(ra.BASE_TIME_ZONE)
    start = None if start_days is None else now - timedelta(days=start_days)
    end = None if end_days is None else now - timedelta(days=end_days)
    details = {"pulp_created": ts(timedelta(days=-file_age_days))}
    assert ra._matches_prune_time_criteria(details, prune_args(), start, end) is expected


def removed_paths(pulp: FakePulp) -> list[str]:
    return [
        next(c.split("=", 1)[1] for c in call if c.startswith("--relative-path="))
        for call in pulp.calls_for("file", "repository", "content", "remove")
    ]


def test_prune_by_age(pulp: FakePulp) -> None:
    pulp.set(
        ("file", "repository", "content", "list"),
        [
            {"relative_path": "old", "sha256": "1", "pulp_created": ts(timedelta(days=-10))},
            {"relative_path": "new", "sha256": "2", "pulp_created": ts(timedelta(days=-1))},
            {"relative_path": "unknown", "sha256": "3"},
        ],
    )
    ra._fn_prune(prune_args(age=5, protection_time=3))

    assert removed_paths(pulp) == ["old"]
    remove_call = pulp.calls_for("file", "repository", "content", "remove")[0]
    assert "--sha256=1" in remove_call
    assert "--repository=repo" in remove_call
    # one single cleanup after all removals
    assert pulp.calls_for("orphan", "cleanup") == [
        ["pulp", "--domain=nobackup", "orphan", "cleanup", "--protection-time=3"]
    ]


def test_prune_by_age_range(pulp: FakePulp) -> None:
    pulp.set(
        ("file", "repository", "content", "list"),
        [
            {
                "relative_path": f"d{d}",
                "sha256": str(d),
                "pulp_last_updated": ts(timedelta(days=-d)),
            }
            for d in (1, 3, 5, 7, 9)
        ],
    )
    ra._fn_prune(prune_args(based_on="last_updated", age_from=2, age_to=6))
    assert removed_paths(pulp) == ["d3", "d5"]


def test_prune_by_retention_hours(pulp: FakePulp) -> None:
    pulp.set(
        ("file", "repository", "content", "list"),
        [
            {"relative_path": "expired", "sha256": "1",
             "pulp_labels": {"retention": ts(timedelta(hours=-1))}},
            {"relative_path": "valid", "sha256": "2",
             "pulp_labels": {"retention": ts(timedelta(hours=1))}},
            {"relative_path": "no-retention", "sha256": "3", "pulp_labels": {}},
        ],
    )  # fmt: skip
    ra._fn_prune(prune_args(based_on="retention", age=0, age_unit="hours"))
    assert removed_paths(pulp) == ["expired"]


def test_prune_nothing_matches_no_cleanup(pulp: FakePulp) -> None:
    pulp.set(
        ("file", "repository", "content", "list"),
        [{"relative_path": "new", "sha256": "2", "pulp_created": ts(timedelta(days=-1))}],
    )
    ra._fn_prune(prune_args(age=5))
    assert removed_paths(pulp) == []
    assert pulp.calls_for("orphan", "cleanup") == []


def test_prune_by_label(pulp: FakePulp) -> None:
    pulp.set(
        ("file", "content", "list"),
        [{"relative_path": "a", "sha256": "1"}, {"relative_path": "b", "sha256": "2"}],
    )
    args = prune_args(
        based_on="label",
        age_unit=None,
        labels=[("--label-key", "branch"), ("--label-value", "!master")],
    )
    ra._fn_prune(args)

    assert pulp.calls_for("file", "content", "list")[0][-1] == "--label-select=branch!=master"
    assert pulp.calls_for("file", "repository", "content", "list") == []
    assert removed_paths(pulp) == ["a", "b"]
    assert len(pulp.calls_for("orphan", "cleanup")) == 1
    # original args are not modified by the per file copies
    assert not hasattr(args, "sha256")


@pytest.mark.parametrize("flag", ["dry_run", "pulp_dry_run"])
def test_prune_dry_run(pulp: FakePulp, flag: str) -> None:
    pulp.set(
        ("file", "repository", "content", "list"),
        [{"relative_path": "old", "sha256": "1", "pulp_created": ts(timedelta(days=-10))}],
    )
    ra._fn_prune(prune_args(age=5, **{flag: True}))
    # read operations are still performed, modifying ones are not
    assert len(pulp.calls_for("file", "repository", "content", "list")) == 1
    assert pulp.calls_for("file", "repository", "content", "remove") == []
    assert pulp.calls_for("orphan", "cleanup") == []


def test_prune_unknown_based_on(pulp: FakePulp) -> None:
    ra._fn_prune(prune_args(based_on="foo"))
    assert pulp.calls == []


@pytest.mark.parametrize("age_args", [["--age", "3"], ["--age-from", "1"], ["--age-to", "5"]])
def test_parse_args_prune_by_age_without_unit(
    parse: Callable[..., Namespace], capsys: pytest.CaptureFixture[str], age_args: list[str]
) -> None:
    with pytest.raises(SystemExit):
        parse("prune", "--repository", "r", "--based-on", "created", *age_args)
    assert "'--age-unit' is required" in capsys.readouterr().err


@pytest.mark.parametrize("age_args", [["--age", "3"], ["--age-from", "1"], ["--age-to", "5"]])
def test_parse_args_prune_by_age_with_unit(
    parse: Callable[..., Namespace], age_args: list[str]
) -> None:
    args = parse(
        "prune", "--repository", "r", "--based-on", "created", *age_args, "--age-unit", "hours"
    )
    assert args.age_unit == "hours"


@pytest.mark.parametrize(
    "based_on_args, subcommand",
    [
        pytest.param(
            ["--based-on", "created", "--age", "3", "--age-unit", "days"],
            ("file", "repository", "content", "list"),
            id="time-based",
        ),
        pytest.param(
            ["--based-on", "label", "--label-key", "k"],
            ("file", "content", "list"),
            id="label-based",
        ),
    ],
)
def test_prune_custom_limit_from_cli(
    pulp: FakePulp,
    parse: Callable[..., Namespace],
    based_on_args: list[str],
    subcommand: tuple[str, ...],
) -> None:
    pulp.set(subcommand, [])
    ra._fn_prune(parse("prune", "--repository", "r", *based_on_args, "--limit", "5"))
    calls = pulp.calls_for(*subcommand)
    assert len(calls) == 1
    assert "--limit=5" in calls[0]


def test_prune_by_age_from_cli(pulp: FakePulp, parse: Callable[..., Namespace]) -> None:
    pulp.set(
        ("file", "repository", "content", "list"),
        [{"relative_path": "old", "sha256": "1", "pulp_created": ts(timedelta(days=-10))}],
    )
    args = parse(
        "prune", "--repository", "r", "--based-on", "created", "--age", "3", "--age-unit", "days"
    )
    ra._fn_prune(args)
    assert removed_paths(pulp) == ["old"]


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------


def test_main_dispatches(pulp: FakePulp) -> None:
    pulp.set(("file", "repository", "content", "list"), [])
    with mock.patch("sys.argv", ["ra", "list", "--repository", "repo"]):
        ra.main()
    assert pulp.calls_for("file", "repository", "content", "list")[0][-1] == "--repository=repo"


@pytest.mark.parametrize(
    "verbose, level",
    [(0, "CRITICAL"), (2, "WARNING"), (4, "DEBUG"), (9, "DEBUG")],
)
def test_main_sets_log_level(pulp: FakePulp, verbose: int, level: str) -> None:
    pulp.set(("file", "content", "show"), {})
    argv = ["ra", *(["-" + "v" * verbose] if verbose else []), "show", "--href", "/h/"]
    with mock.patch("sys.argv", argv), mock.patch.object(ra.logger, "setLevel") as set_level:
        ra.main()
    set_level.assert_called_once_with(level)


def test_main_warns_on_default_domain(pulp: FakePulp, caplog: pytest.LogCaptureFixture) -> None:
    pulp.set(("file", "content", "show"), {})
    with (
        mock.patch("sys.argv", ["ra", "-vv", "--domain", "default", "show", "--href", "/h/"]),
        caplog.at_level("WARNING", logger=ra.logger.name),
    ):
        ra.main()
    assert "default domain" in caplog.text
    assert pulp.calls[0][:2] == ["pulp", "--domain=default"]


def test_matches_prune_time_criteria_logs_timestamp_before_end_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    file_ts = ts(timedelta(days=-10))
    end_time = datetime.now(ra.BASE_TIME_ZONE) - timedelta(days=5)
    with caplog.at_level("DEBUG", logger=ra.logger.name):
        ra._matches_prune_time_criteria(
            {"pulp_created": file_ts}, prune_args(age=5), None, end_time
        )
    timestamp = datetime.strptime(file_ts, ra.TIMESTAMP_FORMAT).replace(tzinfo=ra.BASE_TIME_ZONE)
    assert f"File timestamp < specified end time: {timestamp} < {end_time}" in caplog.text
