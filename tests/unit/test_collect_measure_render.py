"""Dedup, completeness accounting, measurement, and rendering."""

from __future__ import annotations

import csv
import json

from multi_extract.core.collect import (
    Completeness,
    account_for_pages,
    collect,
    dedupe,
)
from multi_extract.core.extract import candidates_in
from multi_extract.core.measure import by_source, measure, score_at_tier
from multi_extract.models import Candidate, Document, SourceKind, Tier
from multi_extract.render import export, text
from multi_extract.sources import detect, load_documents, load_truth
from tests.conftest import document, shaped_but_invalid, valid_code


def candidate(token: str, *, source_id: str = "s1", page: int | None = None,
              offset: int = 0, kind: SourceKind = SourceKind.PLAIN_TEXT) -> Candidate:
    return Candidate(
        token=token, tier=Tier.VALIDATED, source_id=source_id, kind=kind,
        page=page, offset=offset,
    )


class TestDedupeKeepsProvenance:
    def test_repeated_occurrences_collapse_to_one_finding(self):
        code = valid_code(20)
        findings = dedupe([
            candidate(code, source_id="a", offset=0),
            candidate(code, source_id="b", offset=5),
            candidate(code, source_id="a", offset=9),
        ])
        assert len(findings) == 1
        assert findings[0].times_seen == 3, "every occurrence must survive"
        assert findings[0].sources == ["a", "b"]

    def test_provenance_is_not_reduced_to_a_count(self):
        """The naive `{c.token: c for c in ...}` keeps one and loses the rest."""
        code = valid_code(21)
        finding = dedupe([
            candidate(code, source_id="listing", page=1),
            candidate(code, source_id="export"),
        ])[0]
        assert {item.source_id for item in finding.occurrences} == {"listing", "export"}

    def test_the_highest_tier_wins_a_disagreement(self):
        code = valid_code(22)
        low = candidate(code)
        low = low.model_copy(update={"tier": Tier.SHAPED})
        findings = dedupe([low, candidate(code, offset=1)])
        assert findings[0].tier is Tier.VALIDATED

    def test_findings_are_sorted_so_two_runs_agree(self):
        codes = [valid_code(n) for n in range(30, 36)]
        first = [item.token for item in dedupe([candidate(c) for c in codes])]
        second = [item.token for item in dedupe([candidate(c) for c in reversed(codes)])]
        assert first == second == sorted(codes)

    def test_occurrences_within_a_finding_are_ordered(self):
        code = valid_code(23)
        finding = dedupe([
            candidate(code, source_id="b", offset=2),
            candidate(code, source_id="a", offset=9),
        ])[0]
        assert [item.occurrence for item in finding.occurrences] == sorted(
            item.occurrence for item in finding.occurrences
        )

    def test_no_candidates_produces_no_findings(self):
        assert dedupe([]) == []


class TestCompletenessIsNotABoolean:
    """"We got everything" and "we cannot tell" are different claims."""

    def test_a_source_with_no_declared_total_is_unverifiable_not_complete(self):
        code = valid_code(40)
        docs = [document(code, source_id="s")]
        findings = dedupe(candidates_in(docs[0]))
        report = account_for_pages(docs, findings)[0]
        assert report.verdict is Completeness.UNVERIFIABLE
        assert "CANNOT be determined" in report.describe()

    def test_a_matching_declared_total_is_complete(self):
        code = valid_code(41)
        docs = [document(code, source_id="s", declared_total=1)]
        report = account_for_pages(docs, dedupe(candidates_in(docs[0])))[0]
        assert report.verdict is Completeness.COMPLETE

    def test_a_shortfall_against_a_declared_total_is_short(self):
        code = valid_code(42)
        docs = [document(code, source_id="s", declared_total=5)]
        report = account_for_pages(docs, dedupe(candidates_in(docs[0])))[0]
        assert report.verdict is Completeness.SHORT
        assert "4 unaccounted for" in report.describe()

    def test_finding_more_than_declared_is_reported_as_an_ambiguity(self):
        """Either the declared total counts something else, or false positives inflated us."""
        docs = [document(f"{valid_code(43)} {valid_code(44)}", source_id="s",
                         declared_total=1)]
        report = account_for_pages(docs, dedupe(candidates_in(docs[0])))[0]
        assert report.verdict is Completeness.OVER
        assert "counts something else" in report.describe()

    def test_a_page_gap_is_detected_without_any_declared_total(self):
        """The one completeness signal that is always available."""
        docs = [
            document(valid_code(45), source_id="s", page=1),
            document(valid_code(46), source_id="s", page=3),
        ]
        findings = dedupe([item for doc in docs for item in candidates_in(doc)])
        report = account_for_pages(docs, findings)[0]
        assert report.verdict is Completeness.GAP_IN_PAGES
        assert report.missing_pages == [2]

    def test_a_page_gap_outranks_a_matching_total(self):
        """A missing page means data was definitely lost; a total can agree by coincidence."""
        docs = [
            document(valid_code(47), source_id="s", page=1, declared_total=2),
            document(valid_code(48), source_id="s", page=3, declared_total=2),
        ]
        findings = dedupe([item for doc in docs for item in candidates_in(doc)])
        assert account_for_pages(docs, findings)[0].verdict is Completeness.GAP_IN_PAGES

    def test_contiguous_pages_have_no_gap(self):
        docs = [document(valid_code(50 + n), source_id="s", page=n) for n in (1, 2, 3)]
        findings = dedupe([item for doc in docs for item in candidates_in(doc)])
        assert account_for_pages(docs, findings)[0].missing_pages == []

    def test_unique_not_occurrences_is_compared_to_the_declared_total(self):
        """A code repeated across pages must not look like a surplus."""
        code = valid_code(55)
        docs = [document(code, source_id="s", page=n, declared_total=1) for n in (1, 2)]
        findings = dedupe([item for doc in docs for item in candidates_in(doc)])
        report = account_for_pages(docs, findings)[0]
        assert report.unique_found == 1
        assert report.occurrences_found == 2
        assert report.verdict is Completeness.COMPLETE


