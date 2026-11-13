"""Shared fakes for the Moodle connector tests (offline only)."""
from lib.moodle_http import RawResponse, StreamResponse


class FakeTransport:
    """Queued transport: each call pops one RawResponse (or raises the exception)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "data": data})
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class FakeStream:
    def __init__(self, chunks, headers=None, status=200, url="https://learning.monash.edu/pluginfile.php/1/x.pdf"):
        self.chunks = list(chunks)
        self.headers = headers or {}
        self.status = status
        self.url = url
        self.calls = 0

    def __call__(self, url, headers, timeout):
        self.calls += 1
        return StreamResponse(self.status, {k.lower(): v for k, v in self.headers.items()}, iter(self.chunks), self.url)


class FakeStreamQueue:
    """Queued stream responses for redirect/download tests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers)})
        if not self.responses:
            raise AssertionError(f"unexpected stream request: {url}")
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        chunks = resp.pop("chunks", [])
        return StreamResponse(resp.get("status", 200),
                              {k.lower(): v for k, v in (resp.get("headers") or {}).items()},
                              iter(chunks), resp.get("url", url))


def ok_json(data):
    return RawResponse(200, {"content-type": "application/json"}, json_bytes([{"error": False, "data": data}]), "")


def err_json(code, message="error", status=200):
    return RawResponse(status, {"content-type": "application/json"},
                       json_bytes([{"error": True, "errorcode": code, "message": message}]), "")


def json_bytes(obj):
    import json
    return json.dumps(obj).encode()


def my_page(sesskey="s3ssk3y", userid="12345", courses=(10001, 10002), release="4.5.1 (Build: 20260701)"):
    links = "".join(f'<a href="https://learning.monash.edu/course/view.php?id={c}">c{c}</a>' for c in courses)
    return f"""<!DOCTYPE html><html><head><title>Dashboard</title></head><body>
<script>M.cfg = {{"wwwroot":"https://learning.monash.edu","sesskey":"{sesskey}","userid":"{userid}",
"release":"{release}","sitename":"Monash University"}};</script>
<div id="page-my-index">{links}</div></body></html>"""


def login_page():
    return """<!DOCTYPE html><html><body>
<form class="loginform" method="post" action="https://learning.monash.edu/login/index.php">
<input type="hidden" name="logintoken" value="abc123">
<input name="username"></form></body></html>"""
