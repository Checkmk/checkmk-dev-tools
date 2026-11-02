## Rename `cmk-dev` to `cmk-devops`
<!--
type: breaking
scope: all
affected: all
-->

The DevOps multi-command CLI is now called `cmk-devops`. The `cmk-dev` command
is removed; replace `cmk-dev <cmd>` with `cmk-devops <cmd>`.

This frees the `cmk-dev` name for the developer tools in `cmk-dev-site`
(`cmk-dev install`, `cmk-dev site`), which installs `checkmk-dev-tools` as a
dependency and would otherwise clash with it.

Known callers in the checkmk repo (all pin an older `checkmk-dev-tools`, so
they only break once the pin is raised):

| Call site | Branches | Replace with |
|---|---|---|
| `buildscripts/scripts/Dockerfile`: `RUN cmk-dev info` | master, 2.5.0, 2.4.0, 2.3.0 | `RUN cmk-devops info` |
| `omd/run-binreplace`: `cmk-dev binreplace "$@"` | 2.5.0, 2.4.0 | `binreplace "$@"` |
| `omd/packages/openssl/openssl.make`: `run-pipenv run cmk-dev binreplace` | 2.3.0 | `run-pipenv run binreplace` |

The standalone `binreplace` command is available in all versions, so the
`binreplace` callers can switch before the pin is raised.
