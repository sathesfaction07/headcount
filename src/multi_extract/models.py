"""Domain types.

The type that matters is `Tier`. A token can be accepted at three different strictnesses,
and the whole point of this tool is that those three answers differ on real input. Recording
which test a token passed -- rather than just whether it was accepted -- is what makes the
precision/recall trade visible instead of being a hidden constant.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from multi_extract.identity import CODE_LENGTH


class Tier(IntEnum):
    """How strictly a candidate was accepted. Ordered, so comparison is meaningful.

    An IntEnum rather than a StrEnum because `candidate.tier >= Tier.VALIDATED` is the
    natural way to filter, and ordering is the property that makes it a tier rather than a
    label. The cost is that it compares equal to bare ints, which is a real wart -- a caller
    writing `tier == 2` gets away with it.
    """

    LOOKS_LIKE = 1  # ten alphanumerics in a row, nothing more
    SHAPED = 2      # correct length, leading letter, uppercase, on word boundaries
    VALIDATED = 3   # the check character agrees

    @property
    def label(self) -> str:
        return {1: "looks_like", 2: "shaped", 3: "validated"}[int(self)]


class SourceKind(StrEnum):
    """Where a document came from.

    Carried on every candidate because precision differs sharply by source. A CSV export
    column of item codes is almost all true positives; a free-text email body is mostly
    look-alikes. An aggregate precision figure over both describes neither, which is why
    `measure.by_source` exists.
    """

    CSV_EXPORT = "csv_export"
    HTML_LISTING = "html_listing"
    EMAIL_BODY = "email_body"
    EMAIL_ATTACHMENT = "email_attachment"
    PLAIN_TEXT = "plain_text"
    UNKNOWN = "unknown"


class Document(BaseModel):
    """One unit of text to scan, with where it came from.

    `page` and `declared_total` are the pagination story. `declared_total` is what the SOURCE
    claims it holds -- and it is optional because most sources do not say. That optionality
    is load-bearing: without a declared total, "did we get everything" is unanswerable from
    the data alone, and `core/collect.py` must report that rather than implying success.
    """

    source_id: str
    kind: SourceKind = SourceKind.UNKNOWN
    text: str = ""
    page: int | None = None
    declared_total: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _page_numbers_start_at_one(self) -> Self:
        if self.page is not None and self.page < 1:
            raise ValueError(f"{self.source_id}: page numbers start at 1, got {self.page}")
        return self


class Candidate(BaseModel):
    """A token that might be an item code, with the tier it reached and where it was found.

    `offset` is kept so a reviewer can locate the token in the original text. Two candidates
    at the same offset in the same document are the same finding, which is what
    `dedupe` keys on -- not on the token alone, because the same code legitimately appears
    many times and each occurrence is evidence.
    """

    token: str
    tier: Tier
    source_id: str
    kind: SourceKind = SourceKind.UNKNOWN
    page: int | None = None
    offset: int = 0
    # True when this token was found INSIDE a longer alphanumeric run rather than standing
    # alone. Such a token only reaches VALIDATED by passing the check character, and the
    # checksum admits roughly 1 in 36 windows by chance -- so an embedded finding is weaker
    # evidence than a standalone one, and a reviewer should be told which they are looking at.
    embedded: bool = False
    context: str = ""

    @model_validator(mode="after")
    def _token_must_be_the_right_length(self) -> Self:
        if len(self.token) != CODE_LENGTH:
            raise ValueError(
                f"a candidate is {CODE_LENGTH} characters, got {len(self.token)}: "
                f"{self.token!r}"
            )
        return self

    @property
    def occurrence(self) -> tuple[str, int | None, int]:
        """Identifies this OCCURRENCE, not this code."""
        return (self.source_id, self.page, self.offset)


class Finding(BaseModel):
    """One distinct code, with every place it was seen.

    Deduplication collapses occurrences into these, and it keeps the full provenance list
    rather than a count. A count answers "how often"; the list answers "where", and "which
    source did this come from" is the question actually asked when a code turns out to be
    wrong.
    """

    token: str
    tier: Tier
    occurrences: list[Candidate] = Field(default_factory=list)

    @property
    def times_seen(self) -> int:
        return len(self.occurrences)

    @property
    def sources(self) -> list[str]:
        return sorted({item.source_id for item in self.occurrences})

    @property
    def kinds(self) -> list[SourceKind]:
        return sorted({item.kind for item in self.occurrences})
