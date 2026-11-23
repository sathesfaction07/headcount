"""Shared fixtures and builders.

The shipped corpus is loaded once per session and used by the property tests, so editing
`data/` and breaking an invariant fails the build rather than going unnoticed.
"""

from __future__ import annotations

import random
import string
from pathlib import Path

import pytest

from multi_extract.identity import ALPHABET, is_valid, make_code
from multi_extract.models import Document, SourceKind
from multi_extract.sources import load_documents, load_truth

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="session")
def corpus():
    documents, problems = load_documents(DATA / "manifest.jsonl")
    assert not problems, problems
    return documents


@pytest.fixture(scope="session")
def truth():
    return load_truth(DATA / "truth.txt")


def document(text: str, *, source_id: str = "s1", kind: SourceKind = SourceKind.PLAIN_TEXT,
             page: int | None = None, declared_total: int | None = None) -> Document:
    return Document(
        source_id=source_id, kind=kind, text=text, page=page, declared_total=declared_total
    )


def valid_code(seed: int = 0) -> str:
    """A deterministic valid code.

    Generated rather than hard-coded, so a change to the checksum makes the tests fail loudly
    instead of leaving them asserting against stale literals that no longer validate.
    """
    rng = random.Random(seed)
    body = rng.choice(string.ascii_uppercase) + "".join(
        rng.choice(ALPHABET) for _ in range(8)
    )
    return make_code(body)


def shaped_but_invalid(seed: int = 0) -> str:
    """A token of the right shape whose check character disagrees."""
    rng = random.Random(seed)
    while True:
        token = rng.choice(string.ascii_uppercase) + "".join(
            rng.choice(ALPHABET) for _ in range(9)
        )
        if not is_valid(token):
            return token
