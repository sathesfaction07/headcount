"""CLI: every command, exit codes, and stdout staying pipeable."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from multi_extract.cli import main
from tests.conftest import DATA, shaped_but_invalid, valid_code

MANIFEST = str(DATA / "manifest.jsonl")
TRUTH = str(DATA / "truth.txt")


@pytest.fixture
def run():
    runner = CliRunner()
    return lambda *args: runner.invoke(main, list(args))


class TestExtract:
    def test_it_reports_and_exits_nonzero_on_a_page_gap(self, run):
        result = run("extract", "-i", MANIFEST)
        assert result.exit_code == 1
        assert "gap_in_pages" in result.output
        assert "page(s) [3] were never seen" in result.output

    def test_an_unverifiable_source_is_named_but_does_not_fail_the_run(self, run):
        """Most sources declare no total; failing on those would fail always."""
        result = run("extract", "-i", MANIFEST)
        assert "unverifiable" in result.output
        assert "CANNOT be determined" in result.output

    def test_the_tier_funnel_shows_both_readings(self, run):
        result = run("extract", "-i", MANIFEST)
        assert "graded AT" in result.output
        assert "at or above" in result.output

    def test_a_looser_tier_reports_more(self, run):
        strict = run("extract", "-i", MANIFEST, "-t", "validated")
        loose = run("extract", "-i", MANIFEST, "-t", "shaped")
        assert "reporting at tier: validated" in strict.output
        assert "reporting at tier: shaped" in loose.output

        def unique(output: str) -> int:
            line = next(x for x in output.splitlines() if "unique codes" in x)
            return int(line.split()[-1])

        assert unique(loose.output) > unique(strict.output)

    def test_an_export_writes_all_five_artefacts(self, run, tmp_path):
        result = run("extract", "-i", MANIFEST, "-o", str(tmp_path))
        assert result.exit_code == 1, "the page gap does not go away because we exported"
        assert {path.name for path in tmp_path.iterdir()} == {
            "codes.csv", "occurrences.csv", "completeness.csv", "codes.jsonl", "report.json",
        }

    def test_the_json_report_carries_the_completeness_verdicts(self, run, tmp_path):
        run("extract", "-i", MANIFEST, "-o", str(tmp_path))
        payload = json.loads((tmp_path / "report.json").read_text())
        verdicts = {item["source_id"]: item["verdict"] for item in payload["completeness"]}
        assert verdicts["listing-b"] == "gap_in_pages"
        assert verdicts["listing-a"] == "complete"
        assert verdicts["export-c"] == "unverifiable"
        assert payload["summary"]["problem_sources"] == ["listing-b"]
        assert sorted(payload["summary"]["unverifiable_sources"]) == ["email-d", "export-c"]


class TestMeasure:
    def test_it_exits_nonzero_when_a_known_code_was_never_extracted(self, run):
        result = run("measure", "-i", MANIFEST, "-k", TRUTH)
        assert result.exit_code == 1
        assert "never extracted" in result.output

    def test_the_strict_tier_reaches_full_precision_on_the_shipped_corpus(self, run):
        """Note the row selection: TWO rows begin with "validated".

        The tier funnel has one and the accuracy table has another, which is exactly the
        ambiguity the funnel header calls out -- and it caught this test first. The accuracy
        row is the one carrying percentages.
        """
        result = run("measure", "-i", MANIFEST, "-k", TRUTH)
        row = next(
            line for line in result.output.splitlines()
            if line.strip().startswith("validated") and "%" in line
        )
        assert "100.0%" in row

    def test_precision_rises_with_strictness(self, run):
        result = run("measure", "-i", MANIFEST, "-k", TRUTH)
        rows = {
            line.split()[0]: line.split()
            for line in result.output.splitlines()
            if line.strip().split()[:1] and line.strip().split()[0] in
            ("looks_like", "shaped", "validated")
        }
        def precision(name: str) -> float:
            return float(rows[name][-3].rstrip("%"))
        assert precision("validated") >= precision("shaped")

    def test_per_source_precision_is_reported_with_its_caveat(self, run):
        result = run("measure", "-i", MANIFEST, "-k", TRUTH)
        assert "by source" in result.output
        assert "csv_export" in result.output
        assert "diagnostic only" in result.output

    def test_the_json_report_carries_null_for_an_undefined_score(self, run, tmp_path):
        run("measure", "-i", MANIFEST, "-k", TRUTH, "-o", str(tmp_path))
        payload = json.loads((tmp_path / "report.json").read_text())
        assert payload["accuracy"]["truth_size"] == 90
        assert len(payload["accuracy"]["missed"]) == 6
        assert payload["accuracy"]["spurious"] == []


class TestCheck:
    def test_a_valid_code_is_explained_and_exits_zero(self, run):
        code = valid_code(100)
        result = run("check", code)
        assert result.exit_code == 0
        assert "validated" in result.output
        assert "agrees" in result.output

    def test_a_shaped_but_invalid_token_says_what_the_check_should_be(self, run):
        result = run("check", shaped_but_invalid(100))
        assert result.exit_code == 1
        assert "shaped" in result.output
        assert "but should be" in result.output
        assert "another system's" in result.output

    def test_a_badly_shaped_token_says_why(self, run):
        result = run("check", "1234567890")
        assert result.exit_code == 1
        assert "wrong shape" in result.output

    def test_a_lower_case_code_is_normalised_first(self, run):
        code = valid_code(101)
        result = run("check", code.lower())
        assert result.exit_code == 0
        assert code in result.output


class TestDetect:
    def test_a_mismatched_file_exits_nonzero(self, run, tmp_path):
        path = tmp_path / "export.txt"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        result = run("detect", str(path))
        assert result.exit_code == 1
        assert "content is zip" in result.output
        assert "1 of 1 file(s) contradict" in result.output

    def test_a_consistent_file_exits_zero(self, run, tmp_path):
        path = tmp_path / "rows.csv"
        path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
        result = run("detect", str(path))
        assert result.exit_code == 0
        assert "consistent with its extension" in result.output

    def test_several_files_are_all_reported(self, run, tmp_path):
        good = tmp_path / "a.csv"
        good.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
        bad = tmp_path / "b.txt"
        bad.write_bytes(b"%PDF-1.7\n" + b"x" * 32)
        result = run("detect", str(good), str(bad))
        assert result.exit_code == 1
        assert "1 of 2 file(s) contradict" in result.output


class TestDoctor:
    def test_it_passes_with_the_shipped_data(self, run):
        result = run("doctor", "-i", MANIFEST, "-k", TRUTH)
        assert result.exit_code == 0
        assert "10 document(s), 0 problem(s)" in result.output
        assert "90 known code(s)" in result.output

    def test_it_explains_why_unverifiable_is_not_a_failure(self, run):
        result = run("doctor", "-i", MANIFEST, "-k", TRUTH)
        assert "UNVERIFIABLE, not complete" in result.output
        assert "which are demonstrable" in result.output

    def test_a_missing_manifest_fails(self, run, tmp_path):
        result = run("doctor", "-i", str(tmp_path / "nope.jsonl"), "-k", TRUTH)
        assert result.exit_code == 1
        assert "MISSING" in result.output

    def test_a_missing_truth_file_is_noted_but_not_fatal(self, run, tmp_path):
        result = run("doctor", "-i", MANIFEST, "-k", str(tmp_path / "nope.txt"))
        assert result.exit_code == 0
        assert "measure will not run" in result.output


class TestStdoutStaysPipeable:
    def test_diagnostics_go_to_stderr(self):
        """`extract | grep` must not pick up the unverifiable-source warning."""
        runner = CliRunner()
        result = runner.invoke(main, ["extract", "-i", MANIFEST])
        assert "completeness is unknown rather than confirmed" in result.stderr
        assert "completeness is unknown rather than confirmed" not in result.stdout
        assert "summary" in result.stdout
