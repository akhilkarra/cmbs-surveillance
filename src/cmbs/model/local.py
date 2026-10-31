"""Local decision models: what a pinned name means, and running it in this process.

Cloudflare's Clef and Clef-flash are open-weight decision models that answer the same typed
questions as Jev, through the same `/v1/systemone` body (grill log Q7; research/25). Jev's
versions are pinned by TypeSafe ("jev-1.14.0"); open weights are pinned here. A local model's
name ("clef-flash-2026-10-01") is bound to a Hugging Face repository at one commit, to the
SHA-256 of every file the model reads at that commit, and to the SHA-256 of the reference code
that runs it. A name not listed here is not pinned, so it is refused like "jev-latest".

**The reference code is vendored** (`cmbs.model.clef_reference`, Apache-2.0, unmodified below
its header); nothing is executed from the Hugging Face cache. Before the model loads:

- every small file it reads (configs, tokenizer, processor config, chat template) is read once
  and checked against its pinned SHA-256, and those bytes are what the model is given;
- every weight file must be the snapshot's link to the cache blob named by its pinned SHA-256
  (the cache names large files by their SHA-256), which is checked without reading gigabytes;
- the vendored code must hash to the pinned reference hash.

The checked files are laid out in a private directory (small files written from the checked
bytes, weights linked to their checked blobs), and the model loads from there.

`InProcess` answers a request body one request at a time: Clef's logits change with the batch
size (research/25 measured 0.36 between batch 1 and 3), so batch size 1 keeps an answer
reproducible. Probabilities are a float64 softmax of the logits, not rounded, so they sum to 1;
the reference code's own `systemone` rounds them to four places, which they would not survive
(`cmbs.model.policy.check_answers`). Every answer carries the pinned revision and reference
hash, so a recording says exactly which weights and code answered. Running it needs torch and
transformers, the `local-model` extra (`uv sync --extra local-model`).

This module needs nothing beyond the standard library until a model is loaded, so
`tools/clef_server.py` can serve it from any Python environment that has torch.
"""

import hashlib
import importlib
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from cmbs.errors import Refused

REFERENCE_MODULE = "cmbs.model.clef_reference"
VENDORED = Path(__file__).with_name("clef_reference.py")
REFERENCE_MARKER = b"# ---- upstream joint_schema_model.py follows, unmodified ----\n"
LOCAL_MODEL_EXTRA = "uv sync --extra local-model"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class LocalModel:
    """An open-weight model pinned by name: its repository and commit, the SHA-256 of each file
    the model reads (`files`) and of each weight file (`weights`), and of its reference code."""

    name: str
    repo: str
    revision: str
    reference_sha256: str
    files: Mapping[str, str] = field(default_factory=dict)
    weights: Mapping[str, str] = field(default_factory=dict)


