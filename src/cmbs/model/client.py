"""Ask the model, through the replay cache.

`ModelClient.ask` looks up the recording for the exact request first. A replay-only client
stops there: a missing recording is refused, naming the text it was for. A live client sends
the request to its provider, but only if the text may be sent, then checks that the model it
asked for answered every question, and records the call before using it. Nothing a provider
returns is used unrecorded, so every answer the panel holds can be replayed.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any

from cmbs.errors import Refused
from cmbs.model.policy import (
    Classification,
    check_answered_by,
    check_answers,
    check_may_send,
    check_pinned,
    key_for,
)
from cmbs.model.providers import Provider
from cmbs.model.recordings import Recording, Recordings, check_recording, make_recording
from cmbs.model.types import ModelRequest, Question
from cmbs.settings.service import InForce


class NoRecording(Refused):
    """Replay found no recorded answer for the exact request."""


@dataclass(frozen=True)
class Ask:
    """What to ask, about which text. `subject` names the text in messages."""

    questions: dict[str, Question]
    state: dict[str, Any]
    input_text: str
    classification: Classification
    subject: str


@dataclass(frozen=True)
class Answered:
    recording: Recording
    replayed: bool


@dataclass
class ModelClient:
    """`replay_only` never calls the provider; tests always run that way."""

    recordings: Recordings
    provider: Provider | None = None
    replay_only: bool = True

    def ask(self, ask: Ask, settings: InForce, *, now: dt.datetime) -> Answered:
        model = settings.values.model
        prompt_version = settings.values.prompt_version
        check_pinned(model)
        request = ModelRequest(model=model, state=ask.state, questions=ask.questions)
        key = key_for(request, prompt_version=prompt_version, input_text=ask.input_text)
        recorded = self.recordings.get(key)
        if recorded is not None:
            check_recording(recorded)
            return Answered(recording=recorded, replayed=True)
        if self.replay_only or self.provider is None:
            raise NoRecording(
                f"no recorded answer for this input: {ask.subject} "
                f"(model {model}, prompt version {prompt_version})"
            )
        check_may_send(ask.classification, zero_data_retention=settings.values.zero_data_retention)
        response = self.provider.ask(request)
        check_answered_by(model, response.model)
        check_answers(request, response)
        recording = make_recording(
            request=request,
            response=response,
            input_text=ask.input_text,
            prompt_version=prompt_version,
            classification=ask.classification,
            settings_version=settings.version,
            recorded_at=now,
        )
        return Answered(recording=self.recordings.put(recording), replayed=False)
