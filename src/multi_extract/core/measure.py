"""Measuring extraction against a known truth set.

Two failure directions, and they trade off against each other:

    PRECISION   of what we extracted, how much was real          punishes over-capture
    RECALL      of what was there, how much did we extract       punishes misses

The tier system exists because those two move in opposite directions as strictness rises,
and the only way to choose a tier is to see both numbers at every tier on the same input.

A miss is the more dangerous failure and the harder one to notice: a false positive is a
wrong code somebody will eventually query, while a false negative is a code that simply is
not in the output, and nothing in the output says so. That asymmetry is why every tier is scored
rather than just the chosen one, and why `missed_at_strictest` carries the actual tokens
rather than a count.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from multi_extract.models import Candidate, SourceKind, Tier


class TierScore(BaseModel):
    """Precision and recall at one tier.

    `precision` and `recall` are `float | None` because both have an empty denominator case:
    precision is undefined when nothing was extracted, recall when the truth set is empty.
    Returning 0.0 for either would state a measurement where none exists -- an extractor that
    returned nothing would show 0% precision, which reads as "everything it found was wrong"
    rather than "it found nothing".
    """

    tier: Tier
    extracted: int
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float | None:
        if self.extracted == 0:
            return None
        return self.true_positives / self.extracted

    @property
    def recall(self) -> float | None:
        actual = self.true_positives + self.false_negatives
        if actual == 0:
            return None
        return self.true_positives / actual

    @property
    def f1(self) -> float | None:
        """Harmonic mean, or None if either component is undefined or both are zero."""
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * precision * recall / (precision + recall)


class MeasureReport(BaseModel):
    """Scores at every tier, plus what the strictest tier missed."""

    scores: list[TierScore] = Field(default_factory=list)
    missed_at_strictest: list[str] = Field(default_factory=list)
    spurious_at_strictest: list[str] = Field(default_factory=list)
    truth_size: int = 0

    def at(self, tier: Tier) -> TierScore:
        for score in self.scores:
            if score.tier is tier:
                return score
        raise KeyError(f"no score for {tier!r}")

    @property
    def best_f1_tier(self) -> Tier | None:
        """The tier with the highest F1, or None if no tier produced a defined F1.

        Reported as information, not as a recommendation. F1 weights precision and recall
        equally, and in this problem they are not equally important -- a missed code is worse
        than a spurious one. Which tier to use is a judgement about that asymmetry, so this
        names the arithmetic winner and leaves the decision alone.
        """
        scored = [item for item in self.scores if item.f1 is not None]
        if not scored:
            return None
        return max(scored, key=lambda item: item.f1).tier


def score_at_tier(
    candidates: list[Candidate], truth: set[str], tier: Tier
) -> TierScore:
    """Score the candidates that reached at least `tier` against the truth set.

    Compares DISTINCT tokens, not occurrences. Counting occurrences would let a code
    mentioned forty times on one page dominate the score, so a single popular item would
    decide the measurement for the whole catalogue.
    """
    extracted = {item.token for item in candidates if item.tier >= tier}
    true_positives = extracted & truth
    return TierScore(
        tier=tier,
        extracted=len(extracted),
        true_positives=len(true_positives),
        false_positives=len(extracted - truth),
        false_negatives=len(truth - extracted),
    )


def measure(candidates: list[Candidate], truth: set[str]) -> MeasureReport:
    """Score every tier against the truth set."""
    scores = [score_at_tier(candidates, truth, tier) for tier in Tier]
    strictest = max(Tier)
    extracted = {item.token for item in candidates if item.tier >= strictest}
    return MeasureReport(
        scores=scores,
        missed_at_strictest=sorted(truth - extracted),
        spurious_at_strictest=sorted(extracted - truth),
        truth_size=len(truth),
    )


def by_source(
    candidates: list[Candidate], truth: set[str], *, tier: Tier = Tier.VALIDATED
) -> dict[SourceKind, TierScore]:
    """Score each source kind separately.

    An aggregate precision figure over a structured CSV export and a free-text email body
    describes neither. The CSV is nearly all true positives; the email is mostly look-alikes.
    Averaging them produces a number that is wrong about both, and which one dominates depends
    only on which happened to be larger.

    Recall per source is a weaker notion than it looks, and the docstring should say so: the
    truth set is global, so a code present only in source A counts as a miss for source B too.
    Read the per-source PRECISION; treat the per-source recall as diagnostic only.
    """
    grouped: dict[SourceKind, list[Candidate]] = {}
    for item in candidates:
        grouped.setdefault(item.kind, []).append(item)
    return {
        kind: score_at_tier(group, truth, tier)
        for kind, group in sorted(grouped.items())
    }
