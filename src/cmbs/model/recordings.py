"""Recorded model calls: one format, kept in the database and written to files.

A `Recording` is the request, the response, and the facts it is keyed and judged on. The
database (`app.model_call`) is the replay cache a run reads and writes. The same JSON is the
file format: `export_recordings` writes one file per call, `import_recordings` reads them back,
and the test fixtures in `tests/fixtures/model/` are files of this format.

A call is stored the moment it is answered, on its own connection, and committed at once: the
replay cache is not panel data, and a paid answer must survive the rollback of the work that
asked for it (a month that fails to load after its comments were read), so a retry replays it.
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from cmbs.errors import Refused
from cmbs.model.policy import (
    Classification,
    MalformedAnswer,
    check_answered_by,
    check_answers,
    recording_key,
    request_hashes,
)
from cmbs.model.tables import ModelCall
from cmbs.model.types import ModelRequest, ModelResponse


class Recording(BaseModel):
    """One model call as recorded. `synthetic` marks answers written by hand, not by a model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1] = 1
    call_id: str
    model: str
    prompt_version: int
    questions_sha256: str
    state_sha256: str
    input_sha256: str
    classification: Classification
    settings_version: int
    recorded_at: dt.datetime
    synthetic: bool = False
    request: ModelRequest
    response: ModelResponse


class BadRecording(Refused):
    """A recording whose key does not match its content."""


class SyntheticRecording(Refused):
    """Hand-written answers outside tests."""


def make_recording(
    *,
    request: ModelRequest,
    response: ModelResponse,
    input_text: str,
    prompt_version: int,
    classification: Classification,
    settings_version: int,
    recorded_at: dt.datetime,
    synthetic: bool = False,
) -> Recording:
    hashes = request_hashes(request, input_text)
    return Recording(
        call_id=recording_key(model=request.model, prompt_version=prompt_version, **hashes),
        model=request.model,
        prompt_version=prompt_version,
        classification=classification,
        settings_version=settings_version,
        recorded_at=recorded_at,
        synthetic=synthetic,
        request=request,
        response=response,
        questions_sha256=hashes["questions_sha256"],
        state_sha256=hashes["state_sha256"],
        input_sha256=hashes["input_sha256"],
    )


def check_recording(recording: Recording) -> None:
    """The key matches the content, the asked model answered, and every question has an answer.

    The input text itself is not stored, so its hash is taken as recorded.
    """
    dumped = request_hashes(recording.request, "")
    expected = recording_key(
        model=recording.request.model,
        prompt_version=recording.prompt_version,
        questions_sha256=dumped["questions_sha256"],
        state_sha256=dumped["state_sha256"],
        input_sha256=recording.input_sha256,
    )
    if (
        recording.model != recording.request.model
        or recording.questions_sha256 != dumped["questions_sha256"]
        or recording.state_sha256 != dumped["state_sha256"]
        or recording.call_id != expected
    ):
        raise BadRecording(f"recording {recording.call_id[:12]} does not match its own request")
    check_answered_by(recording.model, recording.response.model)
    try:
        check_answers(recording.request, recording.response)
    except MalformedAnswer as error:
        raise BadRecording(f"recording {recording.call_id[:12]}: {error}") from None


class Recordings(Protocol):
    def get(self, key: str) -> Recording | None: ...

    def put(self, recording: Recording) -> Recording:
        """Store a recording; if one is already stored under its key, keep and return that."""
        ...


class MemoryRecordings:
    """Recordings in a dict, for tests that need no database."""

    def __init__(self, recordings: list[Recording] | None = None) -> None:
        self.stored = {recording.call_id: recording for recording in recordings or []}

    def get(self, key: str) -> Recording | None:
        return self.stored.get(key)

    def put(self, recording: Recording) -> Recording:
        return self.stored.setdefault(recording.call_id, recording)


