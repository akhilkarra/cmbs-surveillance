"""The local decision model: pins, the checked snapshot, the vendored reference code, running
in this process, the Clef provider, where restricted text may go, and confidence."""

import datetime as dt
import hashlib
import importlib.util
import io
import json
import sys
import threading
import urllib.error
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmbs.model.client import Ask, ModelClient
from cmbs.model.local import (
    CLEF_FLASH_2026_10_01,
    LOCAL_MODELS,
    REFERENCE_MARKER,
    REFERENCE_MODULE,
    VENDORED,
    InProcess,
    LocalModel,
    LocalModelUnavailable,
    UnknownLocalModel,
    answer,
    best_device,
    check_snapshot,
    hub_cache,
    is_local_model,
    is_loopback,
    load_pinned,
    load_reference,
    local_model,
    snapshot,
    stage,
    systemone,
    vendored_reference_sha256,
)
from cmbs.model.policy import (
    Classification,
    MalformedAnswer,
    ModelMismatch,
    Reach,
    RestrictedText,
    UnpinnedModel,
    check_answers,
    check_may_send,
    check_pin,
    check_pinned,
    confidence,
)
from cmbs.model.providers import (
    CLEF_URL,
    Clef,
    Jev,
    ProviderFailed,
    clef,
    clef_http,
    post_direct,
    provider_for,
)
from cmbs.model.recordings import MemoryRecordings, check_recording, make_recording
from cmbs.model.scripted import ScriptedProvider, respond
from cmbs.model.types import Choice, ChoiceAnswer, ModelRequest, ModelResponse, Noul, NoulAnswer
from cmbs.settings.service import InForce
from cmbs.settings.values import Settings

MODEL = "clef-flash-2026-10-01"
REVISION = "8b2e5fd17c09fd49bc2880b805d5323ec7ff4ff3"
REFERENCE = "0e304cf7c6500e8bb59bef7e2afd2c6373f82596dfb3b57d1aa93c175e2dc3a3"
PIN = {"revision": REVISION, "reference_sha256": REFERENCE}
REQUEST = ModelRequest(
    model=MODEL,
    state={"comment": "The loan is late."},
    questions={"late": Noul(instructions="Is the loan late?")},
)
REPLY = {
    "model": MODEL,
    "answers": {"late": {"type": "noul", "noul": 0.9312}},
    "usage": {"input_tokens": 120, "output_tokens": 0},
    **PIN,
}
NOW = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)


# Pins.


def test_clef_flash_is_pinned_to_one_commit_its_files_and_its_reference_code():
    assert LOCAL_MODELS == {MODEL: CLEF_FLASH_2026_10_01}
    pinned = local_model(MODEL)
    assert (pinned.repo, pinned.revision, pinned.reference_sha256) == (
        "Cloudflare/clef-flash",
        REVISION,
        REFERENCE,
    )
    assert sorted(pinned.files) == [
        "chat_template.jinja",
        "config.json",
        "generation_config.json",
        "joint_head_config.json",
        "model.safetensors.index.json",
        "processor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ]
    assert sorted(pinned.weights) == [
        "joint_head.safetensors",
        *(f"model-0000{n}-of-00004.safetensors" for n in range(1, 5)),
    ]
    assert all(len(digest) == 64 for digest in [*pinned.files.values(), *pinned.weights.values()])


@pytest.mark.parametrize("name", ["clef-flash", "clef-flash-latest", "clef-flash-2026-10-19"])
def test_a_local_model_without_a_pin_is_unknown(name):
    assert not is_local_model(name)
    with pytest.raises(UnknownLocalModel) as refused:
        local_model(name)
    assert str(refused.value) == (
        f"no local model is pinned as {name}; pinned: clef-flash-2026-10-01"
    )


def test_a_pinned_local_model_passes_the_pin_check_and_its_aliases_do_not():
    check_pinned(MODEL)
    check_pinned("jev-1.14.0")
    for alias in ("clef-flash", "clef-flash-latest", "clef-flash-2026-10-19"):
        with pytest.raises(UnpinnedModel) as refused:
            check_pinned(alias)
        assert str(refused.value) == f"pin an exact model version, not {alias}"


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://127.0.0.1:30000", True),
        ("http://localhost:30000/", True),
        ("http://[::1]:30000", True),
        ("http://10.0.0.5:30000", False),
        ("http://127.0.0.1.example.test", False),
    ],
)
def test_a_loopback_address_is_recognised(url, loopback):
    assert is_loopback(url) is loopback


