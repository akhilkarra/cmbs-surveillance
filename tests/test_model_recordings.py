"""Recordings: the key checks, the file format, and the database replay cache."""

import datetime as dt
import json

import pytest

from cmbs.model.policy import Classification, ModelMismatch
from cmbs.model.recordings import (
    BadRecording,
    DbRecordings,
    MemoryRecordings,
    Recording,
    SyntheticRecording,
    check_recording,
    export_recordings,
    file_name,
    import_recordings,
    make_recording,
    read_file,
    write_file,
)
from cmbs.model.scripted import choice_answer, respond
from cmbs.model.tables import ModelCall
from cmbs.model.types import Choice, ModelRequest, ModelResponse, Noul, NoulAnswer
from tests.conftest import needs_db

NOW = dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC)
CHOICE = Choice(instructions="Which status?", criteria={"current": None, "late": None})
REQUEST = ModelRequest(
    model="jev-1.14.0",
    state={"comment": "The loan is late."},
    questions={"late": Noul(instructions="Is the loan late?"), "status": CHOICE},
)


def _recording(**changes) -> Recording:
    response = respond(
        REQUEST,
        {"late": NoulAnswer(noul=0.9), "status": choice_answer(CHOICE, "late", 0.8)},
    )
    made = make_recording(
        request=REQUEST,
        response=response,
        input_text="The loan is late.",
        prompt_version=3,
        classification=Classification.PUBLIC,
        settings_version=7,
        recorded_at=NOW,
        synthetic=False,
    )
    return made.model_copy(update=changes)


def test_a_made_recording_checks_out():
    recording = _recording()
    check_recording(recording)
    assert recording.model == "jev-1.14.0" and not recording.synthetic


@pytest.mark.parametrize(
    "changes",
    [
        {"call_id": "0" * 64},
        {"model": "jev-1.15.0"},
        {"prompt_version": 4},
        {"input_sha256": "0" * 64},
        {"questions_sha256": "0" * 64},
        {"state_sha256": "0" * 64},
    ],
)
def test_a_recording_that_does_not_match_its_request_is_refused(changes):
    recording = _recording(**changes)
    with pytest.raises(BadRecording) as refused:
        check_recording(recording)
    assert str(refused.value) == (
        f"recording {recording.call_id[:12]} does not match its own request"
    )


def test_a_recording_answered_by_another_model_is_refused():
    recording = _recording()
    other = recording.model_copy(
        update={"response": recording.response.model_copy(update={"model": "jev-1.15.0"})}
    )
    with pytest.raises(ModelMismatch):
        check_recording(other)


def test_a_recording_missing_an_answer_is_refused():
    recording = _recording()
    other = recording.model_copy(update={"response": ModelResponse(model="jev-1.14.0", answers={})})
    with pytest.raises(BadRecording) as refused:
        check_recording(other)
    assert str(refused.value) == (
        f"recording {recording.call_id[:12]}: the model did not answer 'late'"
    )


def test_memory_recordings_keep_the_first_answer():
    first = _recording()
    recordings = MemoryRecordings()
    assert recordings.put(first) is first
    assert recordings.put(first.model_copy(update={"settings_version": 9})) is first
    assert recordings.get(first.call_id) is first
    assert recordings.get("missing") is None


def test_files_round_trip_in_the_recording_format(tmp_path):
    recording = _recording()
    path = write_file(tmp_path / "out", recording)
    assert path.name == file_name(recording) == f"{recording.call_id[:16]}.json"
    assert json.loads(path.read_text())["synthetic"] is False
    assert read_file(path) == recording
    memory = MemoryRecordings()
    assert import_recordings(memory, tmp_path / "out") == 1
    assert memory.get(recording.call_id) == recording


def test_a_tampered_file_is_refused_on_import(tmp_path):
    recording = _recording()
    data = json.loads(recording.model_dump_json())
    data["response"]["answers"]["late"]["noul"] = 0.1  # answers are not in the key, so this reads
    data["prompt_version"] = 4  # but a changed key fact is caught
    (tmp_path / "x.json").write_text(json.dumps(data))
    with pytest.raises(BadRecording):
        import_recordings(MemoryRecordings(), tmp_path)


