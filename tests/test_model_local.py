"""The local decision model: pins, the Clef provider, the sending rule and confidence."""

import datetime as dt
import io
import json
import sys
import urllib.error
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmbs.model import local
from cmbs.model.client import Ask, ModelClient
from cmbs.model.local import (
    CLEF_FLASH_2026_10_01,
    LOCAL_MODELS,
    InProcess,
    LocalModel,
    LocalModelUnavailable,
    UnknownLocalModel,
    best_device,
    check_snapshot,
    hub_cache,
    is_local_model,
    is_loopback,
    load_reference,
    local_model,
    sha256_file,
    snapshot,
)
from cmbs.model.policy import (
    Classification,
    RestrictedText,
    UnpinnedModel,
    check_may_send,
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
    provider_for,
)
from cmbs.model.recordings import MemoryRecordings
from cmbs.model.scripted import ScriptedProvider
from cmbs.model.types import Choice, ChoiceAnswer, ModelRequest, ModelResponse, Noul
from cmbs.settings.service import InForce
from cmbs.settings.values import Settings

MODEL = "clef-flash-2026-10-01"
REVISION = "8b2e5fd17c09fd49bc2880b805d5323ec7ff4ff3"
REQUEST = ModelRequest(
    model=MODEL,
    state={"comment": "The loan is late."},
    questions={"late": Noul(instructions="Is the loan late?")},
)
REPLY = {
    "model": MODEL,
    "answers": {"late": {"type": "noul", "noul": 0.9312}},
    "usage": {"input_tokens": 120, "output_tokens": 0},
}
NOW = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)


# Pins.


def test_clef_flash_is_pinned_to_one_commit_of_its_repository():
    assert LOCAL_MODELS == {MODEL: CLEF_FLASH_2026_10_01}
    assert local_model(MODEL) == LocalModel(
        name=MODEL,
        repo="Cloudflare/clef-flash",
        revision=REVISION,
        reference_sha256="0e304cf7c6500e8bb59bef7e2afd2c6373f82596dfb3b57d1aa93c175e2dc3a3",
    )


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
        ("https://clef.example.test", False),
        ("http://127.0.0.1.example.test", False),
    ],
)
def test_only_a_loopback_address_is_this_machine(url, loopback):
    assert is_loopback(url) is loopback


def test_the_hub_cache_is_found_as_huggingface_hub_finds_it():
    home = Path.home()
    assert hub_cache({}) == home / ".cache" / "huggingface" / "hub"
    assert hub_cache({"HF_HOME": "/hf"}) == Path("/hf/hub")
    assert hub_cache({"HF_HOME": "/hf", "HF_HUB_CACHE": "/cache"}) == Path("/cache")
    assert hub_cache({"HF_HOME": "", "HF_HUB_CACHE": ""}) == home / ".cache" / "huggingface" / "hub"


