"""A small HTTP client for the integration tests; standard library only.

Every answer is returned, error codes included, and redirects are not
followed, so a test sees exactly what the proxy sends.
"""

from __future__ import annotations

import base64
import json as jsonlib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any

TIMEOUT = 30


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@dataclass(frozen=True)
class Response:
    status_code: int
    headers: Message  # case-insensitive .get()
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode(errors="replace")

    def __repr__(self) -> str:  # never the content: it could hold a token
        return f"<Response {self.status_code}>"

    def json(self) -> Any:
        return jsonlib.loads(self.content)


class Client:
    def __init__(self, context: ssl.SSLContext, timeout: int = TIMEOUT):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context), _NoRedirect()
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json: Any = None,
        auth: tuple[str, str] | None = None,
    ) -> Response:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        call = urllib.request.Request(url, method=method, headers=dict(headers or {}))
        if json is not None:
            call.data = jsonlib.dumps(json).encode()
            call.add_header("Content-Type", "application/json")
        if auth is not None:
            call.add_header("Authorization", _basic(*auth))
        try:
            with self.opener.open(call, timeout=self.timeout) as answer:
                return Response(answer.status, answer.headers, answer.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.headers, error.read())

    def get(self, url: str, **kwargs) -> Response:
        return self.request("GET", url, **kwargs)

    def put(self, url: str, **kwargs) -> Response:
        return self.request("PUT", url, **kwargs)

    def post(self, url: str, **kwargs) -> Response:
        return self.request("POST", url, **kwargs)

    def delete(self, url: str, **kwargs) -> Response:
        return self.request("DELETE", url, **kwargs)

    def options(self, url: str, **kwargs) -> Response:
        return self.request("OPTIONS", url, **kwargs)


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
