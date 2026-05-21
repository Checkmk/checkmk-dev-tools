## Add public Python API for ci-artifacts fetch
<!--
type: feature
scope: all
affected: all
-->

`ci_artifacts` now exposes a `fetch_artifacts()` async coroutine that can be imported and called
directly without going through the CLI. `_fn_fetch` delegates to it, so CLI behaviour is unchanged.
