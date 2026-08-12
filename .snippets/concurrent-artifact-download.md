## Fix corrupted artifacts on concurrent compressed downloads
<!--
type: bugfix
scope: all
affected: all
-->

Since version `2.4.0` artifacts of builds with more than 10 artifacts are downloaded as one
compressed archive instead of file by file. That archive was always stored as `archive.zip`
directly inside the output directory and extracted into `<out-dir>/archive`, both of which were
removed afterwards.

Several `ci-artifacts` instances may download different builds into the same output directory at
the same time, e.g. parallel Jenkins stages sharing one workspace. Those instances wrote to the
very same `archive.zip` and extraction directory, so they overwrote and deleted each others files,
resulting in errors like

```
zipfile.BadZipFile: File name in directory 'archive/test-results/almalinux-10/.../jobstatus.mk'
and header b'archive/test-results/sles-15sp7/.../jobstatus.mk' differ.
```

or in silently lost artifacts. Both the archive and the extraction directory are now unique per
invocation and are cleaned up reliably. Additionally only the files actually contained in the
archive are reported as downloaded artifacts - previously all files below the extracted top level
directories were listed, including those of other builds.
