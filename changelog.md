# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!--
## [x.y.z] - yyyy-mm-dd
### Added
### Changed
### Removed
### Fixed
-->
<!--
RegEx for release version from file
r"^\#\# \[\d{1,}[.]\d{1,}[.]\d{1,}\] \- \d{4}\-\d{2}-\d{2}$"
-->

## Released
## [0.10.0] - 2025-07-03T11:24:33+02:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

Printing the mismatching path hashes was not using `json.dumps` causing dicts being printed in raw to the console.
With this change it is now being printed in a machine readable JSON format

[0.10.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.10.0

## [0.9.1] - 2025-05-29T10:43:52+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

This fixes the new command line arguments `--poll-queue-sleep` and `--poll-sleep` of `v0.9.0` to be a common CLI argument and not specific to requests only.

[0.9.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.9.1

## [0.9.0] - 2025-05-27T17:22:28+02:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

With `0.6.0` the poll interval for queued and already running jobs have been increased to 30 and 60 seconds.
This change introduces the new, optional, command line arguments `--poll-queue-sleep`, keeping the 30 seconds as default value, and `--poll-sleep`, keeping the 60 seconds as default value.

[0.9.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.9.0

## [0.8.0] - 2025-04-28T14:01:16+02:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

- update `python-jenkins` package to custom package `python-jenkins-checkmk-retry-parameter` containing commit [`f29d64f991b7d44140863e0f4efa1`](https://opendev.org/jjb/python-jenkins/commit/f29d64f991b7d44140863e0f4efa17633793e949) due to PEP440 and PEP508, see also [PyPI issue 71736](https://github.com/pypi/warehouse/issues/7136) and [PyPI issue 9404](https://github.com/pypi/warehouse/issues/9404)
- the optional parameter `retries` specified the number of retries in case of a
failure
- set `retries` to 5 instead of default 0

[0.8.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.8.0

## [0.7.1] - 2025-04-16T15:34:34+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['internal'], 'affected': ['all']} -->

In some job runs `ci-artifacts` tries to query non-existant build infos.
This should be now avoided.

Backtrace:
```
File "/usr/local/lib/python3.12/asyncio/base_events.py", line 691, in run_until_complete
  return future.result()
         ^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/site-packages/cmk_dev/ci_artifacts.py", line 598, in _fn_request_build
  else await identify_matching_build(
       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/site-packages/cmk_dev/ci_artifacts.py", line 789, in identify_matching_build
  await job.expand(jenkins_client)
File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 199, in expand
  (build := await jenkins_client.build_info(self.path, b.number)).number: build
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 569, in build_info
  await self.raw_build_info(
File "/usr/local/lib/python3.12/site-packages/trickkiste/misc.py", line 254, in run
  return await (loop or asyncio.get_event_loop()).run_in_executor(
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/concurrent/futures/thread.py", line 59, in run
  result = self.fn(*self.args, **self.kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 564, in raw_build_info
  return self.client.get_build_info(job_full_name, build_number)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 670, in get_build_info
  raise JenkinsException('job[%s] number[%s] does not exist'
```

[0.7.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.7.1

## [0.7.0] - 2025-04-10T13:57:49+02:00
<!-- meta = {'type': 'feature', 'scope': ['internal'], 'affected': ['all']} -->


`ci-artifacts` may crash during job runs which look like the jenkins master is closing the connection.
The issue has more and more impact as we also try to parallize the CV validation by using `ci-artifacts`.
A possible traceback looks like:
```
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/urllib3/connectionpool.py", line 787, in urlopen
    response = self._make_request(
               ^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/urllib3/connectionpool.py", line 534, in _make_request
    response = conn.getresponse()
               ^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/urllib3/connection.py", line 516, in getresponse
    httplib_response = super().getresponse()
                       ^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/http/client.py", line 1430, in getresponse
    response.begin()
  File "/usr/local/lib/python3.12/http/client.py", line 331, in begin
    version, status, reason = self._read_status()
                              ^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/http/client.py", line 300, in _read_status
    raise RemoteDisconnected("Remote end closed connection without"
http.client.RemoteDisconnected: Remote end closed connection without response

D
uring handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/requests/adapters.py", line 667, in send
    resp = conn.urlopen(
           ^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/urllib3/connectionpool.py", line 841, in urlopen
    retries = retries.increment(
              ^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/urllib3/util/retry.py", line 474, in increment
    raise reraise(type(error), error, _stacktrace)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/urllib3/util/util.py", line 38, in reraise
    raise value.with_traceback(tb)
  File "/usr/local/lib/python3.12/site-packages/urllib3/connectionpool.py", line 787, in urlopen
    response = self._make_request(
               ^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/urllib3/connectionpool.py", line 534, in _make_request
    response = conn.getresponse()
               ^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/urllib3/connection.py", line 516, in getresponse
    httplib_response = super().getresponse()
                       ^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/http/client.py", line 1430, in getresponse
    response.begin()
  File "/usr/local/lib/python3.12/http/client.py", line 331, in begin
    version, status, reason = self._read_status()
                              ^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/http/client.py", line 300, in _read_status
    raise RemoteDisconnected("Remote end closed connection without"
urllib3.exceptions.ProtocolError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/usr/local/bin/ci-artifacts", line 8, in <module>
    sys.exit(main())
             ^^^^^^
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/ci_artifacts.py", line 961, in main
    asyncio.run(args.func(args))
  File "/usr/local/lib/python3.12/asyncio/runners.py", line 195, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/asyncio/runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/asyncio/base_events.py", line 691, in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/ci_artifacts.py", line 598, in _fn_request_build
    else await identify_matching_build(
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/ci_artifacts.py", line 789, in identify_matching_build
    await job.expand(jenkins_client)
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 199, in expand
    (build := await jenkins_client.build_info(self.path, b.number)).number: build
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 569, in build_info
    await self.raw_build_info(
  File "/usr/local/lib/python3.12/site-packages/trickkiste/misc.py", line 254, in run
    return await (loop or asyncio.get_event_loop()).run_in_executor(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/concurrent/futures/thread.py", line 59, in run
    result = self.fn(*self.args, **self.kwargs)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 564, in raw_build_info
    return self.client.get_build_info(job_full_name, build_number)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 661, in get_build_info
    response = self.jenkins_open(requests.Request(
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 562, in jenkins_open
    return self.jenkins_request(req, add_crumb, resolve_auth).text
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 589, in jenkins_request
    self._request(req, stream))
    ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 555, in _request
    return self._session.send(r, **_settings)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/requests/sessions.py", line 703, in send
    r = adapter.send(request, **kwargs)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/requests/adapters.py", line 682, in send
    raise ConnectionError(err, request=request)
requests.exceptions.ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
The JSON input text should neither be null nor empty.[2025-03-16T13:44:28.282Z] WARNING: Executing
```

[0.7.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.7.0

## [0.6.0] - 2025-04-01T16:43:36+02:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

A huge amount of requests towards the Jenkins API might lead to an overload of the Jenkins main controller and result in error messages like `Remote end closed connection without response`

This changes does
- Increase queue poll time from 1 to 30 seconds
- Increase job poll sleep time from 10 to 60 seconds

[0.6.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.6.0

## [0.5.1] - 2025-03-04T14:04:29+01:00
<!-- meta = {'type': 'bugfix', 'scope': ['internal'], 'affected': ['all']} -->

Render, commit and push a changelog during a release build. The branch will be named `release/<VERSION>`. The link to the rendered changelog on GitHub is visible on PyPI.

[0.5.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.5.1

## [0.5.0] - 2025-03-03T08:56:29+01:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

With `lockable-resources` the lockable resources of Jenkins can be listed, locked and unlocked via CLI.
See `README.md` for further details and options

[0.5.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.5.0

## [0.4.4] - 2025-02-20T11:03:49+01:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

We saw some additional failures due to broken network connection due to high
load on Jenkins. Similar to `Remote end closed connection without response`.
Let's do also three attempts and fail afterwards

[0.4.4]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.4.4

## [0.4.3] - 2025-02-13T10:20:25+01:00
<!-- meta = {'type': 'bugfix', 'scope': ['internal'], 'affected': ['all']} -->

We saw some failures due to broken network connection.
Let's do three attempts and fail afterwards.

[0.4.3]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.4.3

## [0.4.2] - 2025-02-12T15:01:33+01:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

CLI parameters are given and interpreted as string with `--params` and `--params-no-check`.
Handing over these to the Jenkins API endpoint works for string input forms. In case of boolean inputs Jenkins reports `parameter 'XYZ' did not have the type expected by <JOB_NAME>. Converting to Boolean Parameter.`
With this change the data of `params` and `params-no-check` is processed and converted to real bool values if it is a string like `'false'` or `'true'`.

[0.4.2]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.4.2

## [0.4.1] - 2025-02-05T14:01:07+01:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

Previously boolean job parameters can not be properly checked and compared.
This leads to unused existing builds due to `('FAKE_WINDOWS_ARTIFACTS', 'false', False)`.
There is already a hardcoded ignore or skip for `DISABLE_CACHE` which was up
to now the only actually checked boolean job parameter. Other boolean job
parameters were used in the `build_params_no_check` section and thereby
actually never checked.
This change introduces a simple bool mapping to handle the values properly
and no longer retrigger new builds due to `'false'` vs `False` mismatches

[0.4.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.4.1

## [0.4.0] - 2025-01-07T14:18:13+01:00
<!-- meta = {'type': 'feature', 'scope': ['external'], 'affected': ['all']} -->

Add script for container resource usage analysis

It uses files containing container metadata produced by docker-shaper to
find job which are causing high CPU or memory resource usage.

[0.4.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.4.0

## [0.3.1] - 2024-12-18T15:18:42+01:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

The function `_fn_fetch` is missing the arg parse argument `download` to
actually download the files from remote

[0.3.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.3.1

## [0.3.0] - 2024-12-10T18:14:18+01:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

- add `snippets2changelog` to `pyproject.toml` dev dependencies section and lock again
- add `changelog.md` with last non-changelog based release tag

[0.3.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.3.0

## [0.2.0] - 2024-11-14
### Changed
- Outsourced `docker_shaper` to https://review.lan.tribe29.com/plugins/gitiles/docker_shaper

[0.2.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags/0.2.0
