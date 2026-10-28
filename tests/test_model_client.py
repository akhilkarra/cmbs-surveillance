"""The model client: replay first, then (live only) a checked, recorded call."""

import datetime as dt
from decimal import Decimal

import pytest

from cmbs.model.client import Ask, ModelClient, NoRecording
from cmbs.model.policy import (
    Classification,
    MalformedAnswer,
    ModelMismatch,
    RestrictedText,
    UnpinnedModel,
    key_for,
)
from cmbs.model.recordings import BadRecording, MemoryRecordings, make_recording
from cmbs.model.scripted import ScriptedProvider, respond
from cmbs.model.types import ModelRequest, ModelResponse, Noul, NoulAnswer
from cmbs.settings.service import InForce
from cmbs.settings.values import Settings

NOW = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)
SETTINGS = InForce(version=7, values=Settings())
ASK = Ask(
    questions={"late": Noul(instructions="Is the loan late?")},
    state={"comment": "The loan is late."},
    input_text="The loan is late.",
    classification=Classification.PUBLIC,
    subject="loan 2's comment",
)
REQUEST = ModelRequest(model="jev-1.14.0", state=ASK.state, questions=ASK.questions)


def _settings(**changes) -> InForce:
    return InForce(version=9, values=Settings().model_copy(update=changes))


def _recorded(**changes) -> MemoryRecordings:
    recording = make_recording(
        request=REQUEST,
        response=ModelResponse(model="jev-1.14.0", answers={"late": NoulAnswer(noul=0.9)}),
        input_text=ASK.input_text,
        prompt_version=3,
        classification=Classification.PUBLIC,
        settings_version=7,
        recorded_at=NOW,
    )
    return MemoryRecordings([recording.model_copy(update=changes)])


def test_a_recorded_answer_is_replayed_without_a_request():
    provider = ScriptedProvider()
    client = ModelClient(_recorded(), provider=provider, replay_only=False)
    answered = client.ask(ASK, SETTINGS, now=NOW)
    assert answered.replayed
    assert answered.recording.response.answers["late"] == NoulAnswer(noul=0.9)
    assert answered.recording.call_id == key_for(
        REQUEST, prompt_version=3, input_text=ASK.input_text
    )
    assert provider.requests == []


def test_replay_without_a_recording_stops_and_names_the_text():
    provider = ScriptedProvider()
    client = ModelClient(MemoryRecordings(), provider=provider, replay_only=True)
    with pytest.raises(NoRecording) as refused:
        client.ask(ASK, SETTINGS, now=NOW)
    assert str(refused.value) == (
        "no recorded answer for this input: loan 2's comment (model jev-1.14.0, prompt version 3)"
    )
    assert provider.requests == []


def test_a_live_client_without_a_provider_can_only_replay():
    with pytest.raises(NoRecording):
        ModelClient(MemoryRecordings(), provider=None, replay_only=False).ask(
            ASK, SETTINGS, now=NOW
        )


def test_a_different_input_text_misses_the_recording():
    other = Ask(**{**ASK.__dict__, "input_text": "The loan is late. "})
    with pytest.raises(NoRecording):
        ModelClient(_recorded()).ask(other, SETTINGS, now=NOW)


def test_another_prompt_version_misses_the_recording():
    with pytest.raises(NoRecording):
        ModelClient(_recorded()).ask(ASK, _settings(prompt_version=4), now=NOW)


def test_a_live_call_is_checked_and_recorded_with_the_settings_version():
    recordings = MemoryRecordings()
    provider = ScriptedProvider(lambda request: respond(request, {"late": NoulAnswer(noul=0.8)}))
    client = ModelClient(recordings, provider=provider, replay_only=False)
    answered = client.ask(ASK, _settings(), now=NOW)
    assert not answered.replayed
    assert provider.requests == [REQUEST]
    recording = answered.recording
    assert (recording.settings_version, recording.recorded_at, recording.synthetic) == (
        9,
        NOW,
        False,
    )
    assert (recording.model, recording.prompt_version) == ("jev-1.14.0", 3)
    assert recording.classification is Classification.PUBLIC
    assert recordings.get(recording.call_id) == recording
    again = client.ask(ASK, _settings(), now=NOW)
    assert again.replayed and len(provider.requests) == 1


def test_a_floating_alias_is_refused_before_anything_else():
    provider = ScriptedProvider()
    client = ModelClient(_recorded(), provider=provider, replay_only=False)
    with pytest.raises(UnpinnedModel):
        client.ask(ASK, _settings(model="jev-latest"), now=NOW)
    assert provider.requests == []


def test_an_answer_from_another_model_is_refused_and_not_recorded():
    recordings = MemoryRecordings()
    provider = ScriptedProvider(lambda request: respond(request, {}, model="jev-1.15.0"))
    client = ModelClient(recordings, provider=provider, replay_only=False)
    with pytest.raises(ModelMismatch) as refused:
        client.ask(ASK, SETTINGS, now=NOW)
    assert str(refused.value) == "model version mismatch: asked for jev-1.14.0, got jev-1.15.0"
    assert recordings.stored == {}


def test_an_incomplete_answer_is_refused_and_not_recorded():
    recordings = MemoryRecordings()
    provider = ScriptedProvider(lambda request: ModelResponse(model="jev-1.14.0", answers={}))
    with pytest.raises(MalformedAnswer):
        ModelClient(recordings, provider=provider, replay_only=False).ask(ASK, SETTINGS, now=NOW)
    assert recordings.stored == {}


def test_a_recording_under_another_model_is_refused_on_replay():
    tampered = _recorded()
    [recording] = tampered.stored.values()
    tampered.stored[recording.call_id] = recording.model_copy(
        update={"response": recording.response.model_copy(update={"model": "jev-1.15.0"})}
    )
    with pytest.raises(ModelMismatch):
        ModelClient(tampered).ask(ASK, SETTINGS, now=NOW)


def test_a_recording_whose_content_changed_is_refused_on_replay():
    tampered = _recorded()
    [recording] = tampered.stored.values()
    tampered.stored[recording.call_id] = recording.model_copy(update={"prompt_version": 4})
    with pytest.raises(BadRecording):
        ModelClient(tampered).ask(ASK, SETTINGS, now=NOW)


RESTRICTED = Ask(**{**ASK.__dict__, "classification": Classification.RESTRICTED})


def test_restricted_text_is_not_sent_without_zero_data_retention():
    provider = ScriptedProvider()
    client = ModelClient(MemoryRecordings(), provider=provider, replay_only=False)
    with pytest.raises(RestrictedText):
        client.ask(RESTRICTED, SETTINGS, now=NOW)
    assert provider.requests == []


def test_restricted_text_is_sent_with_zero_data_retention():
    provider = ScriptedProvider()
    client = ModelClient(MemoryRecordings(), provider=provider, replay_only=False)
    answered = client.ask(RESTRICTED, _settings(zero_data_retention=True), now=NOW)
    assert len(provider.requests) == 1
    assert answered.recording.classification is Classification.RESTRICTED
    assert answered.recording.settings_version == 9


def test_a_recorded_restricted_answer_replays_without_zero_data_retention():
    recordings = _recorded(classification=Classification.RESTRICTED)
    provider = ScriptedProvider()
    client = ModelClient(recordings, provider=provider, replay_only=False)
    assert client.ask(RESTRICTED, SETTINGS, now=NOW).replayed
    assert provider.requests == []


def test_settings_default_band_is_the_baseline():
    assert (SETTINGS.values.abstention_low, SETTINGS.values.abstention_high) == (
        Decimal("0.30"),
        Decimal("0.70"),
    )