def test_the_environment_is_read_when_none_is_given(monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", "/from-env")
    assert hub_cache() == Path("/from-env")


def test_the_snapshot_is_the_pinned_commit_in_the_cache():
    assert snapshot(CLEF_FLASH_2026_10_01, Path("/hub")) == Path(
        f"/hub/models--Cloudflare--clef-flash/snapshots/{REVISION}"
    )


# The snapshot and its reference code.

CODE = "VALUE = 41\n"


def _snapshot(tmp_path: Path, code: str = CODE) -> tuple[LocalModel, Path]:
    model = LocalModel(
        name="clef-test-2026-10-01",
        repo="Example/clef-test",
        revision="abc1234def",
        reference_sha256=local.hashlib.sha256(CODE.encode()).hexdigest(),
    )
    path = snapshot(model, tmp_path)
    path.mkdir(parents=True)
    (path / "joint_schema_model.py").write_text(code)
    return model, path


def test_a_file_hashes_to_its_sha256(tmp_path):
    (tmp_path / "f").write_bytes(b"abc")
    assert sha256_file(tmp_path / "f") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_a_cached_snapshot_with_the_pinned_code_passes(tmp_path):
    model, path = _snapshot(tmp_path)
    check_snapshot(model, path)


def test_a_snapshot_missing_from_the_cache_is_unavailable(tmp_path):
    model = CLEF_FLASH_2026_10_01
    path = snapshot(model, tmp_path)
    with pytest.raises(LocalModelUnavailable) as refused:
        check_snapshot(model, path)
    assert str(refused.value) == (
        f"Cloudflare/clef-flash at 8b2e5fd is not in the Hugging Face cache ({path})"
    )


def test_reference_code_that_is_not_the_pinned_code_is_refused(tmp_path):
    model, path = _snapshot(tmp_path, code="VALUE = 42\n")
    with pytest.raises(LocalModelUnavailable) as refused:
        check_snapshot(model, path)
    assert str(refused.value) == (
        "the reference code of Example/clef-test at abc1234 is not the pinned code"
    )


# Like the reference code: postponed annotations, and a dataclass, which looks its own module up
# while it is made, so the module must be registered before it runs.
DATACLASS_CODE = """from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class A:
    x: int = 1
"""


def test_the_reference_code_is_imported_from_the_snapshot(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "clef_joint_schema_model", raising=False)
    _, path = _snapshot(tmp_path, code=DATACLASS_CODE)
    module = load_reference(path)
    assert module.A().x == 1
    assert sys.modules["clef_joint_schema_model"] is module


def test_reference_code_whose_libraries_are_missing_names_the_extra(tmp_path):
    _, path = _snapshot(tmp_path, code="import torch_not_installed_here\n")
    with pytest.raises(LocalModelUnavailable) as refused:
        load_reference(path)
    assert str(refused.value) == (
        "running the local model needs torch_not_installed_here: uv sync --extra local-model"
    )
    assert "clef_joint_schema_model" not in sys.modules


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


class Reference:
    """Stands in for the reference module: records loads and answers."""

    def __init__(self, mps: bool = True) -> None:
        self.torch = _torch(mps, False)
        self.loads: list[tuple[Path, str]] = []
        self.bodies: list[dict] = []

    def load_release_model(self, path: Path, *, device: str):
        self.loads.append((path, device))
        return "model", "processor"

    def systemone(self, model, processor, body):
        assert (model, processor) == ("model", "processor")
        self.bodies.append(body)
        return {"model": body["model"], "answers": {}}


def test_in_process_loads_the_checked_snapshot_once_and_answers_each_body(tmp_path):
    model, path = _snapshot(tmp_path)
    reference = Reference()
    loaded: list[Path] = []

    def load(where: Path):
        loaded.append(where)
        return reference

    runner = InProcess(model, cache=tmp_path, load=load)  # type: ignore[arg-type]
    assert runner.path == path
    assert runner({"model": "m", "n": 1}) == {"model": "m", "answers": {}}
    assert runner({"model": "m", "n": 2}) == {"model": "m", "answers": {}}
    assert loaded == [path]
    assert reference.loads == [(path, "mps")]
    assert reference.bodies == [{"model": "m", "n": 1}, {"model": "m", "n": 2}]


def test_in_process_uses_the_device_it_is_given(tmp_path):
    model, path = _snapshot(tmp_path)
    reference = Reference()
    runner = InProcess(model, cache=tmp_path, device="cpu", load=lambda _: reference)  # type: ignore[arg-type]
    runner({"model": "m"})
    assert reference.loads == [(path, "cpu")]


def test_in_process_checks_the_snapshot_before_loading(tmp_path):
    loaded: list[Path] = []
    runner = InProcess(CLEF_FLASH_2026_10_01, cache=tmp_path, load=loaded.append)  # type: ignore[arg-type]
    with pytest.raises(LocalModelUnavailable):
        runner({"model": MODEL})
    assert loaded == []


def test_in_process_reads_the_default_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert InProcess(CLEF_FLASH_2026_10_01).path == snapshot(CLEF_FLASH_2026_10_01, tmp_path)


# The Clef provider.


class Post:
    def __init__(self, reply: bytes | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict, dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> bytes:
        self.calls.append((url, json.loads(body), headers))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_clef_sends_the_systemone_body_and_reads_the_answer():
    bodies: list[dict] = []

    def transport(body):
        bodies.append(body)
        return REPLY

    provider = Clef(MODEL, transport, local=True)
    assert provider.ask(REQUEST) == ModelResponse.model_validate(REPLY)
    assert bodies == [
        {
            "model": MODEL,
            "state": {"comment": "The loan is late."},
            "questions": {"late": {"type": "noul", "instructions": "Is the loan late?"}},
        }
    ]
    assert (provider.model, provider.local) == (CLEF_FLASH_2026_10_01, True)


def test_clef_refuses_a_model_that_is_not_pinned():
    with pytest.raises(UnknownLocalModel):
        Clef("clef-flash", lambda body: REPLY, local=True)


def test_a_request_the_reference_code_refuses_is_a_failure():
    def transport(body):
        raise ValueError("at least one question is required")

    with pytest.raises(ProviderFailed) as refused:
        Clef(MODEL, transport, local=True).ask(REQUEST)
    assert str(refused.value) == (
        "the local model refused the request: at least one question is required"
    )


def test_a_reply_that_is_not_an_answer_is_a_failure():
    with pytest.raises(ProviderFailed) as refused:
        Clef(MODEL, lambda body: {"model": MODEL}, local=True).ask(REQUEST)
    assert str(refused.value) == "the local model sent a response that is not an answer"


def test_a_clef_server_is_posted_the_body_without_a_key():
    post = Post(json.dumps(REPLY).encode())
    transport = clef_http("http://127.0.0.1:30000/", post=post)
    assert transport({"model": MODEL}) == REPLY
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


def test_clef_uses_the_server_it_is_given_and_is_local_only_on_loopback():
    post = Post(json.dumps(REPLY).encode())
    near = clef(MODEL, url="http://localhost:30000", post=post)
    assert near.local is True
    near.ask(REQUEST)
    assert post.calls[0][0] == "http://localhost:30000/v1/systemone"
    far = clef(MODEL, url="http://gpu-box.example.test:30000", post=post)
    assert far.local is False


def test_clef_reads_the_server_from_the_environment(monkeypatch):
    monkeypatch.setenv(CLEF_URL, "http://127.0.0.1:30001")
    post = Post(json.dumps(REPLY).encode())
    clef(MODEL, post=post).ask(REQUEST)
    assert post.calls[0][0] == "http://127.0.0.1:30001/v1/systemone"


def test_without_a_server_clef_runs_in_this_process(monkeypatch, tmp_path):
    monkeypatch.delenv(CLEF_URL, raising=False)
    provider = clef(MODEL, cache=tmp_path, device="cpu")
    assert provider.local is True
    assert isinstance(provider.transport, InProcess)
    assert provider.transport.path == snapshot(CLEF_FLASH_2026_10_01, tmp_path)
    assert provider.transport.device == "cpu"


def test_an_unpinned_local_model_has_no_provider():
    with pytest.raises(UnknownLocalModel):
        clef("clef-flash-latest")


def test_the_provider_is_chosen_by_model_family(monkeypatch):
    monkeypatch.delenv(CLEF_URL, raising=False)
    assert isinstance(provider_for("jev-1.14.0", api_key="k"), Jev)
    local_provider = provider_for(MODEL, url="http://127.0.0.1:30000")
    assert isinstance(local_provider, Clef) and local_provider.local
    with pytest.raises(ProviderFailed) as refused:
        provider_for("clef-flash-latest")
    assert str(refused.value) == "no provider is configured for model clef-flash-latest"


def test_jev_and_scripted_providers_are_not_local_unless_told():
    assert Jev("k").local is False
    assert ScriptedProvider().local is False
    assert ScriptedProvider(local=True).local is True


# What may be sent.


def test_restricted_text_may_go_to_a_local_provider_without_zero_data_retention():
    check_may_send(Classification.RESTRICTED, zero_data_retention=False, local=True)
    check_may_send(Classification.RESTRICTED, zero_data_retention=True, local=False)
    check_may_send(Classification.PUBLIC, zero_data_retention=False, local=False)
    with pytest.raises(RestrictedText) as refused:
        check_may_send(Classification.RESTRICTED, zero_data_retention=False)
    assert str(refused.value) == (
        "restricted text cannot be sent to the provider without zero data retention"
    )


RESTRICTED = Ask(
    questions={"late": Noul(instructions="Is the loan late?")},
    state={"comment": "The loan is late."},
    input_text="The loan is late.",
    classification=Classification.RESTRICTED,
    subject="a portal comment",
)


def test_the_client_sends_restricted_text_to_a_local_provider():
    provider = ScriptedProvider(local=True)
    client = ModelClient(MemoryRecordings(), provider=provider, replay_only=False)
    settings = InForce(version=8, values=Settings(model=MODEL))
    answered = client.ask(RESTRICTED, settings, now=NOW)
    assert len(provider.requests) == 1
    assert answered.recording.model == MODEL
    assert answered.recording.classification is Classification.RESTRICTED


def test_the_client_keeps_restricted_text_from_a_remote_provider():
    provider = ScriptedProvider(local=False)
    client = ModelClient(MemoryRecordings(), provider=provider, replay_only=False)
    settings = InForce(version=8, values=Settings(model=MODEL))
    with pytest.raises(RestrictedText):
        client.ask(RESTRICTED, settings, now=NOW)
    assert provider.requests == []


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
    answer = _answer({"0": 0.80, "1": 0.15, "2": 0.05}, reported=0.80)
    assert confidence(seven, answer) == Decimal("4.60") / 6


def test_a_question_of_two_options_measures_from_an_even_half():
    two = Choice(instructions="Which?", criteria={"yes": None, "no": None})
    answer = ChoiceAnswer(choice="yes", confidence=0.75, probabilities={"yes": 0.75, "no": 0.25})
    assert confidence(two, answer) == Decimal("0.50")


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
    answer = ChoiceAnswer(choice="a", confidence=0.5, probabilities=probabilities)
    assert confidence(THREE, answer) == expected


def test_a_question_of_one_option_is_certain():
    one = Choice(instructions="Which?", criteria={"only": None})
    answer = ChoiceAnswer(choice="only", confidence=0.4, probabilities={"only": 0.4})
    assert confidence(one, answer) == Decimal(1)


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
            answer = response.answers[name]
            if isinstance(question, Choice) and isinstance(answer, ChoiceAnswer):
                assert abs(confidence(question, answer) - Decimal(str(answer.confidence))) <= (
                    Decimal("0.01")
                ), (path.name, name)
                checked += 1
    assert checked == 66