def test_the_hub_cache_is_found_as_huggingface_hub_finds_it(monkeypatch):
    home = Path.home()
    assert hub_cache({}) == home / ".cache" / "huggingface" / "hub"
    assert hub_cache({"HF_HOME": "/hf"}) == Path("/hf/hub")
    assert hub_cache({"HF_HOME": "/hf", "HF_HUB_CACHE": "/cache"}) == Path("/cache")
    assert hub_cache({"HF_HOME": "", "HF_HUB_CACHE": ""}) == home / ".cache" / "huggingface" / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", "/from-env")
    assert hub_cache() == Path("/from-env")


def test_the_snapshot_is_the_pinned_commit_in_the_cache():
    assert snapshot(CLEF_FLASH_2026_10_01, Path("/hub")) == Path(
        f"/hub/models--Cloudflare--clef-flash/snapshots/{REVISION}"
    )


# The vendored reference code.


def test_the_vendored_reference_code_is_the_pinned_upstream_file():
    data = VENDORED.read_bytes()
    header, upstream = data.split(REFERENCE_MARKER, 1)
    assert hashlib.sha256(upstream).hexdigest() == REFERENCE
    assert vendored_reference_sha256() == REFERENCE
    assert REVISION.encode() in header and REFERENCE.encode() in header
    assert b"Apache License, Version 2.0" in header


def test_the_vendored_code_is_checked_against_the_pin_before_it_is_imported(monkeypatch):
    other = LocalModel(name="x", repo="r/x", revision="abc1234", reference_sha256="0" * 64)
    monkeypatch.setitem(sys.modules, REFERENCE_MODULE, SimpleNamespace(name="fake"))
    with pytest.raises(LocalModelUnavailable) as refused:
        load_reference(other)
    assert str(refused.value) == "the vendored reference code is not the code pinned for x"
    assert load_reference(CLEF_FLASH_2026_10_01) is sys.modules[REFERENCE_MODULE]


@pytest.mark.skipif(importlib.util.find_spec("torch") is not None, reason="torch is installed")
def test_without_torch_the_reference_code_names_the_extra(monkeypatch):
    monkeypatch.delitem(sys.modules, REFERENCE_MODULE, raising=False)
    with pytest.raises(LocalModelUnavailable) as refused:
        load_reference(CLEF_FLASH_2026_10_01)
    assert str(refused.value) == "running the local model needs torch: uv sync --extra local-model"


# The checked snapshot.

