# Checkmk Dev Tools

This repository includes helper scripts for Checkmk developers. After checking
out this repository, add it to your PATH in order to use the scripts that are
available.

Be sure to 'git pull' regularly in order to use the most up-to-date version of
the tools.

If you want to add new tools: be sure they work across different repositories.


## Installation

```sh
[<PYTHON> -m] pip[3] install [--upgrade] checkmk-dev-tools
```

## Usage

TBD

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
