"""Machine-readable output.

One rule: a value that does not exist is written as null or an empty cell, never as 0.
Precision of 0 and precision-undefined are different facts, and a spreadsheet will happily
average or sort on either.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from multi_extract.core.collect import CollectionReport
from multi_extract.core.measure import MeasureReport

FINDING_FIELDS = ["code", "tier", "times_seen", "sources", "kinds", "embedded"]
OCCURRENCE_FIELDS = [
    "code", "tier", "source_id", "kind", "page", "offset", "embedded", "context",
]
PAGE_FIELDS = [
    "source_id", "verdict", "pages_seen", "missing_pages", "unique_found",
    "occurrences_found", "declared_total",
]


def finding_rows(report: CollectionReport) -> list[dict]:
    return [
        {
            "code": item.token,
            "tier": item.tier.label,
            "times_seen": item.times_seen,
            "sources": "|".join(item.sources),
            "kinds": "|".join(kind.value for kind in item.kinds),
            "embedded": any(occurrence.embedded for occurrence in item.occurrences),
        }
        for item in report.findings
    ]


def occurrence_rows(report: CollectionReport) -> list[dict]:
    return [
        {
            "code": finding.token,
            "tier": occurrence.tier.label,
            "source_id": occurrence.source_id,
            "kind": occurrence.kind.value,
            # Blank, not 0: page 0 does not exist, and a source without pagination is a
            # different thing from one on its first page.
            "page": "" if occurrence.page is None else occurrence.page,
            "offset": occurrence.offset,
            "embedded": occurrence.embedded,
            "context": occurrence.context,
        }
        for finding in report.findings
        for occurrence in finding.occurrences
    ]


def page_rows(report: CollectionReport) -> list[dict]:
    return [
        {
            "source_id": item.source_id,
            "verdict": item.verdict.value,
            "pages_seen": "|".join(str(page) for page in item.pages_seen),
            "missing_pages": "|".join(str(page) for page in item.missing_pages),
            "unique_found": item.unique_found,
            "occurrences_found": item.occurrences_found,
            "declared_total": "" if item.declared_total is None else item.declared_total,
        }
        for item in report.pages
    ]


def report_payload(
    collection: CollectionReport, measurement: MeasureReport | None = None
) -> dict:
    payload: dict = {
        "summary": {
            "unique_codes": collection.unique_codes,
            "occurrences": collection.total_occurrences,
            "sources": len(collection.pages),
            "tier_counts": collection.tier_counts,
            "unverifiable_sources": collection.unverifiable_sources,
            "problem_sources": [item.source_id for item in collection.problem_sources],
        },
        "completeness": [
            {
                "source_id": item.source_id,
                "verdict": item.verdict.value,
                "pages_seen": item.pages_seen,
                "missing_pages": item.missing_pages,
                "unique_found": item.unique_found,
                "declared_total": item.declared_total,
                "note": item.describe(),
            }
            for item in collection.pages
        ],
    }
    if measurement is not None:
        payload["accuracy"] = {
            "truth_size": measurement.truth_size,
            "tiers": [
                {
                    "tier": score.tier.label,
                    "extracted": score.extracted,
                    "true_positives": score.true_positives,
                    "false_positives": score.false_positives,
                    "false_negatives": score.false_negatives,
                    "precision": None if score.precision is None else round(score.precision, 4),
                    "recall": None if score.recall is None else round(score.recall, 4),
                    "f1": None if score.f1 is None else round(score.f1, 4),
                }
                for score in measurement.scores
            ],
            "missed": measurement.missed_at_strictest,
            "spurious": measurement.spurious_at_strictest,
        }
    return payload


def write_csv(rows: list[dict], path: Path, fields: list[str]) -> Path:
    """Write a CSV, header included even with no rows.

    A header-only file says "looked, found nothing". A missing file cannot be distinguished
    from a run that died before it got here.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_jsonl(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, default=str) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def export_all(
    collection: CollectionReport, directory: Path, measurement: MeasureReport | None = None
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = [
        write_csv(finding_rows(collection), directory / "codes.csv", FINDING_FIELDS),
        write_csv(occurrence_rows(collection), directory / "occurrences.csv", OCCURRENCE_FIELDS),
        write_csv(page_rows(collection), directory / "completeness.csv", PAGE_FIELDS),
        write_jsonl(finding_rows(collection), directory / "codes.jsonl"),
    ]
    payload = directory / "report.json"
    payload.write_text(
        json.dumps(report_payload(collection, measurement), indent=2) + "\n", encoding="utf-8"
    )
    written.append(payload)
    return written