SMALL = {"config.json": b'{"a": 1}', "tokenizer.json": b'{"t": 2}'}
WEIGHTS = {"model.safetensors": b"weights", "joint_head.safetensors": b"head"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache(tmp_path: Path) -> tuple[LocalModel, Path]:
    """A cache laid out as Hugging Face's: snapshot files link to blobs named by SHA-256, and
    a blob may link on to a deduplicated store."""
    model = LocalModel(
        name="clef-test-2026-10-01",
        repo="Example/clef-test",
        revision="abc1234def",
        reference_sha256=REFERENCE,
        files={name: _sha(data) for name, data in SMALL.items()},
        weights={name: _sha(data) for name, data in WEIGHTS.items()},
    )
    path = snapshot(model, tmp_path)
    path.mkdir(parents=True)
    blobs = path.parent.parent / "blobs"
    store = blobs / "store"
    store.mkdir(parents=True)
    for name, data in (SMALL | WEIGHTS).items():
        stored = store / f"deduplicated-{name}"
        stored.write_bytes(data)
        (blobs / _sha(data)).symlink_to(stored)
        (path / name).symlink_to(Path("../../blobs") / _sha(data))
    return model, path


def test_a_snapshot_as_pinned_is_checked_and_read_once(tmp_path):
    model, path = _cache(tmp_path)
    verified = check_snapshot(model, path)
    assert verified.files == SMALL
    assert {name: blob.read_bytes() for name, blob in verified.weights.items()} == WEIGHTS
    assert all(blob.name.startswith("deduplicated-") for blob in verified.weights.values())


def test_a_snapshot_missing_from_the_cache_is_unavailable(tmp_path):
    path = snapshot(CLEF_FLASH_2026_10_01, tmp_path)
    with pytest.raises(LocalModelUnavailable) as refused:
        check_snapshot(CLEF_FLASH_2026_10_01, path)
    assert str(refused.value) == (
        "Cloudflare/clef-flash at 8b2e5fd is not in the Hugging Face cache: "
        f"no chat_template.jinja in {path}"
    )


def test_a_changed_small_file_is_refused(tmp_path):
    model, path = _cache(tmp_path)
    (path / "config.json").unlink()
    (path / "config.json").write_bytes(b'{"a": 2}')
    with pytest.raises(LocalModelUnavailable) as refused:
        check_snapshot(model, path)
    assert (
        str(refused.value) == "config.json of Example/clef-test at abc1234 is not the pinned file"
    )


def test_a_missing_weight_file_is_unavailable(tmp_path):
    model, path = _cache(tmp_path)
    (path / "model.safetensors").unlink()
    with pytest.raises(LocalModelUnavailable) as refused:
        check_snapshot(model, path)
    assert str(refused.value) == (
        "Example/clef-test at abc1234 is not in the Hugging Face cache: "
        f"no model.safetensors in {path}"
    )


def test_weights_linked_to_another_blob_are_refused(tmp_path):
    model, path = _cache(tmp_path)
    (path / "model.safetensors").unlink()
    (path / "model.safetensors").symlink_to(Path("../../blobs") / _sha(b"head"))
    with pytest.raises(LocalModelUnavailable) as refused:
        check_snapshot(model, path)
    assert str(refused.value) == (
        "model.safetensors of Example/clef-test at abc1234 is not the pinned weights "
        f"(cache blob {_sha(b'head')})"
    )


def test_weights_that_are_not_a_cache_link_are_refused(tmp_path):
    model, path = _cache(tmp_path)
    (path / "model.safetensors").unlink()
    (path / "model.safetensors").write_bytes(b"weights")
    with pytest.raises(LocalModelUnavailable) as refused:
        check_snapshot(model, path)
    assert str(refused.value) == (
        "model.safetensors of Example/clef-test at abc1234 is not the pinned weights "
        "(cache blob none: not a cache link)"
    )


def test_the_checked_files_are_staged_from_the_checked_bytes(tmp_path):
    model, path = _cache(tmp_path)
    verified = check_snapshot(model, path)
    (store := path.parent.parent / "blobs" / "store" / "deduplicated-config.json").unlink()
    store.write_bytes(b"changed after the check")
    (tmp_path / "staged").mkdir()
    staged = stage(verified, tmp_path / "staged")
    assert (staged / "config.json").read_bytes() == b'{"a": 1}'
    assert not (staged / "config.json").is_symlink()
    assert (staged / "model.safetensors").resolve() == verified.weights["model.safetensors"]


# Running in this process.


def _torch(mps: bool, cuda: bool) -> SimpleNamespace:
    return SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
        cuda=SimpleNamespace(is_available=lambda: cuda),
    )


@pytest.mark.parametrize(
    ("mps", "cuda", "device"),
    [(True, True, "mps"), (False, True, "cuda"), (False, False, "cpu")],
)
def test_the_best_device_is_apples_gpu_then_cuda_then_the_cpu(mps, cuda, device):
    assert best_device(_torch(mps, cuda)) == device


CHOICE = {"type": "choice", "criteria": {"late": "Missed", "current": None, "paid": None}}


def test_an_answer_is_the_systemone_shape_unrounded():
    probabilities = {"current": 0.2, "late": 0.7000001, "paid": 0.0999999}
    assert answer(CHOICE, probabilities) == {
        "type": "choice",
        "choice": "late",
        "confidence": 0.7000001,
        "probabilities": {"late": 0.7000001, "current": 0.2, "paid": 0.0999999},
    }
    assert answer({"type": "noul"}, {"true": 0.12345678, "false": 0.87654322}) == {
        "type": "noul",
        "noul": 0.12345678,
    }


class Scores:
    """Stands in for one question's logits: moved to the CPU, made float64, softmaxed."""

    def __init__(self, probabilities: list[float]) -> None:
        self.probabilities = probabilities
        self.calls: list[str] = []

    def cpu(self):
        self.calls.append("cpu")
        return self

    def double(self):
        self.calls.append("double")
        return self

    def softmax(self, dim: int):
        assert dim == -1
        self.calls.append("softmax")
        return self

    def tolist(self) -> list[float]:
        return self.probabilities