def _from_row(row: ModelCall) -> Recording:
    return Recording(
        call_id=row.call_id,
        model=row.model,
        prompt_version=row.prompt_version,
        questions_sha256=row.questions_sha256,
        state_sha256=row.state_sha256,
        input_sha256=row.input_sha256,
        classification=Classification(row.classification),
        settings_version=row.settings_version,
        recorded_at=row.recorded_at,
        synthetic=row.synthetic,
        request=ModelRequest.model_validate(row.request),
        response=ModelResponse.model_validate(row.response),
    )


_cache_engines: dict[str, Engine] = {}


def _cache_engine(db: Session) -> Engine:
    """An engine on the caller's database for committing calls apart from its transaction."""
    url = db.get_bind().engine.url
    key = url.render_as_string(hide_password=False)
    if key not in _cache_engines:
        _cache_engines[key] = create_engine(url, poolclass=NullPool)
    return _cache_engines[key]


class DbRecordings:
    """The replay cache in `app.model_call`: read in the caller's transaction, written and
    committed on a connection of its own, so a stored answer outlives the caller's rollback."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, key: str) -> Recording | None:
        row = self.db.get(ModelCall, key)
        return None if row is None else _from_row(row)

    def put(self, recording: Recording) -> Recording:
        # Two runs may record the same request at once; the first stored answer is the one
        # both use, so they agree. Committed apart from the caller's transaction (see above).
        with Session(_cache_engine(self.db)) as separate:
            separate.execute(
                insert(ModelCall)
                .values(
                    call_id=recording.call_id,
                    model=recording.model,
                    answered_by=recording.response.model,
                    prompt_version=recording.prompt_version,
                    questions_sha256=recording.questions_sha256,
                    state_sha256=recording.state_sha256,
                    input_sha256=recording.input_sha256,
                    classification=recording.classification.value,
                    settings_version=recording.settings_version,
                    recorded_at=recording.recorded_at,
                    synthetic=recording.synthetic,
                    request=recording.request.model_dump(mode="json"),
                    response=recording.response.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=[ModelCall.call_id])
            )
            separate.commit()
        stored = self.db.get(ModelCall, recording.call_id, populate_existing=True)
        if stored is None:  # pragma: no cover - the insert above guarantees the row
            raise RuntimeError(f"recording {recording.call_id} vanished after insert")
        return _from_row(stored)

    def all(self) -> list[Recording]:
        rows = self.db.scalars(select(ModelCall).order_by(ModelCall.recorded_at, ModelCall.call_id))
        return [_from_row(row) for row in rows]


def read_file(path: Path) -> Recording:
    recording = Recording.model_validate_json(path.read_text())
    check_recording(recording)
    return recording


def file_name(recording: Recording) -> str:
    return f"{recording.call_id[:16]}.json"


def write_file(directory: Path, recording: Recording) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_name(recording)
    path.write_text(recording.model_dump_json(indent=2) + "\n")
    return path


def check_not_synthetic(recording: Recording) -> None:
    if recording.synthetic:
        raise SyntheticRecording(
            f"recording {recording.call_id[:12]} holds synthetic answers, which are for tests only"
        )


def import_recordings(recordings: Recordings, directory: Path) -> int:
    """Store every recording file (`*.json`) in `directory`; returns how many were read.

    Every file is checked before any is stored. Synthetic recordings are refused: answers
    written by hand must never reach the panel. Tests load their fixtures themselves.
    """
    read = [read_file(path) for path in sorted(directory.glob("*.json"))]
    for recording in read:
        check_not_synthetic(recording)
    for recording in read:
        recordings.put(recording)
    return len(read)


@dataclass(frozen=True)
class Exported:
    written: list[Path]
    skipped_restricted: int


def export_recordings(db: Session, directory: Path) -> Exported:
    """Write every recording of public text to `directory`, one file each.

    Recordings of restricted text stay in the database: their requests hold the text itself,
    which must not be copied to files that can travel.
    """
    stored = DbRecordings(db).all()
    public = [r for r in stored if r.classification is Classification.PUBLIC]
    return Exported(
        written=[write_file(directory, recording) for recording in public],
        skipped_restricted=len(stored) - len(public),
    )
