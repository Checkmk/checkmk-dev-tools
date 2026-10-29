## Wait for builds which left the queue but did not start yet
<!--
type: bugfix
scope: all
affected: all
-->

A pipeline build which has just left the Jenkins queue but did not start executing yet (e.g. still
loading its Jenkinsfile) reports `inProgress=false` while its result is still `null`. Such a build
was treated as completed, so `ci-artifacts await-result` (and `request`/`fetch` reusing a matching
queued build) returned immediately and failed with

```
Fatal exception: The build we started has result=None (https://ci.lan.tribe29.com/job/.../2639/)
```

although the awaited build succeeded a few minutes later. A build without a result is now never
considered completed and is awaited.
