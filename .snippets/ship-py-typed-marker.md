## Ship the py.typed marker in the wheel
<!--
type: bugfix
scope: all
affected: all
-->

The `cmk_dev/py.typed` marker exists in the repository but was not included in
built wheels because packaging only picked up `cmk_dev/**/*.py`. Type checkers
therefore treated the installed package as untyped. The marker is now packaged
explicitly.
