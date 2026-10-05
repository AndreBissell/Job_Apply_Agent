"""Which question is this? Seek's id families, text normalisation and the bank key.

Evidence: docs/quick-apply-samples.md. Three id shapes are seen on the form fields
(``name="questionnaire.<id>"``):

* ``AU_Q_<n>_V_<v>``          Seek's standard library. The same ``AU_Q_<n>`` is the same
                              question on every job, so it IS the bank identity.
* ``AU_Q_<32 hex>_V_<v>``     generated per role ("years as a <role>"), new every role.
* ``indirect_<uuid>_<uuid>``  employer-written, new for every questionnaire.

The last two say nothing across jobs, so those questions are identified by a
fingerprint of what the user sees: normalised text + input type + option labels.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Literal, Sequence

IdFamily = Literal["library", "generated", "employer", "other"]

_LIBRARY = re.compile(r"^AU_Q_(\d+)_V_(\d+)$")
_GENERATED = re.compile(r"^AU_Q_[0-9A-Fa-f]{32}_V_\d+$")
_EMPLOYER = re.compile(r"^indirect_")

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


def id_family(seek_question_id: str) -> IdFamily:
    if _LIBRARY.match(seek_question_id):
        return "library"
    if _GENERATED.match(seek_question_id):
        return "generated"
    if _EMPLOYER.match(seek_question_id):
        return "employer"
    return "other"


def parse_library_id(seek_question_id: str) -> tuple[str, str] | None:
    """``AU_Q_6_V_10`` -> ``("AU_Q_6", "10")``; None for any other id family."""
    m = _LIBRARY.match(seek_question_id)
    return (f"AU_Q_{m.group(1)}", m.group(2)) if m else None


def clean_text(text: str) -> str:
    """Display form: straight quotes, single spaces, trimmed (labels carry trailing
    spaces on employer forms, "Yes ")."""
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip()


def normalise_text(text: str) -> str:
    """Comparison form: ``clean_text``, lower-cased, trailing ``?:.!`` dropped (S5 ends
    "... Android ?")."""
    return clean_text(text).lower().rstrip(" ?:.!").strip()


def fingerprint(text: str, input_type: str, option_labels: Sequence[str]) -> str:
    """32 hex chars over normalised text + type + the SORTED normalised option labels
    (so a re-ordered option list is still the same question)."""
    options = sorted(normalise_text(o) for o in option_labels)
    payload = "\x1f".join([normalise_text(text), input_type, *options])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def identity_key(seek_question_id: str, text: str, input_type: str,
                 option_labels: Sequence[str]) -> str:
    lib = parse_library_id(seek_question_id)
    if lib:
        return f"lib:{lib[0]}"
    return f"fp:{fingerprint(text, input_type, option_labels)}"