class Reference:
    """Stands in for the vendored reference code and its loaded model."""

    def __init__(self) -> None:
        self.torch = SimpleNamespace(
            inference_mode=lambda: _Context(self), **vars(_torch(True, False))
        )
        self.in_inference = False
        self.loads: list[tuple[Path, str, bytes]] = []
        self.batches: list[list] = []
        self.scores = [Scores([0.1, 0.9]), Scores([0.25, 0.5, 0.25])]
        self.model = Model(self)

    def load_release_model(self, path: Path, *, device: str):
        self.loads.append((path, device, (path / "config.json").read_bytes()))
        return self.model, SimpleNamespace(tokenizer=SimpleNamespace(pad_token_id=7))

    def encode_record(self, tokenizer, body, *, processor):
        return SimpleNamespace(
            questions=[
                SimpleNamespace(question_id="late", option_ids=("false", "true")),
                SimpleNamespace(question_id="which", option_ids=("current", "late", "paid")),
            ]
        )

    def collate_records(self, records, pad_token_id, device):
        assert (pad_token_id, device) == (7, "mps-device")
        self.batches.append(records)
        return "batch"


class Model:
    """Stands in for the loaded model: its parameters' device, and its logits for a batch."""

    def __init__(self, reference: Reference) -> None:
        self.reference = reference

    def parameters(self):
        return iter([SimpleNamespace(device="mps-device")])

    def __call__(self, batch):
        assert batch == "batch" and self.reference.in_inference
        return [self.reference.scores]


class _Context:
    def __init__(self, reference: Reference) -> None:
        self.reference = reference

    def __enter__(self):
        self.reference.in_inference = True

    def __exit__(self, *exc):
        self.reference.in_inference = False


BODY = {
    "model": MODEL,
    "state": {"comment": "The loan is late."},
    "questions": {"late": {"type": "noul"}, "which": CHOICE},
}


def test_systemone_answers_a_batch_of_one_with_a_float64_softmax_on_the_cpu():
    reference = Reference()
    processor = SimpleNamespace(tokenizer=SimpleNamespace(pad_token_id=7))
    reply = systemone(reference, reference.model, processor, BODY)
    assert reply == {
        "model": MODEL,
        "answers": {
            "late": {"type": "noul", "noul": 0.9},
            "which": {
                "type": "choice",
                "choice": "late",
                "confidence": 0.5,
                "probabilities": {"late": 0.5, "current": 0.25, "paid": 0.25},
            },
        },
    }
    assert len(reference.batches) == 1 and len(reference.batches[0]) == 1
    assert reference.scores[0].calls == ["cpu", "double", "softmax"]
    assert reference.in_inference is False


def test_load_pinned_checks_stages_and_loads_from_the_staged_copy(tmp_path):
    model, path = _cache(tmp_path)
    reference = Reference()
    respond_ = load_pinned(model, path, None, reference=lambda pinned: reference)
    [(staged, device, config)] = reference.loads
    assert (device, config) == ("mps", b'{"a": 1}')
    assert staged != path and (staged / "model.safetensors").is_symlink()
    assert respond_(BODY)["answers"]["late"] == {"type": "noul", "noul": 0.9}


def test_load_pinned_uses_the_device_it_is_given_and_refuses_an_unchecked_snapshot(tmp_path):
    model, path = _cache(tmp_path)
    reference = Reference()
    load_pinned(model, path, "cpu", reference=lambda pinned: reference)
    assert reference.loads[0][1] == "cpu"
    (path / "tokenizer.json").unlink()
    with pytest.raises(LocalModelUnavailable):
        load_pinned(model, path, None, reference=lambda pinned: reference)
    assert len(reference.loads) == 1


def test_in_process_loads_once_and_stamps_every_answer_with_the_pin(tmp_path):
    loads: list[tuple[LocalModel, Path, str | None]] = []
    bodies: list[dict] = []

    def load(model, path, device):
        loads.append((model, path, device))
        return lambda body: bodies.append(body) or {"model": body["model"], "answers": {}}

    runner = InProcess(CLEF_FLASH_2026_10_01, cache=tmp_path, device="cpu", load=load)
    assert runner.path == snapshot(CLEF_FLASH_2026_10_01, tmp_path)
    assert runner({"model": MODEL, "n": 1}) == {"model": MODEL, "answers": {}, **PIN}
    assert runner({"model": MODEL, "n": 2}) == {"model": MODEL, "answers": {}, **PIN}
    assert loads == [(CLEF_FLASH_2026_10_01, runner.path, "cpu")]
    assert bodies == [{"model": MODEL, "n": 1}, {"model": MODEL, "n": 2}]


