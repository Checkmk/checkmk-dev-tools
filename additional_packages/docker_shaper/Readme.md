# Docker Shaper

This is a spin-off package for the `docker-shaper` tool, which can be used to monitor Docker stuff
like containers, images and volumes and automatically enforce certain cleanup-rules.


## Installation

```sh
[<PYTHON> -m] pip[3] install [--user] [--upgrade] docker-shaper
```


## Usage

```
docker-shaper serve`
```

=> Navigate to e.g. http://my-build-node:5432/


## Development & Contribution

### Prerequisites

* Python 3.8.10+ (e.g. via `pyenv`)
* `poetry` and `pre-commit`
  `python3 -m pip install --upgrade --user poetry pre-commit`

```sh
git clone https://github.com/Checkmk/checkmk-dev-tools
cd checkmk-dev-tools/additional_packages/docker_shaper
pre-commit install
# if you need a specific version of Python inside your dev environment
poetry env use ~/.pyenv/versions/3.8.10/bin/python3
poetry install
```


### Workflow

* (once and only for publishing to PyPi) Get token on PyPi.org
* (maybe) setup distinct repository setup `poetry config repositories.checkmk https://upload.pypi.org/legacy/`
* (once and only for publishing to PyPi) `poetry config pypi-token.checkmk pypi-<LONG-STRING>`
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
  - publish the new package `poetry publish --build --repository checkmk`
  - commit new version && push


## Todo

- [x] Fix: stuck auto-reload: was: postpone=True + monitoring log
- [ ] Fix: crawl-images should fix parents not having them listed
- [ ] Fix: stuck crawl images
- [ ] Fix: `'677aff0727' could not be removed: DockerError(404, 'No such image: 677aff0727:latest')`
- [ ] Fix: `tried to remove container 4f5fb0848c unknown to us`

- [x] installable via `pip install`
- [x] Quart interface (instead of `flask`)
- [x] auto-apply changes to source and configuration files
- [x] outsourced config file
- [x] bring in features of former `dgcd`
- [x] bring in features of former `dockermon`
- [x] untag certain tags
- [x] container cleanup
- [x] Fix `none` image lookup
- [x] Exceptions to messages
- [x] Clip
- [x] Increase/decrease logging via web / signal
- [x] Link: cleanup (images/containers) now
- [x] Add volumes list (with recent owners)
- [x] Containers: Store CPU / Memory usage over time
- [x] Containers: store history
- [x] Persist messages
- [ ] Show different color for unmatched images
- [ ] Warn about use of unpinned / upstream images
- [ ] Handle 'build cache objects' (found on system prune)
- [ ] Bring in volume monitoring: which volumes have been created and used by which containers?
- [ ] Containers: show total CPU usage
- [ ] Containers: list volumes
- [ ] Images: list parents / children
- [ ] Volumes: list usage
- [ ] Instructions to readme
- [ ] List unmatched / overmatched tags
- [ ] Links to `delete` / `remove`
- [ ] Links to jobs
- [ ] Link: inspect
- [ ] Graph: cpu / containers (idle/up)
- [ ] Authenticate (at least if we can modify behavior, like stopping/removing images/containers)


## Knowledge

(just misc links to articles that helped me out)

* [Showing Text Box On Hover (In Table)](https://stackoverflow.com/questions/52562345/showing-text-box-on-hover-in-table)
* [Beautiful Interactive Tables for your Flask Templates](https://blog.miguelgrinberg.com/post/beautiful-interactive-tables-for-your-flask-templates)
* https://github.com/torfsen/python-systemd-tutorial
* https://www.digitalocean.com/community/tutorials/how-to-use-templates-in-a-flask-application
* https://stackoverflow.com/questions/49957034/live-updating-dynamic-variable-on-html-with-flask
* https://pgjones.gitlab.io/quart/how_to_guides/templating.html


### Logging

* https://pgjones.gitlab.io/hypercorn/how_to_guides/logging.html#how-to-log

