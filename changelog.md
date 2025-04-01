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
