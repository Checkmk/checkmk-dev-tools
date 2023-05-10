=================
Checkmk Dev Tools
=================

This repository includes helper scripts for Checkmk developers. After checking
out this repository, add it to your PATH in order to use the scripts that are
available.

Be sure to 'git pull' regularly in order to use the most up-to-date version of
the tools.

If you want to add new tools: be sure they work across different repositories.


Installation
------------

```sh
[<PYTHON> -m] pip[3] install [--upgrade] checkmk_dev_tools
```

Usage
-----


Development & Contribution
--------------------------

* Setup
```sh
python3 -m pip install --upgrade --user poetry pre-commit
git clone ssh://review.lan.tribe29.com:29418/checkmk_dev_tools
cd checkmk_dev_tools
pre-commit install
# if you need a specific version of Python inside your dev environment
poetry env use ~/.pyenv/versions/3.10.4/bin/python3
poetry install
```

* Workflow

```sh
poetry build && \
python3 -m pip uninstall checkmk_dev_tools && \
python3 -m pip install --user dist/checkmk_dev_tools-0.1.0-py3-none-any.whl
```
