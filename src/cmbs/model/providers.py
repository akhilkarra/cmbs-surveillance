"""Model providers: who answers a request when no recording does.

A provider takes a `ModelRequest` and returns the `ModelResponse` as the provider sent it; the
client checks it. Two models answer the same `/v1/systemone` body:

- Jev, TypeSafe's decision model, over HTTP with the key in `TYPESAFE_API_KEY`;
- Clef-flash, Cloudflare's open-weight decision model, the local fallback (grill log Q7): from a
  server speaking `/v1/systemone` at `CMBS_CLEF_URL` (SGLang, or `tools/clef_server.py`), or,
  without one, in this process from the Hugging Face cache (`cmbs.model.local`).

A provider says whether it is `local`: whether a request to it stays on this machine. Clef in
this process is local, and so is a Clef server on a loopback address; Jev never is. The
data-sending policy reads it (`cmbs.model.policy.check_may_send`).

Each provider's `confidence` field is kept as it was sent, but it means different things: Jev's
is `(p_max - 1/n) / (1 - 1/n)`, Clef's is `p_max`. `cmbs.model.policy.confidence` computes Jev's
measure from the probabilities, the same way for every provider.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError

from cmbs.errors import Refused
from cmbs.model.local import InProcess, is_local_model, is_loopback, local_model
from cmbs.model.types import ModelRequest, ModelResponse

TYPESAFE_URL = "https://api.typesafe.ai"
SYSTEM_ONE_PATH = "/v1/systemone"
CLEF_URL = "CMBS_CLEF_URL"


class ProviderFailed(Refused):
    """The provider could not be called, or did not answer. Never read as an answer."""


class Provider(Protocol):
    @property
    def local(self) -> bool:
        """Whether a request stays on this machine."""
        ...

    def ask(self, request: ModelRequest) -> ModelResponse: ...


type Post = Callable[[str, bytes, dict[str, str]], bytes]
type Transport = Callable[[dict[str, Any]], dict[str, Any]]


def _post(url: str, body: bytes, headers: dict[str, str]) -> bytes:  # pragma: no cover - network
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _body(request: ModelRequest) -> dict[str, Any]:
    """The `/v1/systemone` body: the request, without empty optional fields."""
    return request.model_dump(mode="json", exclude_none=True)


def _send(post: Post, url: str, body: dict[str, Any], headers: dict[str, str], who: str) -> Any:
    """POST `body` as JSON and parse the reply; any failure is ProviderFailed, never an answer."""
    try:
        raw = post(url, json.dumps(body).encode(), headers)
    except urllib.error.HTTPError as error:
        raise ProviderFailed(f"{who} answered HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise ProviderFailed(f"{who} could not be reached: {error.reason}") from error
    try:
        return json.loads(raw)
    except ValueError as error:
        raise ProviderFailed(f"{who} sent a response that is not an answer") from error


def _response(reply: Any, who: str) -> ModelResponse:
    try:
        return ModelResponse.model_validate(reply)
    except ValidationError as error:
        raise ProviderFailed(f"{who} sent a response that is not an answer") from error


class Jev:
    """TypeSafe's System One endpoint. `post` is replaceable so tests never touch the network."""

    local = False
    who = "the model provider"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        post: Post = _post,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")
        self.url = (base_url or os.environ.get("TYPESAFE_BASE_URL") or TYPESAFE_URL).rstrip(
            "/"
        ) + SYSTEM_ONE_PATH
        self.post = post

    def ask(self, request: ModelRequest) -> ModelResponse:
        if not self.api_key:
            raise ProviderFailed("set TYPESAFE_API_KEY to ask the model provider")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return _response(_send(self.post, self.url, _body(request), headers, self.who), self.who)


def clef_http(url: str, *, post: Post = _post) -> Transport:
    """A Clef server speaking `/v1/systemone` at `url`, with no key: it is the analyst's own."""
    endpoint = url.rstrip("/") + SYSTEM_ONE_PATH
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    return lambda body: _send(post, endpoint, body, headers, Clef.who)


class Clef:
    """A pinned Clef model behind a transport that takes and returns `/v1/systemone` bodies.

    The body names the pinned model ("clef-flash-2026-10-01"); the reference code answers with
    the name it was asked for, so the client's check that the asked model answered holds. A
    server that answers under another name (its repository, say) is refused by that check.
    """

    who = "the local model"

    def __init__(self, model: str, transport: Transport, *, local: bool) -> None:
        self.model = local_model(model)
        self.transport = transport
        self.local = local

    def ask(self, request: ModelRequest) -> ModelResponse:
        try:
            reply = self.transport(_body(request))
        except ValueError as error:  # the reference code's refusal of a malformed request
            raise ProviderFailed(f"{self.who} refused the request: {error}") from error
        return _response(reply, self.who)


def clef(model: str, *, url: str | None = None, post: Post = _post, **in_process: Any) -> Clef:
    """Clef for a pinned model: from the server at `url` (else `CMBS_CLEF_URL`) when one is
    named, otherwise in this process. A server is local only on a loopback address."""
    pinned = local_model(model)
    server = url if url is not None else os.environ.get(CLEF_URL, "")
    if server:
        return Clef(model, clef_http(server, post=post), local=is_loopback(server))
    return Clef(model, InProcess(pinned, **in_process), local=True)


def provider_for(model: str, **options: Any) -> Provider:
    """The live provider for a model, by its family."""
    if model.startswith("jev-"):
        return Jev(**options)
    if is_local_model(model):
        return clef(model, **options)
    raise ProviderFailed(f"no provider is configured for model {model}")