def test_in_process_refuses_a_body_for_another_model_before_loading(tmp_path):
    loads: list = []
    runner = InProcess(CLEF_FLASH_2026_10_01, cache=tmp_path, load=lambda *a: loads.append(a))  # type: ignore[arg-type]
    with pytest.raises(ValueError) as refused:
        runner({"model": "clef-flash"})
    assert str(refused.value) == "this process runs clef-flash-2026-10-01, not clef-flash"
    assert loads == []


def test_in_process_reads_the_default_cache_and_loads_the_pinned_way(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    runner = InProcess(CLEF_FLASH_2026_10_01)
    assert runner.path == snapshot(CLEF_FLASH_2026_10_01, tmp_path)
    assert runner.load is load_pinned


# The Clef provider.


def _stand_in(reply: dict) -> InProcess:
    return InProcess(CLEF_FLASH_2026_10_01, load=lambda *a: lambda body: dict(reply))


def test_clef_in_this_process_sends_the_body_and_reads_the_stamped_answer():
    bodies: list[dict] = []

    def load(*args):
        return lambda body: bodies.append(body) or {"model": MODEL, "answers": REPLY["answers"]}

    provider = Clef(MODEL, InProcess(CLEF_FLASH_2026_10_01, load=load))
    response = provider.ask(REQUEST)
    assert response == ModelResponse.model_validate(REPLY)
    assert (response.revision, response.reference_sha256) == (REVISION, REFERENCE)
    assert bodies == [
        {
            "model": MODEL,
            "state": {"comment": "The loan is late."},
            "questions": {"late": {"type": "noul", "instructions": "Is the loan late?"}},
        }
    ]
    assert (provider.model, provider.reach) == (CLEF_FLASH_2026_10_01, Reach.IN_PROCESS)


def test_any_other_transport_is_a_model_server():
    assert Clef(MODEL, lambda body: REPLY).reach is Reach.SERVER


def test_clef_refuses_a_model_that_is_not_pinned():
    with pytest.raises(UnknownLocalModel):
        Clef("clef-flash", lambda body: REPLY)


def test_a_request_the_model_refuses_is_a_failure():
    with pytest.raises(ProviderFailed) as refused:
        Clef(MODEL, _stand_in(REPLY)).ask(REQUEST.model_copy(update={"model": "clef-flash"}))
    assert str(refused.value) == (
        "the local model refused the request: this process runs clef-flash-2026-10-01, "
        "not clef-flash"
    )


def test_a_reply_that_is_not_an_answer_is_a_failure():
    with pytest.raises(ProviderFailed) as refused:
        Clef(MODEL, lambda body: {"model": MODEL, **PIN}).ask(REQUEST)
    assert str(refused.value) == "the local model sent a response that is not an answer"


@pytest.mark.parametrize(
    ("pin", "message"),
    [
        ({}, "clef-flash-2026-10-01 answered without its revision and reference code hash"),
        (
            {"revision": REVISION},
            "clef-flash-2026-10-01 answered without its revision and reference code hash",
        ),
        (
            {"revision": "f0b7d3c" + "0" * 33, "reference_sha256": REFERENCE},
            "clef-flash-2026-10-01 answered from revision f0b7d3c, not the pinned 8b2e5fd",
        ),
        (
            {"revision": REVISION, "reference_sha256": "1" * 64},
            "clef-flash-2026-10-01 answered with reference code 111111111111, "
            "not the pinned 0e304cf7c650",
        ),
    ],
)
def test_a_clef_server_answer_without_the_pin_is_refused(pin, message):
    reply = {"model": MODEL, "answers": REPLY["answers"], **pin}
    with pytest.raises(ModelMismatch) as refused:
        Clef(MODEL, lambda body: reply).ask(REQUEST)
    assert str(refused.value) == message


def test_jev_answers_need_no_pin():
    check_pin("jev-1.14.0", ModelResponse(model="jev-1.14.0", answers={}))


def test_the_pin_is_kept_in_the_recording_and_compared_on_replay():
    response = ModelResponse.model_validate(REPLY)
    recording = make_recording(
        request=REQUEST,
        response=response,
        input_text="The loan is late.",
        prompt_version=3,
        classification=Classification.PUBLIC,
        settings_version=8,
        recorded_at=NOW,
    )
    stored = json.loads(recording.model_dump_json())
    assert (stored["response"]["revision"], stored["response"]["reference_sha256"]) == (
        REVISION,
        REFERENCE,
    )
    check_recording(recording)
    stale = response.model_copy(update={"revision": "f0b7d3c" + "0" * 33})
    with pytest.raises(ModelMismatch):
        check_recording(recording.model_copy(update={"response": stale}))
    unpinned = response.model_copy(update={"revision": None, "reference_sha256": None})
    with pytest.raises(ModelMismatch):
        check_recording(recording.model_copy(update={"response": unpinned}))


def test_jev_responses_carry_no_pin_fields():
    dumped = ModelResponse(model="jev-1.14.0", answers={}).model_dump(mode="json")
    assert dumped == {"model": "jev-1.14.0", "answers": {}}


# A Clef server over HTTP: never through a proxy.


class _Server(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        data = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args) -> None:
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Server)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def proxy_everything(monkeypatch):
    """A proxy that refuses connections, named for every scheme, with no exemptions."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")


def test_a_clef_server_is_reached_directly_even_when_a_proxy_is_set(server, proxy_everything):
    assert clef_http(server)({"model": MODEL}) == REPLY
    assert Clef(MODEL, clef_http(server)).ask(REQUEST) == ModelResponse.model_validate(REPLY)


def test_the_direct_post_ignores_the_proxy(server, proxy_everything):
    reply = post_direct(server + "/v1/systemone", b"{}", {"Content-Type": "application/json"})
    assert json.loads(reply) == REPLY


class Post:
    def __init__(self, reply: bytes | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict, dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> bytes:
        self.calls.append((url, json.loads(body), headers))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_a_clef_server_is_posted_the_body_without_a_key():
    post = Post(json.dumps(REPLY).encode())
    assert clef_http("http://127.0.0.1:30000/", post=post)({"model": MODEL}) == REPLY
    assert post.calls == [
        (
            "http://127.0.0.1:30000/v1/systemone",
            {"model": MODEL},
            {"Content-Type": "application/json", "Accept": "application/json"},
        )
    ]


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (
            urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO()),  # type: ignore[arg-type]
            "the local model answered HTTP 503",
        ),
        (urllib.error.URLError("refused"), "the local model could not be reached: refused"),
        (b"not json", "the local model sent a response that is not an answer"),
    ],
)
def test_a_clef_server_failure_is_a_failure_not_an_answer(reply, message):
    transport = clef_http("http://127.0.0.1:30000", post=Post(reply))
    with pytest.raises(ProviderFailed) as refused:
        transport({"model": MODEL})
    assert str(refused.value) == message


def test_clef_uses_the_server_it_is_given_or_the_environment(monkeypatch):
    post = Post(json.dumps(REPLY).encode())
    clef(MODEL, url="http://localhost:30000", post=post).ask(REQUEST)
    monkeypatch.setenv(CLEF_URL, "http://127.0.0.1:30001")
    clef(MODEL, post=post).ask(REQUEST)
    assert [call[0] for call in post.calls] == [
        "http://localhost:30000/v1/systemone",
        "http://127.0.0.1:30001/v1/systemone",
    ]
    assert clef(MODEL, url="http://localhost:30000").reach is Reach.SERVER


def test_without_a_server_clef_runs_in_this_process(monkeypatch, tmp_path):
    monkeypatch.setenv(CLEF_URL, "http://127.0.0.1:30001")
    provider = clef(MODEL, url="", cache=tmp_path, device="cpu")
    assert provider.reach is Reach.IN_PROCESS
    assert isinstance(provider.transport, InProcess)
    assert provider.transport.path == snapshot(CLEF_FLASH_2026_10_01, tmp_path)
    assert provider.transport.device == "cpu"
    monkeypatch.delenv(CLEF_URL)
    assert clef(MODEL).reach is Reach.IN_PROCESS


def test_an_unpinned_local_model_has_no_provider():
    with pytest.raises(UnknownLocalModel):
        clef("clef-flash-latest")


def test_the_provider_is_chosen_by_model_family():
    assert isinstance(provider_for("jev-1.14.0", api_key="k"), Jev)
    assert isinstance(provider_for(MODEL, url="http://127.0.0.1:30000"), Clef)
    with pytest.raises(ProviderFailed) as refused:
        provider_for("clef-flash-latest")
    assert str(refused.value) == "no provider is configured for model clef-flash-latest"


def test_who_reads_what_each_provider_is_sent():
    assert Jev("k").reach is Reach.TYPESAFE
    assert ScriptedProvider().reach is Reach.TYPESAFE
    assert ScriptedProvider(reach=Reach.SERVER).reach is Reach.SERVER


# Where restricted text may go.


@pytest.mark.parametrize("zdr", [False, True])
def test_restricted_text_may_always_go_to_the_model_in_this_process(zdr):
    check_may_send(Classification.RESTRICTED, zero_data_retention=zdr, reach=Reach.IN_PROCESS)


@pytest.mark.parametrize("zdr", [False, True])
def test_restricted_text_never_goes_to_a_model_server(zdr):
    with pytest.raises(RestrictedText) as refused:
        check_may_send(Classification.RESTRICTED, zero_data_retention=zdr, reach=Reach.SERVER)
    assert str(refused.value) == (
        "restricted text cannot be sent to a model server; only the model in this process may "
        "read it"
    )


RESTRICTED = Ask(
    questions={"late": Noul(instructions="Is the loan late?")},
    state={"comment": "The loan is late."},
    input_text="The loan is late.",
    classification=Classification.RESTRICTED,
    subject="a portal comment",
)
LOCAL_SETTINGS = InForce(version=8, values=Settings(model=MODEL, zero_data_retention=True))


def test_the_client_sends_restricted_text_to_the_model_in_this_process():
    bodies: list[dict] = []

    def load(*args):
        return lambda body: bodies.append(body) or {"model": MODEL, "answers": REPLY["answers"]}

    provider = Clef(MODEL, InProcess(CLEF_FLASH_2026_10_01, load=load))
    client = ModelClient(MemoryRecordings(), provider=provider, replay_only=False)
    settings = InForce(version=8, values=Settings(model=MODEL))
    answered = client.ask(RESTRICTED, settings, now=NOW)
    assert len(bodies) == 1
    assert answered.recording.classification is Classification.RESTRICTED
    assert answered.recording.response.revision == REVISION


def test_a_loopback_clef_server_is_refused_restricted_text_even_with_zero_data_retention():
    post = Post(json.dumps(REPLY).encode())
    provider = clef(MODEL, url="http://127.0.0.1:30000", post=post)
    client = ModelClient(MemoryRecordings(), provider=provider, replay_only=False)
    with pytest.raises(RestrictedText):
        client.ask(RESTRICTED, LOCAL_SETTINGS, now=NOW)
    assert post.calls == []


def test_the_client_refuses_a_local_answer_without_the_pin():
    def answer_(request):
        return respond(request, {})  # no revision, no reference hash

    provider = ScriptedProvider(answer_, reach=Reach.IN_PROCESS)
    recordings = MemoryRecordings()
    client = ModelClient(recordings, provider=provider, replay_only=False)
    settings = InForce(version=8, values=Settings(model=MODEL))
    with pytest.raises(ModelMismatch) as refused:
        client.ask(RESTRICTED, settings, now=NOW)
    assert str(refused.value) == (
        "clef-flash-2026-10-01 answered without its revision and reference code hash"
    )
    assert recordings.stored == {}


# Confidence.

THREE = Choice(instructions="Which?", criteria={"a": None, "b": None, "c": None})


def _answer(probabilities: dict[str, float], reported: float) -> ChoiceAnswer:
    top = max(probabilities, key=probabilities.__getitem__)
    return ChoiceAnswer(choice=top, confidence=reported, probabilities=probabilities)


def test_confidence_is_jevs_measure_from_the_probabilities_not_the_reported_field():
    clef_says = _answer({"a": 0.80, "b": 0.15, "c": 0.05}, reported=0.80)
    jev_says = _answer({"a": 0.80, "b": 0.15, "c": 0.05}, reported=0.70)
    assert confidence(THREE, clef_says) == Decimal("0.70")
    assert confidence(THREE, jev_says) == Decimal("0.70")


def test_confidence_counts_the_options_asked_not_the_probabilities_sent():
    seven = Choice(instructions="Which?", criteria={str(n): None for n in range(7)})
    answer_ = _answer({"0": 0.80, "1": 0.15, "2": 0.05}, reported=0.80)
    assert confidence(seven, answer_) == Decimal("4.60") / 6


def test_a_question_of_two_options_measures_from_an_even_half():
    two = Choice(instructions="Which?", criteria={"yes": None, "no": None})
    answer_ = ChoiceAnswer(choice="yes", confidence=0.75, probabilities={"yes": 0.75, "no": 0.25})
    assert confidence(two, answer_) == Decimal("0.50")


@pytest.mark.parametrize(
    ("probabilities", "expected"),
    [
        ({"a": 1.0, "b": 0.0, "c": 0.0}, Decimal(1)),
        ({"a": 0.34, "b": 0.33, "c": 0.33}, Decimal("0.01")),
        ({"a": 0.2, "b": 0.1}, Decimal(0)),  # below an even share: no better than even
        ({}, Decimal(0)),
    ],
)
def test_confidence_runs_from_even_to_certain(probabilities, expected):
    answer_ = ChoiceAnswer(choice="a", confidence=0.5, probabilities=probabilities)
    assert confidence(THREE, answer_) == expected


@pytest.mark.parametrize("criteria", [{"only": None}, {}])
def test_a_question_of_fewer_than_two_options_has_no_confidence(criteria):
    question = Choice(instructions="Which?", criteria=criteria)
    answer_ = ChoiceAnswer(choice="only", confidence=1.0, probabilities={"only": 1.0})
    with pytest.raises(ValueError) as refused:
        confidence(question, answer_)
    assert str(refused.value) == (
        f"confidence needs two options or more; 'Which?' has {len(criteria)}"
    )


def test_jevs_live_confidences_are_its_measure_to_two_places():
    """Jev rounds its probabilities and its confidence to two places, so the measure taken
    from the rounded probabilities is within 0.01 of Jev's own field."""
    live = Path(__file__).parent / "fixtures" / "model" / "live"
    checked = 0
    for path in sorted(live.glob("*.json")):
        case = json.loads(path.read_text())
        request = ModelRequest.model_validate(case["request"])
        response = ModelResponse.model_validate(case["response"])
        for name, question in request.questions.items():
            answered = response.answers[name]
            if isinstance(question, Choice) and isinstance(answered, ChoiceAnswer):
                difference = abs(confidence(question, answered) - Decimal(str(answered.confidence)))
                assert difference <= Decimal("0.01"), (path.name, name)
                checked += 1
    assert checked == 66


