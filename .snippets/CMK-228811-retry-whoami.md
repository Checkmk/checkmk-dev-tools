## Add retry for whoami
<!--
type: bugfix
scope: all
affected: all
-->

We see a lot of failures of `ci-artifacts` during the instantiation of `AugmentedJenkinsClient`:
```build
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/ci_artifacts.py", line 708, in _fn_request_build
    async with AugmentedJenkinsClient(
               ^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 667, in __aenter__
    return self._check_connection()
           ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 673, in _check_connection
    whoami = (self.sync_whoami())["id"]
              ^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/cmk_dev/jenkins_utils/__init__.py", line 694, in sync_whoami
    whoami = self.client.get_whoami()
             ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 891, in get_whoami
    response = self.jenkins_open(requests.Request(
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 570, in jenkins_open
    return self.jenkins_request(req, add_crumb, resolve_auth).text
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 597, in jenkins_request
    self._request(req, stream))
    ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/jenkins/__init__.py", line 563, in _request
    return self._session.send(r, **_settings)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/requests/sessions.py", line 703, in send
    r = adapter.send(request, **kwargs)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/requests/adapters.py", line 659, in send
    raise ConnectionError(err, request=request)
requests.exceptions.ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

As the underlying problem - our jenkins instance can be overloaded - is not easily resolveable, we add another `retry`.