# Clef-flash as released on 2026-10-01 (blog.cloudflare.com/clef-decision-models): the commit
# cached on this machine on 2026-10-12. Hugging Face's main branch has moved on since (research
# /25 §2.4), so the commit and the hashes, not the name, say which weights answer.
CLEF_FLASH_2026_10_01 = LocalModel(
    name="clef-flash-2026-10-01",
    repo="Cloudflare/clef-flash",
    revision="8b2e5fd17c09fd49bc2880b805d5323ec7ff4ff3",
    reference_sha256="0e304cf7c6500e8bb59bef7e2afd2c6373f82596dfb3b57d1aa93c175e2dc3a3",
    files={
        "chat_template.jinja": "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715",
        "config.json": "66f87f6fb2616b46604daf2a9c67ddc87938296d07156efa34d59b5be49e3238",
        "generation_config.json": (
            "45707f8467bf4e54e112c6095a1b2d1f2d63651cf86816344cfff21f02dda0d4"
        ),
        "joint_head_config.json": (
            "77efe959a38b5b17b241543e129e695f3c77465ece55a25985279bd8176279a0"
        ),
        "model.safetensors.index.json": (
            "941305ff9f77551e145a6cea976ef456cd5cb208cbece99f168376c752fcf96c"
        ),
        "processor_config.json": (
            "d89ef49ce9cd37fbf510158e13c1ef063d9286411c1ec9049932dbe0487143b1"
        ),
        # A file hash, not a secret: gitleaks reads "token" in the name as a key.
        "tokenizer.json": (
            "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523"  # gitleaks:allow
        ),
        "tokenizer_config.json": (
            "91a08f825d370d085d692e04cf117cdd7faad7bf18e996f1e6031b6dab03db72"
        ),
    },
    weights={
        "joint_head.safetensors": (
            "19cdcec8c81dc9212be320fff47462ab342fbc1278be4368fb3da71241cf5ba0"
        ),
        "model-00001-of-00004.safetensors": (
            "8b45a8e968141cdcc58fb71c9adfc258e2c77b5f062bc636c1fd5bc5d916b565"
        ),
        "model-00002-of-00004.safetensors": (
            "7590856c713eed844a2dcf48e6c43c4de165b788bc3f80e328311183cdbc7db8"
        ),
        "model-00003-of-00004.safetensors": (
            "e6eac2467952c33361ed7dcb3c7959d1086bbe57201cd3749c3d769fdc17fe63"
        ),
        "model-00004-of-00004.safetensors": (
            "9fcecc6556b39171238373a465f409794b7f821fb4cd1e6459e3a9c0fe317af7"
        ),
    },
)
LOCAL_MODELS: dict[str, LocalModel] = {CLEF_FLASH_2026_10_01.name: CLEF_FLASH_2026_10_01}


class UnknownLocalModel(Refused):
    """A local model name that no pin in this code binds to weights."""


class LocalModelUnavailable(Refused):
    """The pinned weights, or the libraries to run them, are not on this machine as pinned."""


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
    """Whether `url` names this machine's loopback address (which a tunnel may still forward)."""
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Verified:
    """A snapshot's files as checked: small files' bytes, and weight files' cache blobs."""

    files: dict[str, bytes]
    weights: dict[str, Path]


def _where(model: LocalModel) -> str:
    return f"{model.repo} at {model.revision[:7]}"


def check_snapshot(model: LocalModel, path: Path) -> Verified:
    """Read each small file once and check its SHA-256; check each weight file is the cache
    blob named by its pinned SHA-256. Anything missing or different is refused."""
    files: dict[str, bytes] = {}
    for name, expected in model.files.items():
        try:
            data = (path / name).read_bytes()
        except FileNotFoundError:
            raise LocalModelUnavailable(
                f"{_where(model)} is not in the Hugging Face cache: no {name} in {path}"
            ) from None
        if sha256(data) != expected:
            raise LocalModelUnavailable(f"{name} of {_where(model)} is not the pinned file")
        files[name] = data
    weights: dict[str, Path] = {}
    for name, expected in model.weights.items():
        link = path / name
        if not link.exists():
            raise LocalModelUnavailable(
                f"{_where(model)} is not in the Hugging Face cache: no {name} in {path}"
            )
        # The snapshot links each file to the blob named by its SHA-256 (the blob itself may
        # link on to a deduplicated store); the first link's name is the one the cache vouches for.
        blob = Path(os.readlink(link)).name if link.is_symlink() else "none: not a cache link"
        if blob != expected:
            raise LocalModelUnavailable(
                f"{name} of {_where(model)} is not the pinned weights (cache blob {blob})"
            )
        weights[name] = link.resolve()
    return Verified(files=files, weights=weights)


def stage(verified: Verified, directory: Path) -> Path:
    """Lay the checked files out in `directory`: small files from their checked bytes, weight
    files as links to their checked blobs. The model loads from here, not from the cache."""
    for name, data in verified.files.items():
        (directory / name).write_bytes(data)
    for name, blob in verified.weights.items():
        (directory / name).symlink_to(blob)
    return directory


def vendored_reference_sha256(path: Path = VENDORED) -> str:
    """The SHA-256 of the upstream bytes in the vendored reference code (below its header)."""
    _, upstream = path.read_bytes().split(REFERENCE_MARKER, 1)
    return sha256(upstream)