class TestMeasurement:
    def test_precision_is_none_when_nothing_was_extracted(self):
        """0% would read as 'everything we found was wrong'."""
        score = score_at_tier([], {valid_code(60)}, Tier.VALIDATED)
        assert score.precision is None
        assert score.recall == 0.0, "recall IS defined: the truth set is non-empty"
        assert score.f1 is None

    def test_recall_is_none_when_the_truth_set_is_empty(self):
        score = score_at_tier([candidate(valid_code(61))], set(), Tier.VALIDATED)
        assert score.recall is None
        assert score.precision == 0.0

    def test_a_perfect_extraction_scores_one(self):
        codes = {valid_code(n) for n in range(62, 66)}
        score = score_at_tier([candidate(c) for c in codes], codes, Tier.VALIDATED)
        assert score.precision == 1.0
        assert score.recall == 1.0
        assert score.f1 == 1.0

    def test_distinct_tokens_are_scored_not_occurrences(self):
        """A code mentioned forty times must not dominate the measurement."""
        code = valid_code(67)
        many = [candidate(code, offset=n) for n in range(40)]
        score = score_at_tier(many, {code}, Tier.VALIDATED)
        assert score.extracted == 1
        assert score.true_positives == 1

    def test_strictness_raises_precision_and_lowers_or_holds_recall(self):
        real = valid_code(70)
        fake = shaped_but_invalid(70)
        docs = [document(f"{real} {fake}")]
        every = candidates_in(docs[0])
        report = measure(every, {real})
        loose = report.at(Tier.SHAPED)
        strict = report.at(Tier.VALIDATED)
        assert strict.precision >= loose.precision
        assert strict.recall <= loose.recall

    def test_missed_codes_are_named_not_counted(self):
        real = valid_code(71)
        report = measure([], {real})
        assert report.missed_at_strictest == [real]

    def test_best_f1_tier_is_none_when_nothing_is_measurable(self):
        assert measure([], set()).best_f1_tier is None

    def test_per_source_scores_are_separate(self):
        real = valid_code(72)
        fake = shaped_but_invalid(72)
        docs = [
            document(real, source_id="csv", kind=SourceKind.CSV_EXPORT),
            document(fake, source_id="mail", kind=SourceKind.EMAIL_BODY),
        ]
        every = [item for doc in docs for item in candidates_in(doc)]
        scores = by_source(every, {real}, tier=Tier.SHAPED)
        assert scores[SourceKind.CSV_EXPORT].precision == 1.0
        assert scores[SourceKind.EMAIL_BODY].precision == 0.0