# Probabilities must sum to 1.

LATE = ModelRequest(
    model=MODEL,
    state={},
    questions={"which": Choice(instructions="Which?", criteria={"late": None, "current": None})},
)


@pytest.mark.parametrize(
    "probabilities",
    [{"late": 0.7, "current": 0.3}, {"late": 0.7000005, "current": 0.3}, {"late": 1.0}],
)
def test_probabilities_that_sum_to_1_within_a_millionth_pass(probabilities):
    response = ModelResponse(
        model=MODEL,
        answers={"which": ChoiceAnswer(choice="late", confidence=0.7, probabilities=probabilities)},
    )
    check_answers(LATE, response)


@pytest.mark.parametrize(
    "probabilities",
    [
        {"late": 0.7, "current": 0.2999},  # rounded to four places, as Clef's own code does
        {"late": 0.7, "current": 0.3000011},
        {"late": 0.5},
    ],
)
def test_probabilities_that_do_not_sum_to_1_are_malformed(probabilities):
    response = ModelResponse(
        model=MODEL,
        answers={"which": ChoiceAnswer(choice="late", confidence=0.7, probabilities=probabilities)},
    )
    with pytest.raises(MalformedAnswer) as refused:
        check_answers(LATE, response)
    assert str(refused.value) == "'which' was answered with probabilities that do not sum to 1"


def test_a_noul_has_no_distribution_to_sum():
    request = ModelRequest(model=MODEL, state={}, questions={"q": Noul(instructions="Late?")})
    check_answers(request, ModelResponse(model=MODEL, answers={"q": NoulAnswer(noul=0.3)}))