def load_reference(model: LocalModel) -> ModuleType:
    """The vendored reference code, once its upstream bytes are checked against the pin."""
    if vendored_reference_sha256() != model.reference_sha256:
        raise LocalModelUnavailable(
            f"the vendored reference code is not the code pinned for {model.name}"
        )
    try:
        return importlib.import_module(REFERENCE_MODULE)
    except ModuleNotFoundError as error:
        raise LocalModelUnavailable(
            f"running the local model needs {error.name}: {LOCAL_MODEL_EXTRA}"
        ) from error


def best_device(torch: Any) -> str:
    """Apple's GPU if there is one, else CUDA, else the CPU."""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


type Body = dict[str, Any]
type Answerer = Callable[[Body], Body]


def answer(question: Body, probabilities: Mapping[str, float]) -> Body:
    """One question's answer in the `/v1/systemone` shape, from its options' probabilities.

    As the reference code's `systemone_answer`, but not rounded. A choice's `confidence` is
    Clef's own, the top probability; it is recorded and never read
    (`cmbs.model.policy.confidence`). Only Noul and Choice are asked here."""
    if question["type"] == "noul":
        return {"type": "noul", "noul": probabilities["true"]}
    options = [str(option) for option in question["criteria"]]
    top = max(options, key=probabilities.__getitem__)
    return {
        "type": "choice",
        "choice": top,
        "confidence": probabilities[top],
        "probabilities": {option: probabilities[option] for option in options},
    }


def _softmax(scores: Any) -> list[float]:
    return scores.cpu().double().softmax(-1).tolist()


def systemone(reference: Any, model: Any, processor: Any, body: Body) -> Body:
    """Answer one request body with a loaded model, as a batch of one, through the reference
    code's encoding. Probabilities are a float64 softmax per question, taken on the CPU
    (Apple's GPU has no float64)."""
    encoded = reference.encode_record(processor.tokenizer, body, processor=processor)
    device = next(model.parameters()).device
    with reference.torch.inference_mode():
        batch = reference.collate_records([encoded], processor.tokenizer.pad_token_id, device)
        logits = model(batch)[0]
    answers = {
        question.question_id: answer(
            body["questions"][question.question_id],
            dict(zip(question.option_ids, _softmax(scores), strict=True)),
        )
        for question, scores in zip(encoded.questions, logits, strict=True)
    }
    return {"model": body["model"], "answers": answers}


def load_pinned(
    model: LocalModel,
    path: Path,
    device: str | None,
    *,
    reference: Callable[[LocalModel], Any] = load_reference,
) -> Answerer:
    """Check the snapshot at `path`, stage it, and load the model from the staged copy."""
    verified = check_snapshot(model, path)
    code = reference(model)
    staged = tempfile.TemporaryDirectory(prefix="clef-")
    loaded, processor = code.load_release_model(
        stage(verified, Path(staged.name)), device=device or best_device(code.torch)
    )

    def respond(body: Body) -> Body:
        _ = staged  # the staged files live as long as the model that reads them
        return systemone(code, loaded, processor, body)

    return respond


type Loader = Callable[[LocalModel, Path, str | None], Answerer]


class InProcess:
    """Answers `/v1/systemone` bodies with the pinned model, loaded once, at batch size 1.

    A body naming another model is refused: this process runs one pinned model. Each answer
    carries the revision and reference hash of the pin it was loaded under."""

    def __init__(
        self,
        model: LocalModel,
        *,
        cache: Path | None = None,
        device: str | None = None,
        load: Loader = load_pinned,
    ) -> None:
        self.model = model
        self.path = snapshot(model, cache if cache is not None else hub_cache())
        self.device = device
        self.load = load
        self._answer: Answerer | None = None

    def __call__(self, body: Body) -> Body:
        if body.get("model") != self.model.name:
            raise ValueError(f"this process runs {self.model.name}, not {body.get('model')}")
        if self._answer is None:
            self._answer = self.load(self.model, self.path, self.device)
        return self._answer(body) | {
            "revision": self.model.revision,
            "reference_sha256": self.model.reference_sha256,
        }