class TestRendering:
    def test_an_undefined_precision_prints_as_not_available(self):
        """Precision is undefined here; recall is genuinely 0%. They are different facts.

        Nothing was extracted, so "of what we found, how much was real" has no denominator.
        But the truth set is non-empty, so "of what was there, how much did we get" is a real
        measurement whose answer is none of it. The report must show n/a for the first and
        0.0% for the second -- collapsing them either way would lose one of the two.
        """
        score = measure([], {valid_code(80)}).at(Tier.VALIDATED)
        assert score.precision is None
        assert score.recall == 0.0

        row = next(
            line
            for line in text.score_table(measure([], {valid_code(80)}))
            if line.strip().startswith("validated")
        )
        precision_cell, recall_cell = row.split()[-3], row.split()[-2]
        assert precision_cell == "n/a"
        assert recall_cell == "0.0%"

    def test_an_unverifiable_source_says_so_in_the_report(self):
        docs = [document(valid_code(81), source_id="s")]
        rendered = text.render(collect(docs))
        assert "unverifiable" in rendered
        assert "CANNOT be determined" in rendered

    def test_a_page_gap_appears_before_a_clean_source(self):
        """Worst first, so a reader who stops after two lines has seen the problem."""
        docs = [
            document(valid_code(82), source_id="broken", page=1),
            document(valid_code(83), source_id="broken", page=3),
            document(valid_code(84), source_id="fine", declared_total=1),
        ]
        lines = text.completeness_block(collect(docs))
        assert "gap_in_pages" in lines[0]

    def test_an_embedded_finding_is_marked_in_the_findings_list(self):
        code = valid_code(85)
        rendered = "\n".join(text.findings_block(collect([document(f"XX{code}")])))
        assert code in rendered
        assert "(embedded)" in rendered

    def test_the_tier_funnel_distinguishes_at_from_at_or_above(self):
        """Same words, two meanings -- the funnel says which is which."""
        docs = [document(f"{valid_code(86)} {shaped_but_invalid(86)}")]
        lines = text.tier_funnel(collect(docs))
        assert "graded AT" in lines[0]
        assert "at or above" in lines[0]

    def test_missed_codes_appear_in_the_full_report(self):
        real = valid_code(87)
        docs = [document("nothing here")]
        rendered = text.render(collect(docs), measure([], {real}))
        assert "never extracted" in rendered
        assert real in rendered


class TestExport:
    def test_all_files_are_written_even_when_empty(self, tmp_path):
        written = export.export_all(collect([]), tmp_path)
        assert {path.name for path in written} == {
            "codes.csv", "occurrences.csv", "completeness.csv", "codes.jsonl", "report.json",
        }
        for path in written:
            assert path.exists()

    def test_an_empty_csv_keeps_its_header(self, tmp_path):
        export.export_all(collect([]), tmp_path)
        rows = list(csv.reader((tmp_path / "codes.csv").read_text().splitlines()))
        assert rows[0] == export.FINDING_FIELDS
        assert len(rows) == 1

    def test_an_undefined_score_serialises_as_null(self, tmp_path):
        payload = export.report_payload(collect([]), measure([], {valid_code(90)}))
        strict = next(
            item for item in payload["accuracy"]["tiers"] if item["tier"] == "validated"
        )
        assert strict["precision"] is None
        assert '"precision": null' in json.dumps(payload)

    def test_a_source_without_pages_writes_blank_not_zero(self, tmp_path):
        """Page 0 does not exist; unpaginated is not 'the first page'."""
        rows = export.occurrence_rows(collect([document(valid_code(91), source_id="s")]))
        assert rows[0]["page"] == ""

    def test_the_completeness_verdict_is_exported(self, tmp_path):
        docs = [document(valid_code(92), source_id="s", declared_total=9)]
        rows = export.page_rows(collect(docs))
        assert rows[0]["verdict"] == "short"
        assert rows[0]["declared_total"] == 9


class TestFormatDetection:
    def test_a_zip_masquerading_as_text_is_caught(self, tmp_path):
        """An attachment named .txt that is really an archive would yield zero codes."""
        path = tmp_path / "export.txt"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        result = detect(path)
        assert result.detected == "zip"
        assert result.mismatched
        assert "would have produced nothing" in result.describe()

    def test_an_xlsx_is_not_a_mismatch(self, tmp_path):
        path = tmp_path / "book.xlsx"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        assert not detect(path).mismatched

    def test_a_pdf_is_recognised(self, tmp_path):
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"%PDF-1.7\n" + b"x" * 32)
        assert detect(path).detected == "pdf"
        assert not detect(path).mismatched

    def test_html_emailed_as_doc_is_caught(self, tmp_path):
        path = tmp_path / "page.doc"
        path.write_text("<!DOCTYPE html><html><body>hi</body></html>", encoding="utf-8")
        result = detect(path)
        assert result.detected == "markup"
        assert result.mismatched

    def test_a_csv_is_detected_as_delimited(self, tmp_path):
        path = tmp_path / "rows.csv"
        path.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")
        assert detect(path).detected == "delimited"
        assert not detect(path).mismatched

    def test_prose_containing_one_comma_is_not_delimited(self, tmp_path):
        """One line with a comma is a sentence; two lines agreeing is a table."""
        path = tmp_path / "note.txt"
        path.write_text("Hello, team.\nNothing tabular follows at all here.\n", encoding="utf-8")
        assert detect(path).detected == "text"

    def test_binary_signatures_are_checked_before_the_text_heuristic(self, tmp_path):
        """A zip can contain decodable bytes; asking 'is it text' first misclassifies it."""
        path = tmp_path / "archive.zip"
        path.write_bytes(b"PK\x03\x04" + b"a,b,c\n1,2,3\n4,5,6\n" * 4)
        assert detect(path).detected == "zip"


