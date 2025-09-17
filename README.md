# Checkmk Development Tools

This repository includes scripts/tools for Checkmk developers.

- TBD: what should go here
- TBD: what shouldn't go here

## Installation

While you can just clone and use the tools inside of course (they're just plain Python or Bash
scripts), the intended way to use it is via `pip` or inside a virtual environment.

Install it locally using `pip`:

```sh
[<PYTHON> -m] pip[3] install [--user] [--upgrade] checkmk-dev-tools
```

## Contained tools

### General

#### Jenkins

For tools interacting with Jenkins an API key, username and URL to Jenkins has to be provided with `~/.config/jenkins_jobs/jenkins_jobs.ini` otherwise those parameters have to be specified explicitly.

This is a template of the `jenkins_jobs.ini` file

```
[jenkins]
user=carl.lama
# Get the APIKEY from the CI web UI, click top right Profile -> Configure -> Show API Key
# https://JENKINS_URL.tld/user/carl.lama/configure
password=API_KEY_NOT_YOUR_PASSWORD
url=https://JENKINS_URL.tld
query_plugins_info=False
```

#### InfluxDB

`ci-artifacts` can also be used with the [InfluxDB plugin](https://plugins.jenkins.io/influxdb/) storing a history of job builds, their result and used parameters. To use the InfluxDB as backend over the Jenkins job REST API, extend the above `jenkins_jobs.ini` config file with this additional section. The `port` is optional.

```
[influxdb]
password=TOKEN_NOT_YOUR_PASSWORD
url=https://INFLUXDB.tld
port=8086
```

The usage of the InfluxDB is enabled with the optional `--influxdb` argument.

Use the Groovy code snippet in a jobs to post all relevant data to the (hardcoded) bucket `job_bucket` of the org `jenkins`. It has to be used at the very beginning of the job with `build_status` set to `PROGRESS` and at the very end of the job to post another measurement with the final build result. The total job runtime will be calculated as the difference between the first and the second (last) post.

```
def buildData = [:];
params.each { paramKey, paramValue ->
    buildData[paramKey] = paramValue;
}

// === IMPORTANT ===
// use 'PROGRESS' at the begin of a job
// use 'currentBuild.result' at the closest possible end of a job
// buildData['build_status'] = 'PROGRESS';
// buildData['build_result'] = currentBuild.result;
// === IMPORTANT ===

influxDbPublisher(
    selectedTarget: "YOUR_TARGET_NAME", // Use value configured at the plugin
    customData: buildData,
    measurementName: "jenkins_job_params", // required, no custom value possible yet
);
```

### `ci-artifacts`

`ci-artifacts` is a tool for accessing and triggering (currently Jenkins only) CI job builds and
making build artifacts available locally in an efficient way (i.e. avoiding unnessessary builds by
comparing certain constraints like job parameters and time of already available builds).

Formerly it was only used to make artifacts available which is the reason for the name and some
CLI desing desicions.

#### Usage

Run `ci-artifacts --help` in general to get more details about the usage of the tool.

##### Await result

Wait for an existing and specified build to finish. Nothing is downloaded.

```sh
ci-artifacts --log-level debug \
    await-result checkmk/master/builders/build-cmk-distro-package:6066
```

The returned result is a JSON might look like

```json
{"result": "SUCCESS", "artifacts": null}
```

##### Download

Wait for an existing and specified build to finish and download the artifacts.

The destination of the artifacts can be specified with the optional argument `--out-dir` (defaults to `out`) and is relative to the base directory (`--base-dir`, defaults to current directory) used to fetch hashes of the downloaded artifacts.
The flag `--no-remove-others` can be used to keep additional files in the download directory which were not part of the download. This is like a built-in garbage collection.

```sh
ci-artifacts --log-level debug \
    download checkmk/master/builders/build-cmk-distro-package:6066 \
    --base-dir ~/my-git-projects/checkmk/master \
    --out-dir package_download \
    --no-remove-others
```

The returned result is a JSON might look like

```json
{"result": "SUCCESS", "artifacts": ["check-mk-enterprise-2.4.0-2024.10.31_0.jammy_amd64.deb"]}
```

##### Fetch

If there are no more constraints than a build has been completed successfully, `fetch` downloads a given jobs artifact, just like with `download` but for the latest build instead of a specified build number.

Pressing `CTRL+C` while the script is running will ask for confirmation, default answer is `no` and cancel the build.

```sh
ci-artifacts --log-level debug \
    fetch checkmk/master/winagt-build
```

In contrast, this is what a more detailed call might look like

```sh
ci-artifacts --log-level debug \
    fetch checkmk/master/winagt-build \
    --params EDITION=raw,DISTRO="ubuntu-22.04",CUSTOM_GIT_REF=85fa488e0a32f6ea55d8875ab9c517bdc253a8e1 \
    --params-no-check DISABLE_CACHE=false,CIPARAM_OVERRIDE_BUILD_NODE=fra001 \
    --dependency-paths agents/wnx,agents/windows,packages/cmk-agent-ctl \
    --time-constraints today \
    --base-dir ~/my-git-projects/checkmk/master \
    --out-dir package_download
```

**`--params <JOB-PARAMETERS>`**

Comma separated list of job-parameters used for identifying existing builds and
to start new ones.

**`--params-no-check <JOB-PARAMETERS>`**

Comma separated list of job-parameters used only to start a new build. These parameters are ignored during the search of an already existing build.

**`--time-constraints <SPECIFIER>`**

Check for build date constraints when looking for existing builds - currently
only `today` is taken into account.

**`--dependency-paths <PATH,..>`**

Comma separated list of relative paths to files and directories checked for
differences when looking for existing builds.

**`--omit-new-build`**

Don't start new builds, even when no matching build could be found.

**`--force-new-build`**

Don't look for existing builds, always start a new build instead.

**`--poll-sleep`**

Overwrite default poll interval checking the status of a running Jenkins job.

**`--poll-queue-sleep`**

Overwrite default poll interval checking the status of a queued Jenkins job.

##### Info

Request helpful informations about a Jenkins job.

Do use this command with care! Its output is quite massive and causes a lot of API calls towards Jenkins.

```sh
ci-artifacts --log-level debug \
    info checkmk/master/winagt-build
```

```
DD │ 2025-09-17 11:44:36 │ fetch job info for checkmk/master/winagt-build
...
DD │ 2025-09-17 11:45:30 │ fetch build log for checkmk/master/winagt-build:16263
Job('checkmk/master/winagt-build', 100 builds)
  - 16362: Build(nr=16362, running/None, started: 2025.09.17-11:32:40, took 00d:00h:00m, params={DISABLE_CACHE=False, VERSION=daily, CIPARAM_CLEANUP_WORKSPACE=0 - none, CUSTOM_GIT_REF=93ae7d1a..}, hashes={})
...
```

##### Request

Like `fetch` but with the optional parameter `--passive` which outputs the informations needed to trigger a build instead of triggering the build.
This is helpful in pipeline scripts to keep track of issuers of a build.

```sh
ci-artifacts --log-level debug \
    request checkmk/master/winagt-build \
    --params EDITION=raw,DISTRO="ubuntu-22.04",CUSTOM_GIT_REF=85fa488e0a32f6ea55d8875ab9c517bdc253a8e1 \
    --params-no-check DISABLE_CACHE=false,CIPARAM_OVERRIDE_BUILD_NODE=fra001 \
    --time-constraints today \
    --base-dir ~/my-git-projects/checkmk/master \
    --passive
```

```json
{
    "new_build":
    {
        "path": "checkmk/master/winagt-build",
        "params":
        {
            "EDITION": "raw",
            "DISTRO": "ubuntu-22.04",
            "CUSTOM_GIT_REF": "85fa488e0a32f6ea55d8875ab9c517bdc253a8e1",
            "DISABLE_CACHE": "false",
            "CIPARAM_OVERRIDE_BUILD_NODE": "fra001"
        }
    }
}
```

Without the `--passive` flag the build is triggered if no matching one is found. If a matching build with the specified parameters was found the returned JSON might look like

```json
{
    "existing":
    {
        "path": "checkmk/master/winagt-build",
        "number": 6066,
        "url": "https://JENKINS_URL.tld/job/checkmk/job/master/job/winagt-build/6066/",
        "result": "SUCCESS",
        "new_build": false
    }
}
```

##### Validate

The `validate` subcommand is a combination of several other commands. It requests, identifies a matching of triggers a new build while waiting for the build to complete. Nothing is downloaded. It has the same parameters as `fetch`.
This subcommand can be used to trigger a remote build with custom parameters or check if an existing build with these parameters passed or not.

```sh
ci-artifacts --log-level debug \
    validate checkmk/master/winagt-build \
    --params EDITION=raw,DISTRO="ubuntu-22.04",CUSTOM_GIT_REF=85fa488e0a32f6ea55d8875ab9c517bdc253a8e1 \
    --params-no-check DISABLE_CACHE=false,CIPARAM_OVERRIDE_BUILD_NODE=fra001 \
    --time-constraints today
```

```json
{"result": "SUCCESS", "artifacts": []}
```

#### Todo

- [ ] request CI build from local changes

### `job-resource-usage`

This is a tool to parse resource usage data for single containers
based on data collected by docker-shaper.
The [ndjson](https://github.com/ndjson/ndjson-spec) formatted data files can usually be found on the build nodes at `~jenkins/.docker_shaper/container-logs`.

#### Usage

```bash
job-resource-usage --before=7d --after=14d folder/with/datafiles/
```

### `lockable-resources`

`lockable-resources` is a tool for listing, locking and unlocking [lockable resources](https://plugins.jenkins.io/lockable-resources/) of Jenkins.

#### General

Run `lockable-resources --help` in general to get more details about the usage of the tool.

#### List

The `list` argument provides a JSON output of all labels and their resources.

```sh
lockable-resources -vvv list
```

```json
{"my_label": ["resouce1", "resouce2"], "other_label": ["this", "that"]}
```

#### Reserve and unreserve

The `reserve` and the `unreserve` argument require a single or a list of labels to lock or unlock.

```sh
lockable-resources -vvv \
[--fail-already-locked] \
[reserve, unreserve] first_resource second_resource third_resource
```

With the `--fail-already-locked` flag an exception can be thrown if the resource is already locked. If this flag is not set only a warning is logged.

To lock all resources with the label `my_lock` use the following simple call

```sh
lockable-resources list | \
jq -c .my_lock[] | \
xargs -I {} lockable-resources -vvv reserve {}
```

## Development & Contribution

### Setup

For active development you need to have `poetry` and `pre-commit` installed

```sh
python3 -m pip install --upgrade --user poetry pre-commit
git clone ssh://review.lan.tribe29.com:29418/checkmk_dev_tools
cd checkmk_dev_tools
pre-commit install
# if you need a specific version of Python inside your dev environment
poetry env use ~/.pyenv/versions/3.10.4/bin/python3
poetry install
```

### Workflow

Create a new changelog snippet. If no new snippets is found on a merged change
no new release will be built and published.

If the change is based on a Jira ticket, use the Jira ticket name as snippet
name otherwise use a unique name.

```sh
poetry run \
    changelog-generator \
    create .snippets/CMK-20150.md
```

After committing the snippet a changelog can be generated locally. For CI usage
the `--in-place` flag is recommended to use as it will update the existing
changelog with the collected snippets. For local usage remember to reset the
changelog file before a second run, as the version would be updated recursively
due to the way the changelog generator is working. It extracts the latest
version from the changelog file and puts the found snippets on top.

Future changes to the changelog are ignored by

```sh
git update-index --assume-unchanged changelog.md
```

```sh
poetry run \
    changelog-generator \
    changelog changelog.md \
    --snippets=.snippets \
    --in-place \
    --version-reference="https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags/"
```

Update the version of the project in all required files by calling

```sh
poetry run \
    changelog2version \
    --changelog_file changelog.md \
    --version_file cmk_dev/version.py \
    --version_file_type py \
    --additional_version_info="-rc42+$(git rev-parse HEAD)" \
    --print \
    | jq -r .info.version
```

* modify and check commits via `pre-commit run --all-files`
* after work is done locally:

  - update dependencies before/with a new release
```sh
poetry lock
```
  - build and check package locally
```sh
poetry build && \
poetry run twine check dist/* &&
python3 -m pip uninstall -y checkmk_dev_tools && \
python3 -m pip install --user dist/checkmk_dev_tools-$(grep -E "^version.?=" pyproject.toml | cut -d '"' -f 2)-py3-none-any.whl
```
  - commit, push and review the changes
```sh
git add ...
git commit -m "cmk-dev-tools: bump version, update dependencies"
```
  - test deployed packages from `test.pypi.org`. The extra index URL is required to get those dependencies from `pypi.org` which are not available from `test.pypi.org`
```sh
pip install --no-cache-dir \
    -i https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple \
    checkmk-dev-tools==<VERSION_WITH_RC>
```
  - finally merge the changes and let Jenkins create the release tag and deployment
