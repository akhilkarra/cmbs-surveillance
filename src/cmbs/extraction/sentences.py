"""Split a comment into numbered sentences, and check quoted evidence against them.

Splitting is deterministic code, never the model (grill log Q15). A sentence ends at ".", "!" or
"?" followed by whitespace and a capital letter, a digit or an opening quote or bracket. So
"3/6/2025", "0.71x" and "61.4%" never split (no whitespace follows their dots). A dot after
these words does not end a sentence either:

- a listed abbreviation ("U.S. Bank", "approx. 5", "Acme Inc. Holdings") or a month
  ("Dec. 31, 2024"), so "paid in Dec. The loan" stays one sentence;
- a single capital letter, an initial ("John A. Smith"). "Class A. The loan" therefore stays one
  sentence; servicer comments name people far more often than they end on a class letter;
- "No." or "Nos." only before a number ("No. 5"); "said no. The" ends;
- "St." as Saint ("in St. Louis"), which is when the word before it is not capitalised. After a
  capitalised word it is a street ("on Main St. The loan"), and the sentence may end there.

Evidence must be one whole sentence of the comment, quoted exactly once whitespace is
normalised; anything else is "evidence not found in source".
"""

import re
from dataclasses import dataclass

from cmbs.errors import Refused

EVIDENCE_NOT_FOUND = "evidence not found in source"

# Words that never end a sentence with their dot, in lower case without the dot.
ABBREVIATIONS = frozenset(
    {
        "approx",
        "co",
        "corp",
        "dr",
        "e.g",
        "i.e",
        "inc",
        "jr",
        "mr",
        "mrs",
        "ms",
        "sq",
        "sr",
        "u.s",
        "vs",
    }
)
MONTHS = frozenset(
    {"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
)
NUMBER_SIGNS = frozenset({"no", "nos"})
_BOUNDARY = re.compile(r"""[.!?]["')\]]*\s+(?=[A-Z0-9"'(\[])""")


class EvidenceNotFound(Refused):
    """Quoted evidence that is not a sentence of the comment."""


@dataclass(frozen=True)
class Sentence:
    number: int
    text: str


def normalize(text: str) -> str:
    """Runs of whitespace collapsed to one space, ends trimmed."""
    return " ".join(text.split())


def _continues(piece: str, following: str) -> bool:
    """Whether the dot ending `piece` belongs to a word, so the sentence goes on into
    `following` (the text after the boundary)."""
    words = piece.split()
    last = words[-1]
    if not last.endswith("."):
        return False
    stem = last[:-1].lower()
    if stem in ABBREVIATIONS or stem in MONTHS:
        return True
    if len(stem) == 1 and last[0].isupper():
        return True
    if stem in NUMBER_SIGNS:
        return following[:1].isdigit()
    if stem == "st":
        return len(words) == 1 or not words[-2][:1].isupper()
    return False


def split(comment: str) -> list[Sentence]:
    """The comment's sentences, numbered from 1, whitespace normalised."""
    text = normalize(comment)
    pieces: list[str] = []
    start = 0
    for boundary in _BOUNDARY.finditer(text):
        piece = text[start : boundary.end()].strip()
        if _continues(piece, text[boundary.end() :]):
            continue
        pieces.append(piece)
        start = boundary.end()
    if text[start:].strip():
        pieces.append(text[start:].strip())
    return [Sentence(number, piece) for number, piece in enumerate(pieces, start=1)]


def find(sentences: list[Sentence], evidence: str) -> Sentence:
    """The sentence `evidence` quotes exactly (after whitespace normalisation)."""
    quoted = normalize(evidence)
    for sentence in sentences:
        if sentence.text == quoted:
            return sentence
    raise EvidenceNotFound(EVIDENCE_NOT_FOUND)


def numbered(sentences: list[Sentence], number: int) -> Sentence | None:
    for sentence in sentences:
        if sentence.number == number:
            return sentence
    return None
