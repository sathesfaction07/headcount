"""Collapsing occurrences into findings, and accounting for whether anything was missed.

Two separate jobs that are easy to conflate.

DEDUPLICATION answers "which distinct codes did we see". It is straightforward and it
destroys information if done carelessly, so every occurrence is retained on the finding.

COMPLETENESS answers "did we see all of them", and that question is usually
**unanswerable from the extracted data alone**. This module's job is to say so out loud
rather than to imply success by staying quiet.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from multi_extract.models import Candidate, Document, Finding, Tier


def dedupe(candidates: list[Candidate]) -> list[Finding]:
    """Collapse occurrences of the same token into one finding, keeping all provenance.

    Keyed on the token, and every occurrence is kept on the finding. The obvious
    implementation -- `{c.token: c for c in candidates}` -- keeps one occurrence and silently
    discards the rest, which loses the answer to "where did this come from" for every code
    seen more than once. That is the question actually asked when a code turns out to be
    wrong.

    A token graded differently in two places takes the HIGHEST tier, because grading depends
    only on the token and a disagreement therefore means one occurrence was punctuation-
    padded or lower-cased. Taking the lowest would let a sloppily formatted mention
    downgrade a code that validates perfectly elsewhere.

    Findings come out sorted by token so two runs over the same input agree.
    """
    grouped: dict[str, list[Candidate]] = {}
    for item in candidates:
        grouped.setdefault(item.token, []).append(item)

    findings = []
    for token, occurrences in grouped.items():
        ordered = sorted(occurrences, key=lambda item: item.occurrence)
        findings.append(
            Finding(
                token=token,
                tier=max(item.tier for item in occurrences),
                occurrences=ordered,
            )
        )
    return sorted(findings, key=lambda item: item.token)


class Completeness(StrEnum):
    """What can be said about whether everything was collected."""

    COMPLETE = "complete"                 # a declared total exists and was matched
    SHORT = "short"                       # a declared total exists and was NOT matched
    OVER = "over"                         # more found than declared
    UNVERIFIABLE = "unverifiable"         # no declared total: nothing can be concluded
    GAP_IN_PAGES = "gap_in_pages"         # page numbers are not contiguous


class PageReport(BaseModel):
    """Pagination accounting for one source.

    `verdict` is deliberately not a boolean. "We collected everything" and "we cannot tell
    whether we collected everything" are different claims, and a bool forces the second into
    looking like the first or the third.
    """

    source_id: str
    pages_seen: list[int] = Field(default_factory=list)
    unique_found: int = 0
    occurrences_found: int = 0
    declared_total: int | None = None
    verdict: Completeness = Completeness.UNVERIFIABLE

    @property
    def missing_pages(self) -> list[int]:
        """Page numbers absent from an otherwise contiguous run starting at 1.

        Only meaningful for a source that numbers its pages. A gap is strong evidence of a
        fetch that dropped a page, and it is detectable without any declared total -- which
        makes it the one completeness signal that is always available.
        """
        if not self.pages_seen:
            return []
        expected = set(range(1, max(self.pages_seen) + 1))
        return sorted(expected - set(self.pages_seen))

    def describe(self) -> str:
        if self.verdict is Completeness.UNVERIFIABLE:
            return (
                f"{self.source_id}: {self.unique_found} unique code(s) found. The source "
                "declares no total, so whether anything was missed CANNOT be determined "
                "from this data."
            )
        if self.verdict is Completeness.GAP_IN_PAGES:
            return (
                f"{self.source_id}: page(s) {self.missing_pages} were never seen. Anything "
                "on them was missed, regardless of the totals."
            )
        if self.verdict is Completeness.SHORT:
            return (
                f"{self.source_id}: found {self.unique_found} unique but the source declares "
                f"{self.declared_total}. {self.declared_total - self.unique_found} "
                "unaccounted for."
            )
        if self.verdict is Completeness.OVER:
            return (
                f"{self.source_id}: found {self.unique_found} unique, more than the declared "
                f"{self.declared_total}. Either the declared total counts something else, or "
                "false positives are inflating the count."
            )
        return (
            f"{self.source_id}: {self.unique_found} unique matches the declared "
            f"{self.declared_total}."
        )


def account_for_pages(
    documents: list[Document], findings: list[Finding]
) -> list[PageReport]:
    """Per-source pagination accounting.

    The comparison is against UNIQUE codes, not occurrences. A code legitimately appears on
    several pages of a listing, so comparing occurrences to a declared total would report a
    spurious surplus on any source that repeats itself -- and repetition is normal.

    The subtlety that makes this worth a function rather than a subtraction: a source's
    declared total might itself count occurrences rather than distinct items, in which case
    even the unique comparison is apples-to-oranges. Nothing here can detect that, so OVER is
    reported as an ambiguity naming both possible causes rather than as a failure.

    A gap in page numbers outranks the totals. A missing page means data was definitely
    missed, whereas a total mismatch has innocent explanations.
    """
    per_source: dict[str, list[Document]] = {}
    for document in documents:
        per_source.setdefault(document.source_id, []).append(document)

    reports = []
    for source_id, docs in sorted(per_source.items()):
        pages = sorted({doc.page for doc in docs if doc.page is not None})

        # A source may declare its total on any page; they should agree, and if they do not
        # the largest is used because a listing usually reports the full count on every page.
        declared = [doc.declared_total for doc in docs if doc.declared_total is not None]
        declared_total = max(declared) if declared else None

        relevant = [
            item
            for item in findings
            if any(occurrence.source_id == source_id for occurrence in item.occurrences)
        ]
        occurrences = sum(
            1
            for item in findings
            for occurrence in item.occurrences
            if occurrence.source_id == source_id
        )

        report = PageReport(
            source_id=source_id,
            pages_seen=pages,
            unique_found=len(relevant),
            occurrences_found=occurrences,
            declared_total=declared_total,
        )

        if report.missing_pages:
            report.verdict = Completeness.GAP_IN_PAGES
        elif declared_total is None:
            report.verdict = Completeness.UNVERIFIABLE
        elif len(relevant) < declared_total:
            report.verdict = Completeness.SHORT
        elif len(relevant) > declared_total:
            report.verdict = Completeness.OVER
        else:
            report.verdict = Completeness.COMPLETE
        reports.append(report)

    return reports


class CollectionReport(BaseModel):
    """Everything one run produced."""

    findings: list[Finding] = Field(default_factory=list)
    pages: list[PageReport] = Field(default_factory=list)
    tier_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def unique_codes(self) -> int:
        return len(self.findings)

    @property
    def total_occurrences(self) -> int:
        return sum(item.times_seen for item in self.findings)

    @property
    def unverifiable_sources(self) -> list[str]:
        return [
            item.source_id
            for item in self.pages
            if item.verdict is Completeness.UNVERIFIABLE
        ]

    @property
    def problem_sources(self) -> list[PageReport]:
        """Sources where something is demonstrably wrong, as opposed to merely unknown."""
        return [
            item
            for item in self.pages
            if item.verdict in (Completeness.SHORT, Completeness.GAP_IN_PAGES)
        ]


def collect(
    documents: list[Document], *, min_tier: Tier = Tier.VALIDATED
) -> CollectionReport:
    """Extract, deduplicate, and account for completeness in one pass.

    `tier_counts` is computed over ALL candidates regardless of `min_tier`, because the
    counts at the tiers you rejected are the only evidence about whether the tier you chose
    is throwing away real codes.
    """
    from multi_extract.core.extract import candidates_in  # noqa: PLC0415

    every: list[Candidate] = []
    for document in documents:
        every.extend(candidates_in(document))

    counts = {tier.label: 0 for tier in Tier}
    for item in every:
        counts[item.tier.label] += 1

    kept = [item for item in every if item.tier >= min_tier]
    findings = dedupe(kept)
    return CollectionReport(
        findings=findings,
        pages=account_for_pages(documents, findings),
        tier_counts=counts,
    )
