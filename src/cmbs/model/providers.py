"""Model providers: who answers a request when no recording does.

A provider takes a `ModelRequest` and returns the `ModelResponse` as the provider sent it; the
client checks it. Jev (TypeSafe's decision model) is the first provider, called over HTTP with
the key in `TYPESAFE_API_KEY`. Cloudflare Clef, the planned local fallback (grill log Q7), would
be a second class with the same method.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError

from cmbs.errors import Refused
from cmbs.model.types import ModelRequest, ModelResponse

TYPESAFE_URL = "https://api.typesafe.ai"
SYSTEM_ONE_PATH = "/v1/systemone"


class ProviderFailed(Refused):
    """The provider could not be called, or did not answer. Never read as an answer."""


class Provider(Protocol):
    def ask(self, request: ModelRequest) -> ModelResponse: ...


type Post = Callable[[str, bytes, dict[str, str]], bytes]


def _post(url: str, body: bytes, headers: dict[str, str]) -> bytes:  # pragma: no cover - network
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


class Jev:
    """TypeSafe's System One endpoint. `post` is replaceable so tests never touch the network."""

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
        body = json.dumps(request.model_dump(mode="json", exclude_none=True)).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            raw = self.post(self.url, body, headers)
        except urllib.error.HTTPError as error:
            raise ProviderFailed(f"the model provider answered HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ProviderFailed(f"the model provider could not be reached: {error.reason}") from (
                error
            )
        try:
            return ModelResponse.model_validate(json.loads(raw))
        except (ValueError, ValidationError) as error:
            raise ProviderFailed("the model provider sent a response that is not an answer") from (
                error
            )


def provider_for(model: str, **options: Any) -> Provider:
    """The live provider for a model, by its family."""
    if model.startswith("jev-"):
        return Jev(**options)
    raise ProviderFailed(f"no provider is configured for model {model}")
