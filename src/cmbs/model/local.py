"""Local decision models: which open weights a pinned name means, and running them in-process.

Cloudflare's Clef and Clef-flash are open-weight decision models that answer the same typed
questions as Jev, through the same `/v1/systemone` body (grill log Q7; research/25). Jev's
versions are pinned by TypeSafe ("jev-1.14.0"); open weights are pinned here. A local model's
name ("clef-flash-2026-10-01") is bound to a Hugging Face repository at one commit, and to the
SHA-256 of the reference code at that commit, which runs the model. A name not listed here is
not pinned, so it is refused like "jev-latest".

`InProcess` answers a request body with the reference code's `systemone`, one request at a
time: Clef's logits change with the batch size (research/25 measured 0.36 between batch 1 and
3), so batch size 1 keeps an answer reproducible. It reads the weights from the Hugging Face
cache only and never downloads; running it needs torch and transformers, the `local-model`
extra (`uv sync --extra local-model`).

This module needs nothing beyond the standard library until a model is loaded, so
`tools/clef_server.py` can serve it from any Python environment that has torch.
"""

import hashlib
import importlib.util
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from cmbs.errors import Refused

REFERENCE_CODE = "joint_schema_model.py"
LOCAL_MODEL_EXTRA = "uv sync --extra local-model"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class LocalModel:
    """An open-weight model pinned by name: its repository, commit and reference code."""

    name: str
    repo: str
    revision: str
    reference_sha256: str


# Clef-flash as released on 2026-10-01 (blog.cloudflare.com/clef-decision-models): the commit
# cached on this machine on 2026-10-12. Hugging Face's main branch has moved on since (research
# /25 §2.4), so the commit, not the name, says which weights answer.
CLEF_FLASH_2026_10_01 = LocalModel(
    name="clef-flash-2026-10-01",
    repo="Cloudflare/clef-flash",
    revision="8b2e5fd17c09fd49bc2880b805d5323ec7ff4ff3",
    reference_sha256="0e304cf7c6500e8bb59bef7e2afd2c6373f82596dfb3b57d1aa93c175e2dc3a3",
)
LOCAL_MODELS: dict[str, LocalModel] = {CLEF_FLASH_2026_10_01.name: CLEF_FLASH_2026_10_01}


class UnknownLocalModel(Refused):
    """A local model name that no pin in this code binds to weights."""


class LocalModelUnavailable(Refused):
    """The pinned weights or the code to run them are not on this machine."""


def local_model(name: str) -> LocalModel:
    pinned = LOCAL_MODELS.get(name)
    if pinned is None:
        raise UnknownLocalModel(
            f"no local model is pinned as {name}; pinned: {', '.join(sorted(LOCAL_MODELS))}"
        )
    return pinned


def is_local_model(name: str) -> bool:
    return name in LOCAL_MODELS


def is_loopback(url: str) -> bool:
    """Whether `url` names this machine, so a request to it never leaves it."""
    return urlsplit(url).hostname in LOOPBACK_HOSTS


def hub_cache(environ: Mapping[str, str] | None = None) -> Path:
    """The Hugging Face hub cache, as huggingface_hub finds it: HF_HUB_CACHE, then
    HF_HOME/hub, then ~/.cache/huggingface/hub."""
    env = os.environ if environ is None else environ
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"])
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def snapshot(model: LocalModel, cache: Path) -> Path:
    """The cached snapshot of the pinned commit."""
    return cache / f"models--{model.repo.replace('/', '--')}" / "snapshots" / model.revision


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_snapshot(model: LocalModel, path: Path) -> None:
    """The pinned commit is cached, and its reference code is the code that was pinned."""
    code = path / REFERENCE_CODE
    if not code.is_file():
        raise LocalModelUnavailable(
            f"{model.repo} at {model.revision[:7]} is not in the Hugging Face cache ({path})"
        )
    if sha256_file(code) != model.reference_sha256:
        raise LocalModelUnavailable(
            f"the reference code of {model.repo} at {model.revision[:7]} is not the pinned code"
        )


def load_reference(path: Path) -> ModuleType:
    """The reference code of a checked snapshot, imported from its file."""
    spec = importlib.util.spec_from_file_location("clef_joint_schema_model", path / REFERENCE_CODE)
    if spec is None or spec.loader is None:  # pragma: no cover - a .py file always has a loader
        raise LocalModelUnavailable(f"cannot import {path / REFERENCE_CODE}")
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses look their own module up while being made.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as error:
        del sys.modules[spec.name]
        raise LocalModelUnavailable(
            f"running the local model needs {error.name}: {LOCAL_MODEL_EXTRA}"
        ) from error
    return module


def best_device(torch: Any) -> str:
    """Apple's GPU if there is one, else CUDA, else the CPU."""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


type Body = dict[str, Any]


class InProcess:
    """Answers `/v1/systemone` bodies with the pinned model, loaded once, at batch size 1."""

    def __init__(
        self,
        model: LocalModel,
        *,
        cache: Path | None = None,
        device: str | None = None,
        load: Callable[[Path], ModuleType] = load_reference,
    ) -> None:
        self.model = model
        self.path = snapshot(model, cache if cache is not None else hub_cache())
        self.device = device
        self.load = load
        self._loaded: tuple[ModuleType, Any, Any] | None = None

    def _ready(self) -> tuple[ModuleType, Any, Any]:
        if self._loaded is None:
            check_snapshot(self.model, self.path)
            reference = self.load(self.path)
            device = self.device or best_device(reference.torch)
            model, processor = reference.load_release_model(self.path, device=device)
            self._loaded = (reference, model, processor)
        return self._loaded

    def __call__(self, body: Body) -> Body:
        reference, model, processor = self._ready()
        return reference.systemone(model, processor, body)
