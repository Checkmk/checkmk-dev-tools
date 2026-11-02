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
