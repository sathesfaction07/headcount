"""Text reporting.

Two rules run through all of it.

A number that does not exist prints as `n/a`, never as `0`. Precision of 0% reads as
"everything we found was wrong"; an extractor that found nothing has earned no such verdict.

A completeness claim is never implied by silence. A source that declares no total gets an
explicit sentence saying its completeness cannot be determined, because a clean-looking
report with no warnings is exactly how an unverifiable result gets read as a verified one.
"""

from __future__ import annotations

from multi_extract.core.collect import CollectionReport, Completeness
from multi_extract.core.measure import MeasureReport, TierScore
from multi_extract.models import SourceKind, Tier

NOT_MEASURED = "n/a"


def _pct(value: float | None) -> str:
    return NOT_MEASURED if value is None else f"{value:.1%}"


def _num(value: float | None) -> str:
    return NOT_MEASURED if value is None else f"{value:.3f}"


def tier_funnel(report: CollectionReport) -> list[str]:
    """How many candidates each tier admitted.

    The funnel is the evidence about whether the strict tier is discarding real codes. Shown
    even when only the strict tier is being acted on, because a large gap between SHAPED and
    VALIDATED is the signal that either the corpus is full of other systems' identifiers or
    the check character is being computed wrongly -- and those need different responses.
    """
    lines = [
        "  tier          graded AT   at or above",
    ]
    total = sum(report.tier_counts.values())
    running = total
    for tier in Tier:
        at_tier = report.tier_counts.get(tier.label, 0)
        lines.append(f"  {tier.label:<13} {at_tier:>9} {running:>13}")
        running -= at_tier
    lines.append(
        "  'graded AT' is how many stopped at that tier; 'at or above' is what a run "
        "reporting\n  at that tier would include. The accuracy table uses AT OR ABOVE."
    )
    return lines


def score_table(report: MeasureReport) -> list[str]:
    """Precision and recall at every tier."""
    lines = [
        f"  truth set: {report.truth_size} known code(s)",
        "  tier          extracted    TP    FP    FN  precision   recall      F1",
    ]
    for score in report.scores:
        lines.append(
            f"  {score.tier.label:<13} {score.extracted:>9} {score.true_positives:>5} "
            f"{score.false_positives:>5} {score.false_negatives:>5} "
            f"{_pct(score.precision):>10} {_pct(score.recall):>8} {_num(score.f1):>7}"
        )
    best = report.best_f1_tier
    lines.append(
        f"  highest F1: {best.label if best else NOT_MEASURED}"
        "   (arithmetic only -- a miss costs more than a false hit, so this is not advice)"
    )
    return lines


def source_table(scores: dict[SourceKind, TierScore]) -> list[str]:
    """Per-source precision.

    Precision is the column to read. Per-source RECALL is against the global truth set, so a
    code present only in source A counts as a miss for source B -- the number is diagnostic,
    not meaningful, and labelling it as such is cheaper than omitting it and being asked.
    """
    lines = ["  source kind         extracted  precision   recall*"]
    for kind, score in scores.items():
        lines.append(
            f"  {kind.value:<20} {score.extracted:>8} {_pct(score.precision):>10} "
            f"{_pct(score.recall):>9}"
        )
    lines.append("  * recall is against the GLOBAL truth set, so per-source it is diagnostic only")
    return lines


def completeness_block(report: CollectionReport) -> list[str]:
    """Per-source completeness, worst first.

    Ordered so the demonstrable problems come before the merely unknown ones, and the unknowns
    before the clean results. A reader who stops after two lines should have seen the worst.
    """
    order = {
        Completeness.GAP_IN_PAGES: 0,
        Completeness.SHORT: 1,
        Completeness.OVER: 2,
        Completeness.UNVERIFIABLE: 3,
        Completeness.COMPLETE: 4,
    }
    lines = []
    for page in sorted(report.pages, key=lambda item: (order[item.verdict], item.source_id)):
        lines.append(f"  [{page.verdict.value}] {page.describe()}")
    if not lines:
        lines.append("  no sources")
    return lines


def findings_block(report: CollectionReport, *, limit: int = 12) -> list[str]:
    """Distinct codes with their provenance.

    `embedded` is surfaced per finding rather than buried, because such a token reached
    VALIDATED only by passing the check character inside a longer run -- weaker evidence than
    a standalone match, and a reviewer should be able to see which they are looking at.
    """
    if not report.findings:
        return ["  none"]
    lines = ["  code         seen  sources"]
    for finding in report.findings[:limit]:
        embedded = any(item.embedded for item in finding.occurrences)
        mark = " (embedded)" if embedded else ""
        lines.append(
            f"  {finding.token}  {finding.times_seen:>4}  "
            f"{', '.join(finding.sources)}{mark}"
        )
    if len(report.findings) > limit:
        lines.append(f"  ... {len(report.findings) - limit} more")
    return lines


def render(
    collection: CollectionReport,
    measurement: MeasureReport | None = None,
    per_source: dict[SourceKind, TierScore] | None = None,
) -> str:
    """The full text report. Measurement sections appear only when a truth set was supplied."""
    sections: list[tuple[str, list[str]]] = [
        ("summary", [
            f"  unique codes     {collection.unique_codes}",
            f"  occurrences      {collection.total_occurrences}",
            f"  sources          {len(collection.pages)}",
        ]),
        ("tiers", tier_funnel(collection)),
        ("completeness", completeness_block(collection)),
        ("findings", findings_block(collection)),
    ]
    if measurement is not None:
        sections.insert(2, ("accuracy", score_table(measurement)))
        if measurement.missed_at_strictest:
            sections.insert(3, ("missed", [
                f"  {len(measurement.missed_at_strictest)} known code(s) never extracted:",
                *[f"    {token}" for token in measurement.missed_at_strictest[:10]],
            ]))
    if per_source:
        sections.append(("by source", source_table(per_source)))

    lines: list[str] = []
    for title, body in sections:
        lines.append(title)
        lines.extend(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
