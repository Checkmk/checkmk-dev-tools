# Checkmk Development Tools

This repository includes scripts/tools for Checkmk developers.

TBD: what should go here
TBD: what shouldn't go here


## Installation

While you can just clone and use the tools inside of course (they're just plain Python or Bash
scripts), the intended way to use it is via `pip` or inside a virtual environment.

Install it locally using `pip`:

```sh
[<PYTHON> -m] pip[3] install [--user] [--upgrade] checkmk-dev-tools
```

## Contained tools

### `cmk-dev howto`

### `werk`

### `ci-artifacts`

`ci-rtifacts` is a tool which makes artifacts of CI jobs (currently only Jenkins)
locally available based on certain constraints like job parameters and time constraints.


#### Usage

Run `ci-artifacts --help` in general. Here come a few more detailed examples,
which might be outdated.

Assuming credentials are configured and you don't have any more constraints than
a build has been completed successfully, `fetch` downloads a given jobs artifacts
to a folder called `out`:
```
ci-artifacts fetch checkmk/master/winagt-build
```

In contrast, this is what a more detailed call might look like
```
ci-artifacts --log-level debug \
    --credentials url_env=JENKINS_URL,username_env=JENKINS_USERNAME,password_env=JENKINS_PASSWORD \
    fetch checkmk/master/winagt-build \
    --params EDITION=raw,DISTRO="ubuntu-22.04" \
    --params-no-check DISABLE_CACHE=false \
    --dependency-paths agents/wnx,agents/windows,packages/cmk-agent-ctl \
    --time-constraints today \
    --base-dir ~/my-git-projects/checkmk/master \
    --out-dir package_download
```

**`--credentials <CREDENTIALS>`**

Provide URL, username and password to your Jenkins instance.

**`--params <JOB-PARAMETERS>`**

Comma separated list of job-parameters used for identifying existing builds and
to start new ones.

**`--params-no-check <JOB-PARAMETERS>`**

Comma separated list of job-parameters used only to start new ones.

**`--time-constraints <SPECIFIER>`**

Check for build date constraints when looking for existing builds - currently
only `today` is taken into account.

**`--dependency-paths <PATH,..>`**

Comma separated list of relative paths to files and directories checked for
differences when looking for existing builds

**`--base-dir <PATH>`**

Path taken as repository base dir to prefix paths provided with `--dependency-paths`
as well as base dir for `--out-dir`.

**`--out-dir <PATH>`**

Artifact download destination directory.

**`--omit-new-build`**

Don't start new builds, even when no matching build could be found.

**`--force-new-build`**

Don't look for existing builds, always start a new build instead.

**`--log-level`**

Provide a Python `logging` level name, e.g. `DEBUG` (case-insensitive)


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

* (once and only for publishing to PyPi) Get token on PyPi.org
* (once and only for publishing to PyPi) `poetry config pypi-token.pypi pypi-<LONG-STRING>`
  (will write to `~/.config/pypoetry/auth.toml`)
* modify and check commits via `pre-commit`
* after work is done locally:
  - adapt version in `pyproject.toml`
  - build and check a package
```sh
poetry build && \
twine check dist/* &&
python3 -m pip uninstall -y checkmk_dev_tools && \
python3 -m pip install --user dist/checkmk_dev_tools-$(grep -E "^version.?=" pyproject.toml | cut -d '"' -f 2)-py3-none-any.whl
```
  - check installed package
  - go through review process
  - publish the new package `poetry publish --build`
  - commit new version && push