class TestLoading:
    def test_a_malformed_manifest_line_is_reported_not_fatal(self, tmp_path):
        path = tmp_path / "m.jsonl"
        path.write_text(
            '{"source_id":"a","text":"x"}\nnot json at all\n{"source_id":"b","text":"y"}\n',
            encoding="utf-8",
        )
        documents, problems = load_documents(path)
        assert len(documents) == 2
        assert len(problems) == 1
        assert "m.jsonl:2" in problems[0]

    def test_a_page_number_below_one_is_refused(self, tmp_path):
        path = tmp_path / "m.jsonl"
        path.write_text('{"source_id":"a","text":"x","page":0}\n', encoding="utf-8")
        documents, problems = load_documents(path)
        assert not documents
        assert "page numbers start at 1" in problems[0]

    def test_a_missing_manifest_raises(self, tmp_path):
        import pytest

        with pytest.raises(FileNotFoundError):
            load_documents(tmp_path / "nope.jsonl")

    def test_truth_loads_from_a_plain_list_or_a_csv(self, tmp_path):
        code = valid_code(95)
        plain = tmp_path / "t.txt"
        plain.write_text(f"{code}\n{valid_code(96)}\n", encoding="utf-8")
        assert code in load_truth(plain)

        table = tmp_path / "t.csv"
        table.write_text(f"code,note\n{code},x\n", encoding="utf-8")
        assert load_truth(table) == {code}


class TestPropertiesOverTheShippedCorpus:
    """Run against data/, so editing it and breaking an invariant fails the build."""

    def test_the_corpus_loads_cleanly(self, corpus):
        assert len(corpus) >= 5

    def test_every_finding_carries_at_least_one_occurrence(self, corpus):
        for finding in collect(corpus).findings:
            assert finding.occurrences

    def test_occurrences_sum_to_the_candidates_that_were_kept(self, corpus):
        report = collect(corpus)
        kept = [
            item
            for doc in corpus
            for item in candidates_in(doc)
            if item.tier >= Tier.VALIDATED
        ]
        assert report.total_occurrences == len(kept)

    def test_the_strict_tier_reaches_full_precision_on_this_corpus(self, corpus, truth):
        every = [item for doc in corpus for item in candidates_in(doc)]
        score = measure(every, truth).at(Tier.VALIDATED)
        assert score.precision == 1.0, f"{score.false_positives} false positive(s)"

    def test_the_only_misses_are_the_codes_on_the_dropped_page(self, corpus, truth):
        """Recall below 1.0 here is a COMPLETENESS failure, not an extraction one."""
        every = [item for doc in corpus for item in candidates_in(doc)]
        report = measure(every, truth)
        present = {code for code in truth for doc in corpus if code in doc.text.upper()}
        assert set(report.missed_at_strictest) == truth - present

    def test_the_dropped_page_is_reported_as_a_gap(self, corpus):
        report = collect(corpus)
        gaps = [
            item for item in report.pages if item.verdict is Completeness.GAP_IN_PAGES
        ]
        assert gaps, "the corpus must exercise a page gap"
        assert gaps[0].missing_pages == [3]

    def test_sources_without_a_declared_total_are_named_unverifiable(self, corpus):
        assert collect(corpus).unverifiable_sources

    def test_a_code_appearing_in_two_sources_is_one_finding_with_two_sources(self, corpus):
        shared = [item for item in collect(corpus).findings if len(item.sources) > 1]
        assert shared, "the corpus must exercise cross-source overlap"

    def test_collection_is_order_independent(self, corpus):
        import random

        reference = collect(corpus)
        for seed in range(5):
            shuffled = list(corpus)
            random.Random(seed).shuffle(shuffled)
            got = collect(shuffled)
            assert [item.token for item in got.findings] == [
                item.token for item in reference.findings
            ]
            assert got.total_occurrences == reference.total_occurrences

    def test_every_tier_is_exercised_by_the_corpus(self, corpus):
        """Otherwise the tier assertions above are vacuous."""
        counts = collect(corpus).tier_counts
        assert counts["validated"] > 0
        assert counts["shaped"] > 0, "the corpus must contain shaped-but-invalid look-alikes"

    def test_every_source_kind_is_exercised(self, corpus):
        kinds = {doc.kind for doc in corpus}
        assert len(kinds) >= 4, f"only {kinds}"


class TestDocumentModel:
    def test_a_candidate_of_the_wrong_length_is_refused(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="a candidate is 10 characters"):
            Candidate(token="SHORT", tier=Tier.VALIDATED, source_id="s")

    def test_a_document_defaults_to_unknown_kind(self):
        assert Document(source_id="s").kind is SourceKind.UNKNOWN
