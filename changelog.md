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
## [1.0.1] - 2025-10-14T09:52:05+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

Iterate over the results reported by the InfluxDB in a latest to oldest order to return the latest build instead of the first match to take rebuilds and reruns into account.

[1.0.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//1.0.1

## [1.0.0] - 2025-10-08T16:33:53+02:00
<!-- meta = {'type': 'breaking', 'scope': ['all'], 'affected': ['all']} -->

With this change `ci-artifacts` uses all available information provided by `QueueItem`
instances to identify potentially identical builds.

[1.0.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//1.0.0

## [0.14.0] - 2025-09-24T08:27:25+02:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

With this change the CLI option `--no-simple-logging` is added. By default the standard python logging module is used.

[0.14.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.14.0

## [0.13.1] - 2025-09-23T10:48:32+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

Before this change an error like the following was always raised

```json
{"err": "Fatal exception: The build we started has result=FAILURE (https://JENKINS/path/to/job/316/)"}
```

This did not allow to download may existing artifacts of a failed build with a command call like

```sh
ci-artifacts --log-level debug download path/to/job:316 --no-remove-others --base-dir=. --out-dir=tmp_artifacts
```

But these artifacts might be of interest for analysis.

With this change the hard error is skipable with the `--no-raise` option and the response JSON is changed to the following example in case the awaited build or the job to download artifacts has the job status as anything but success. This is now in sync with the result returned when requesting a successfully passed job.

```json
{"result": "FAILURE", "artifacts": ["results/shellcheck.txt"]}
```

[0.13.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.13.1

## [0.13.0] - 2025-09-23T10:41:15+02:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

Previously an error `Job has no artifacts!` was raised if the download did not return any artifacts in case the job had no artifacts.

With this change an empty list is returned. The decision to raise an error if nothing is provided by the job should be up to the consumer, but not to the tool. To make this change as soft as possible the `--no-raise` flag is introduced to opt-out of raising an error and return an emtpy list instead.

[0.13.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.13.0

## [0.12.5] - 2025-09-23T10:29:22+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

The env variables `FORCE_COLOR` and `COLUMNS` still get the same default value, as before but can now be overwritten by setting the env variable to a different value.

[0.12.5]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.12.5

## [0.12.4] - 2025-09-17T17:11:06+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

With this fix a valid JSON value is always returned on a error.
Previously the returned value was the printed exception thrown by `raise Fatal()`. The output looked like `The build we started has result=FAILURE` as an example.
The output of `ci-artifacts` calls can be captured by other tools which try to process the data and expect a JSON on success.
Errors are now printed with the logger on error level and an empty JSON is returned as value which is always processable by other tools.

[0.12.4]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.12.4

## [0.12.3] - 2025-09-17T15:00:44+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

Document all available subcommands of `ci-artifacts`. This adds documentation for the `info` subcommand.

[0.12.3]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.12.3

## [0.12.2] - 2025-09-16T23:01:37+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

Fix logging as `params` can not be printed as string when checking for queued items

[0.12.2]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.12.2

## [0.12.1] - 2025-09-15T14:10:35+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

With this change a `RUNNING` job found in the InfluxDB result is not directly rejected and used for further checks to meet the configured constraints.
Moreover logs are provided if the pending queue is checked after no InfluxDB match was found.

[0.12.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.12.1

## [0.12.0] - 2025-08-28T14:53:41+02:00
<!-- meta = {'type': 'feature', 'scope': ['all'], 'affected': ['all']} -->

This reduces the REST API request load at Jenkins tremendously.

To use this feature the [InfluxDB Jenkins plugin](https://plugins.jenkins.io/influxdb) is required.

The change is fully backwards compatible and can be enabled with the
`--influxdb` argument.

[0.12.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.12.0

## [0.11.0] - 2025-08-22T15:13:39+02:00
<!-- meta = {'type': 'feature', 'scope': ['internal'], 'affected': ['all']} -->

Update python-jenkins to 1.8.3 or newer

[This change](https://opendev.org/jjb/python-jenkins/commit/f29d64f991b7d44140863e0f4efa17633793e949)
includes the feature we've been waiting for.
It is included in release 1.8.3.

This also bumps all dependencies.

[0.11.0]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.11.0

## [0.10.3] - 2025-07-24T15:04:54+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['internal'], 'affected': ['all']} -->

This change introduces a timeout for the download step.
This was motivated by serveral failures when `ci-artifacts` did not finish but was eventually killed by the overall jenkins job timeout:
```build
[2025-07-24T01:13:58.458Z] DD │ handle artifact: bazel_log_cache_hits_debian-12.csv (md5: 0b86fe87f430c79af663d92de0fabc4b)
[2025-07-24T01:13:58.658Z] DD │ download: bazel_log_cache_hits_debian-12.csv
[2025-07-24T01:13:58.658Z] DD │ handle artifact: check-mk-raw-2.4.0-2025.07.24_0.bookworm_amd64.deb (md5: ff80ee95ce72fead1c84a14651972c51)
[2025-07-24T01:13:58.759Z] DD │ download: check-mk-raw-2.4.0-20
```

[0.10.3]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.10.3

## [0.10.2] - 2025-07-08T13:16:29+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

This does no longer ignore the value of the job parameter `DISABLE_CACHE` when checking for existing builds. As a result a job started with `DISABLE_CACHE=true` will not be taken into account if `DISABLE_CACHE=false` is set on other jobs.

[0.10.2]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.10.2

## [0.10.1] - 2025-07-03T11:55:48+02:00
<!-- meta = {'type': 'bugfix', 'scope': ['all'], 'affected': ['all']} -->

The `path_hashes` hve been extracted from the `CustomBuildPropertiesAction` of a job. This value was no longer updated and thereby causing mismatching builds for all builds since the last update of the build property.
With this fix the `path_hashes` are extraced the same way as all other build parameters. This now finds matching builds again

[0.10.1]: https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags//0.10.1

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
