"""tools/clef_server.py: what it answers, and what a browser page cannot make it do."""

import http.client
import importlib.util
import json
import threading
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
MODEL = "clef-flash-2026-10-01"


def _load():
    spec = importlib.util.spec_from_file_location("clef_server", TOOLS / "clef_server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clef_server = _load()


class Answer:
    """Stands in for the model in this process: refuses other models, as InProcess does."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, body: dict) -> dict:
        if body.get("model") != MODEL:
            raise ValueError(f"this process runs {MODEL}, not {body.get('model')}")
        self.bodies.append(body)
        return {"model": MODEL, "answers": {}, "revision": "8b2e5fd"}


@pytest.fixture
def served():
    answer = Answer()
    server = clef_server.make_server(answer, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, answer
    server.shutdown()
    server.server_close()


BODY = {"model": MODEL, "state": "s", "questions": {"q": {"type": "noul"}}}


def _post(port: int, body: bytes, headers: dict[str, str], path: str = "/v1/systemone"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.putrequest("POST", path, skip_host=True)
    for name, value in headers.items():
        connection.putheader(name, value)
    connection.endheaders(body)
    response = connection.getresponse()
    status, reply = response.status, json.loads(response.read())
    connection.close()
    return status, reply


def _headers(port: int, **changes: str) -> dict[str, str]:
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Content-Type": "application/json",
    }
    return headers | changes


def test_a_request_from_this_machine_is_answered(served):
    port, answer = served
    body = json.dumps(BODY).encode()
    status, reply = _post(port, body, _headers(port, **{"Content-Length": str(len(body))}))
    assert (status, reply) == (200, {"model": MODEL, "answers": {}, "revision": "8b2e5fd"})
    assert answer.bodies == [BODY]


@pytest.mark.parametrize("host", ["localhost", "[::1]"])
def test_every_loopback_name_of_this_port_is_a_valid_host(served, host):
    port, _ = served
    body = json.dumps(BODY).encode()
    headers = _headers(port, Host=f"{host}:{port}", **{"Content-Length": str(len(body))})
    assert _post(port, body, headers)[0] == 200


@pytest.mark.parametrize(
    "host",
    ["evil.example.test:{port}", "127.0.0.1", "127.0.0.1:1", "localhost.example.test:{port}", ""],
)
def test_any_other_host_is_refused(served, host):
    """A page on another site that rebinds its name to 127.0.0.1 still sends its own Host."""
    port, answer = served
    body = json.dumps(BODY).encode()
    headers = _headers(port, Host=host.format(port=port), **{"Content-Length": str(len(body))})
    status, reply = _post(port, body, headers)
    assert (status, reply) == (403, {"error": "the Host header must name this server"})
    assert answer.bodies == []


def test_a_request_without_a_host_is_refused(served):
    port, _ = served
    body = json.dumps(BODY).encode()
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    assert _post(port, body, headers)[0] == 403


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", ""])
def test_a_body_that_is_not_json_is_refused(served, content_type):
    """A simple cross-site form or fetch cannot send application/json without a preflight."""
    port, answer = served
    body = json.dumps(BODY).encode()
    headers = _headers(port, **{"Content-Type": content_type, "Content-Length": str(len(body))})
    assert _post(port, body, headers) == (415, {"error": "send application/json"})
    assert answer.bodies == []


def test_json_with_a_charset_is_json(served):
    port, _ = served
    body = json.dumps(BODY).encode()
    headers = _headers(
        port,
        **{"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))},
    )
    assert _post(port, body, headers)[0] == 200


def test_a_body_over_one_megabyte_is_refused_unread(served):
    port, answer = served
    headers = _headers(port, **{"Content-Length": str(clef_server.MAX_BODY + 1)})
    status, reply = _post(port, b"", headers)
    assert (status, reply) == (413, {"error": "the body is over 1048576 bytes"})
    assert answer.bodies == []


def test_a_body_of_exactly_one_megabyte_is_read(served):
    port, _ = served
    body = json.dumps(BODY).encode()
    body += b" " * (clef_server.MAX_BODY - len(body))
    assert _post(port, body, _headers(port, **{"Content-Length": str(len(body))}))[0] == 200


def test_a_request_for_another_model_is_refused(served):
    port, answer = served
    body = json.dumps(BODY | {"model": "clef-flash"}).encode()
    status, reply = _post(port, body, _headers(port, **{"Content-Length": str(len(body))}))
    assert (status, reply) == (400, {"error": f"this process runs {MODEL}, not clef-flash"})
    assert answer.bodies == []


@pytest.mark.parametrize("body", [b"not json", b"{}"])
def test_a_malformed_body_is_a_bad_request(served, body):
    port, _ = served
    status, _ = _post(port, body, _headers(port, **{"Content-Length": str(len(body))}))
    assert status == 400


def test_only_the_systemone_path_is_served(served):
    port, _ = served
    body = json.dumps(BODY).encode()
    headers = _headers(port, **{"Content-Length": str(len(body))})
    assert _post(port, body, headers, path="/other") == (404, {"error": "POST /v1/systemone"})


def test_the_host_check_names_this_port_only():
    assert clef_server.allowed_host("127.0.0.1:30000", 30000)
    assert clef_server.allowed_host("localhost:30000", 30000)
    assert clef_server.allowed_host("[::1]:30000", 30000)
    assert not clef_server.allowed_host("127.0.0.1:30001", 30000)
    assert not clef_server.allowed_host(None, 30000)
