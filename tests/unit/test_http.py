import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifdd_meta.http import Client
from ifdd_meta.keycloak import ssl_context


@pytest.fixture(scope="module")
def server():
    class Handler(BaseHTTPRequestHandler):
        def handle_any(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
                return
            status = 404 if self.path.startswith("/missing") else 200
            data = json.dumps(
                {
                    "method": self.command,
                    "path": self.path,
                    "body": raw.decode(),
                    "type": self.headers.get("Content-Type"),
                    "auth": self.headers.get("Authorization"),
                }
            ).encode()
            self.send_response(status)
            self.send_header("X-Total-Count", "7")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = handle_any

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def client():
    return Client(ssl_context(None))


def test_error_answers_are_returned(server, client):
    answer = client.get(f"{server}/missing")
    assert answer.status_code == 404
    assert answer.json()["path"] == "/missing"


def test_redirects_are_not_followed(server, client):
    answer = client.get(f"{server}/redirect")
    assert answer.status_code == 302
    assert answer.headers.get("location") == "/elsewhere"


def test_params_json_and_headers(server, client):
    answer = client.put(
        f"{server}/x", params={"at": "2026-01-01T00:00:00Z"}, json={"a": 1}, headers={"X-A": "b"}
    )
    sent = answer.json()
    assert (sent["method"], sent["path"]) == ("PUT", "/x?at=2026-01-01T00%3A00%3A00Z")
    assert (json.loads(sent["body"]), sent["type"]) == ({"a": 1}, "application/json")
    assert answer.headers.get("x-total-count") == "7"


def test_basic_auth(server, client):
    sent = client.get(f"{server}/", auth=("admin", "pw")).json()
    assert sent["auth"] == "Basic " + base64.b64encode(b"admin:pw").decode()


def test_repr_never_shows_the_content(server, client):
    answer = client.post(f"{server}/", json={"token": "abc.def.ghi"})
    assert "abc.def.ghi" in answer.text
    assert repr(answer) == "<Response 200>"
