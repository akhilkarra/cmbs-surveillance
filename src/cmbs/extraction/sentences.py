"""Split a comment into numbered sentences, and check quoted evidence against them.

Splitting is deterministic code, never the model (grill log Q15). A sentence ends at ".", "!" or
"?" followed by whitespace and a capital letter, a digit or an opening quote or bracket. So
"3/6/2025", "0.71x" and "61.4%" never split (no whitespace follows their dots), and neither do
the abbreviations servicers write ("U.S. Bank", "approx. 5", "No. 2"), which are listed.

Evidence must be one whole sentence of the comment, quoted exactly once whitespace is
normalised; anything else is "evidence not found in source".
"""

import re
from dataclasses import dataclass

from cmbs.errors import Refused

EVIDENCE_NOT_FOUND = "evidence not found in source"

# Words that end with a dot without ending a sentence, compared in lower case without the dot.
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
        "no",
        "nos",
        "sq",
        "sr",
        "st",
        "u.s",
        "vs",
    }
)
_BOUNDARY = re.compile(r"""[.!?]["')\]]*\s+(?=[A-Z0-9"'(\[])""")
_LAST_WORD = re.compile(r"(\S+)$")


class EvidenceNotFound(Refused):
    """Quoted evidence that is not a sentence of the comment."""


@dataclass(frozen=True)
class Sentence:
    number: int
    text: str


def normalize(text: str) -> str:
    """Runs of whitespace collapsed to one space, ends trimmed."""
    return " ".join(text.split())


def _ends_with_abbreviation(text: str) -> bool:
    match = _LAST_WORD.search(text)
    if match is None:
        return False
    word = match.group(1).lower()
    return word.endswith(".") and word[:-1] in ABBREVIATIONS


def split(comment: str) -> list[Sentence]:
    """The comment's sentences, numbered from 1, whitespace normalised."""
    text = normalize(comment)
    pieces: list[str] = []
    start = 0
    for boundary in _BOUNDARY.finditer(text):
        piece = text[start : boundary.end()].strip()
        if _ends_with_abbreviation(piece):
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
