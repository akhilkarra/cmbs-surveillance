"""Model providers: who answers a request when no recording does.

A provider takes a `ModelRequest` and returns the `ModelResponse` as the provider sent it; the
client checks it. Two models answer the same `/v1/systemone` body:

- Jev, TypeSafe's decision model, over HTTP with the key in `TYPESAFE_API_KEY`;
- Clef-flash, Cloudflare's open-weight decision model, the local fallback (grill log Q7): in
  this process from the checked Hugging Face cache (`cmbs.model.local`), or from a server
  speaking `/v1/systemone` at `CMBS_CLEF_URL` (`tools/clef_server.py`).

A Clef answer must name the revision and reference-code hash of the pin
(`cmbs.model.policy.check_pin`); `tools/clef_server.py` adds them. A server that cannot report
them, such as SGLang's, is not supported. Requests to a Clef server never go through a proxy,
whatever HTTP_PROXY says.

A provider says who reads what it is sent (`reach`): the model in this process, TypeSafe, or a
model server (any Clef server, loopback included). The data-sending policy reads it
(`cmbs.model.policy.check_may_send`).

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
from cmbs.model.local import InProcess, is_local_model, local_model
from cmbs.model.policy import Reach, check_pin
from cmbs.model.types import ModelRequest, ModelResponse

TYPESAFE_URL = "https://api.typesafe.ai"
SYSTEM_ONE_PATH = "/v1/systemone"
CLEF_URL = "CMBS_CLEF_URL"


class ProviderFailed(Refused):
    """The provider could not be called, or did not answer. Never read as an answer."""


class Provider(Protocol):
    @property
    def reach(self) -> Reach:
        """Who reads the requests it is sent."""
        ...

    def ask(self, request: ModelRequest) -> ModelResponse: ...


type Post = Callable[[str, bytes, dict[str, str]], bytes]
type Transport = Callable[[dict[str, Any]], dict[str, Any]]


def _post(url: str, body: bytes, headers: dict[str, str]) -> bytes:  # pragma: no cover - network
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


# No proxy, ever: a proxy named in the environment would otherwise see the text.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_direct(url: str, body: bytes, headers: dict[str, str]) -> bytes:
    """POST to `url` itself, never through a proxy."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with _DIRECT.open(request, timeout=120) as response:
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

    reach = Reach.TYPESAFE
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


def clef_http(url: str, *, post: Post = post_direct) -> Transport:
    """A Clef server speaking `/v1/systemone` at `url`, with no key: it is the analyst's own."""
    endpoint = url.rstrip("/") + SYSTEM_ONE_PATH
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    return lambda body: _send(post, endpoint, body, headers, Clef.who)


class Clef:
    """A pinned Clef model behind a transport that takes and returns `/v1/systemone` bodies.

    The body names the pinned model ("clef-flash-2026-10-01"), and the answer must come back
    under that name with the pin's revision and reference hash. Only the model in this process
    (`InProcess`) reads text without it leaving; any other transport is a model server.
    """

    who = "the local model"

    def __init__(self, model: str, transport: Transport) -> None:
        self.model = local_model(model)
        self.transport = transport
        self.reach = Reach.IN_PROCESS if isinstance(transport, InProcess) else Reach.SERVER

    def ask(self, request: ModelRequest) -> ModelResponse:
        try:
            reply = self.transport(_body(request))
        except ValueError as error:  # a request the model or its server refuses
            raise ProviderFailed(f"{self.who} refused the request: {error}") from error
        response = _response(reply, self.who)
        check_pin(self.model.name, response)
        return response


def clef(
    model: str, *, url: str | None = None, post: Post = post_direct, **in_process: Any
) -> Clef:
    """Clef for a pinned model: from the server at `url` when one is named, otherwise in this
    process. `url=None` reads `CMBS_CLEF_URL`; `url=""` names no server, whatever it says."""
    pinned = local_model(model)
    server = url if url is not None else os.environ.get(CLEF_URL, "")
    if server:
        return Clef(model, clef_http(server, post=post))
    return Clef(model, InProcess(pinned, **in_process))


def provider_for(model: str, **options: Any) -> Provider:
    """The live provider for a model, by its family."""
    if model.startswith("jev-"):
        return Jev(**options)
    if is_local_model(model):
        return clef(model, **options)
    raise ProviderFailed(f"no provider is configured for model {model}")
