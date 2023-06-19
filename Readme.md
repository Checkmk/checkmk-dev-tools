# Checkmk Dev Tools

This repository includes scripts/tools for Checkmk developers.

## Installation

```sh
[<PYTHON> -m] pip[3] install [--upgrade] checkmk-dev-tools
```

## `ci-rtifacts`

`ci-rtifacts` is a tool which makes artifacts of CI jobs (currently only Jenkins) locally available
based on certain constraints like job parameters and time constraints.


## Usage

Run `ci-artifacts --help` in general. Here come a few more detailed examples, which might be outdated

Fetch the last successful artifacts of a given job `checkmk/master/winagt-build`
```
ci-artifacts fetch checkmk/master/winagt-build
```

## Development & Contribution

### Setup

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

* (once) Get token on PyPi.org
* (once) `poetry config pypi-token.pypi pypi-<LONG-STRING>`
  (will write to ~/.config/pypoetry/auth.toml)
* modify and check commits via `pre-commit`
* after work is done locally:
** adapt version in `pyproject.toml`
** build and check a package
```sh
poetry build && \
twine check dist/* &&
python3 -m pip uninstall -y checkmk_dev_tools && \
python3 -m pip install --user dist/checkmk_dev_tools-$(grep -E "^version.?=" pyproject.toml | cut -d '"' -f 2)-py3-none-any.whl
```
** check installed package
** go through review process
** publish the new package `poetry publish --build`
** commit new version && push