@needs_db
def test_the_database_cache_stores_reads_and_exports(seeded, tmp_path):
    cache = DbRecordings(seeded)
    first = _recording()
    assert cache.get(first.call_id) is None
    assert cache.put(first) == first
    kept = cache.put(first.model_copy(update={"settings_version": 9}))
    assert kept.settings_version == 7
    row = seeded.get(ModelCall, first.call_id)
    assert row is not None and (row.answered_by, row.classification) == ("jev-1.14.0", "public")
    assert cache.all() == [first]
    exported = export_recordings(seeded, tmp_path)
    assert exported.skipped_restricted == 0
    [path] = exported.written
    assert read_file(path) == first


@needs_db
def test_restricted_text_never_reaches_an_export(seeded, tmp_path):
    secret = "The sponsor plans to sell the asset in March."
    restricted = make_recording(
        request=REQUEST.model_copy(update={"state": {"comment": secret}}),
        response=_recording().response,
        input_text=secret,
        prompt_version=3,
        classification=Classification.RESTRICTED,
        settings_version=9,
        recorded_at=NOW,
    )
    cache = DbRecordings(seeded)
    cache.put(restricted)
    cache.put(_recording())
    exported = export_recordings(seeded, tmp_path)
    assert (len(exported.written), exported.skipped_restricted) == (1, 1)
    assert [path.name for path in tmp_path.iterdir()] == [exported.written[0].name]
    assert all("sponsor" not in path.read_text() for path in tmp_path.iterdir())


def test_synthetic_recordings_are_refused_on_import_and_nothing_is_stored(tmp_path):
    write_file(tmp_path, _recording())
    synthetic = _recording(synthetic=True)
    other = make_recording(
        request=REQUEST.model_copy(update={"state": {"comment": "Other."}}),
        response=synthetic.response,
        input_text="Other.",
        prompt_version=3,
        classification=Classification.PUBLIC,
        settings_version=7,
        recorded_at=NOW,
        synthetic=True,
    )
    write_file(tmp_path, other)
    memory = MemoryRecordings()
    with pytest.raises(SyntheticRecording) as refused:
        import_recordings(memory, tmp_path)
    assert str(refused.value) == (
        f"recording {other.call_id[:12]} holds synthetic answers, which are for tests only"
    )
    assert memory.stored == {}


@pytest.mark.parametrize("bad", [1.5, -0.1, float("nan"), float("inf")])
def test_a_recording_with_an_impossible_probability_is_refused(bad):
    recording = _recording()
    answers = recording.response.answers | {"late": NoulAnswer(noul=bad)}
    other = recording.model_copy(
        update={"response": recording.response.model_copy(update={"answers": answers})}
    )
    with pytest.raises(BadRecording) as refused:
        check_recording(other)
    assert str(refused.value) == (
        f"recording {recording.call_id[:12]}: 'late' was answered with a probability outside 0 to 1"
    )


@needs_db
def test_a_stored_impossible_probability_is_refused_on_replay_not_a_crash(seeded):
    from cmbs.model.client import Ask, ModelClient
    from cmbs.settings.service import in_force

    recording = _recording()
    DbRecordings(seeded).put(recording)
    row = seeded.get(ModelCall, recording.call_id)
    assert row is not None
    row.response = {
        **row.response,
        "answers": {**row.response["answers"], "late": {"type": "noul", "noul": 1.5}},
    }
    seeded.flush()
    ask = Ask(
        questions=REQUEST.questions,
        state=REQUEST.state,
        input_text="The loan is late.",
        classification=Classification.PUBLIC,
        subject="loan 2",
    )
    with pytest.raises(BadRecording):
        ModelClient(DbRecordings(seeded)).ask(ask, in_force(seeded), now=NOW)
