"""Command line interface.

Exit codes are the point of this being a CLI rather than a library:

  extract   1 if any source has a DEMONSTRABLE completeness problem -- a page gap or a
              shortfall against a declared total
  measure   1 if any known code was never extracted
  detect    1 if any file's content contradicts its extension

`extract` deliberately does NOT fail on an unverifiable source. Most sources declare no
total, so failing on those would fail always and the signal would be worthless. It says so
loudly in the output instead, which is the honest handling of "we cannot tell".
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from multi_extract.config import load_settings
from multi_extract.core.collect import collect
from multi_extract.core.extract import candidates_in
from multi_extract.core.measure import by_source, measure
from multi_extract.models import Tier
from multi_extract.render import export, text
from multi_extract.sources import detect, load_documents, load_truth


def _err(message: str) -> None:
    """Diagnostics to stderr, so stdout stays pipeable."""
    click.echo(message, err=True)


def _load(manifest: Path | None):
    settings = load_settings()
    documents, problems = load_documents(manifest or settings.manifest)
    for problem in problems:
        _err(f"  problem: {problem}")
    return settings, documents, problems


@click.group()
@click.version_option("0.1.0", prog_name="headcount")
def main() -> None:
    """Extract item codes from mixed sources and account for what may have been missed."""


@main.command()
@click.option("-i", "--manifest", type=click.Path(exists=True, path_type=Path))
@click.option("-t", "--tier", type=click.Choice([item.label for item in Tier]),
              default=None, help="minimum tier to report (default: validated)")
@click.option("-o", "--out", type=click.Path(path_type=Path), help="also export artefacts here")
@click.option("--limit", type=int, default=12, help="findings to list")
def extract(manifest: Path | None, tier: str | None, out: Path | None, limit: int) -> None:
    """Extract codes, deduplicate, and account for completeness.

    Exits non-zero only for a demonstrable problem: a gap in page numbers, or fewer unique
    codes than a source declared. An unverifiable source is reported, not failed on.
    """
    settings, documents, problems = _load(manifest)
    chosen = (
        next(item for item in Tier if item.label == tier)
        if tier
        else Tier(settings.min_tier)
    )

    report = collect(documents, min_tier=chosen)
    click.echo(text.render(report), nl=False)
    click.echo(f"\nreporting at tier: {chosen.label}")

    if report.unverifiable_sources:
        _err(
            f"  {len(report.unverifiable_sources)} source(s) declare no total, so their "
            "completeness is unknown rather than confirmed"
        )
    if out:
        for path in export.export_all(report, out):
            click.echo(f"wrote {path}")

    sys.exit(1 if (report.problem_sources or problems) else 0)


@main.command()
@click.option("-i", "--manifest", type=click.Path(exists=True, path_type=Path))
@click.option("-k", "--truth", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--out", type=click.Path(path_type=Path))
def measure_cmd(manifest: Path | None, truth: Path | None, out: Path | None) -> None:
    """Score extraction at every tier against a known truth set.

    Exits non-zero if any known code was never extracted, because a miss is the failure that
    nothing in the normal output reveals.
    """
    settings, documents, _ = _load(manifest)
    known = load_truth(truth or settings.truth)

    every = [item for document in documents for item in candidates_in(document)]
    report = collect(documents, min_tier=Tier(settings.min_tier))
    scores = measure(every, known)
    click.echo(
        text.render(report, scores, by_source(every, known)), nl=False
    )

    if out:
        for path in export.export_all(report, out, scores):
            click.echo(f"wrote {path}")

    sys.exit(1 if scores.missed_at_strictest else 0)


@main.command()
@click.argument("paths", nargs=-1, required=True,
                type=click.Path(exists=True, path_type=Path))
def detect_cmd(paths: tuple[Path, ...]) -> None:
    """Identify files by content and report any whose extension disagrees.

    Exits non-zero on a mismatch. An attachment named `.txt` that is really a zip archive
    would be parsed as text and yield no codes, and a zero is indistinguishable from a file
    that genuinely had none.
    """
    mismatched = 0
    for path in sorted(paths):
        result = detect(path)
        click.echo(result.describe())
        mismatched += int(result.mismatched)
    click.echo(f"\n{mismatched} of {len(paths)} file(s) contradict their extension")
    sys.exit(1 if mismatched else 0)


@main.command()
@click.argument("token")
def check(token: str) -> None:
    """Explain what tier a single token reaches, and why."""
    from multi_extract.core.extract import grade  # noqa: PLC0415
    from multi_extract.identity import (  # noqa: PLC0415
        BODY_LENGTH,
        SHAPED,
        check_character,
        normalise,
    )

    cleaned = normalise(token)
    click.echo(f"  input      {token!r}")
    click.echo(f"  normalised {cleaned!r}")
    click.echo(f"  tier       {grade(cleaned).label}")

    if not SHAPED.fullmatch(cleaned):
        click.echo("  why        wrong shape: needs 10 characters, a leading letter, uppercase")
        sys.exit(1)

    expected = check_character(cleaned[:BODY_LENGTH])
    if expected == cleaned[-1]:
        click.echo(f"  why        check character {expected!r} agrees")
        sys.exit(0)
    click.echo(f"  why        check character is {cleaned[-1]!r} but should be {expected!r}")
    click.echo("             shaped like a code, but not one -- most likely another system's")
    sys.exit(1)


@main.command()
@click.option("-i", "--manifest", type=click.Path(path_type=Path))
@click.option("-k", "--truth", type=click.Path(path_type=Path))
def doctor(manifest: Path | None, truth: Path | None) -> None:
    """Check configuration and inputs."""
    settings = load_settings()
    manifest_path = manifest or settings.manifest
    truth_path = truth or settings.truth
    ok = True

    click.echo("configuration")
    click.echo(f"  manifest    {manifest_path}")
    click.echo(f"  truth       {truth_path}")
    click.echo(f"  min tier    {Tier(settings.min_tier).label}")

    click.echo("\ninputs")
    if not manifest_path.exists():
        click.echo(f"  manifest    MISSING {manifest_path}")
        ok = False
    else:
        documents, problems = load_documents(manifest_path)
        pages = sum(1 for item in documents if item.page is not None)
        declared = sum(1 for item in documents if item.declared_total is not None)
        click.echo(
            f"  manifest    {len(documents)} document(s), {len(problems)} problem(s), "
            f"{pages} paginated, {declared} declaring a total"
        )
        ok = ok and not problems

    if not truth_path.exists():
        click.echo(f"  truth       MISSING {truth_path} (measure will not run)")
    else:
        click.echo(f"  truth       {len(load_truth(truth_path))} known code(s)")

    click.echo("\nreading the report")
    click.echo("  A source that declares no total is reported UNVERIFIABLE, not complete.")
    click.echo("  Its codes were extracted; whether any were missed simply cannot be known")
    click.echo("  from the data. `extract` does not fail on that, because most sources are")
    click.echo("  like it -- it fails on a page gap or a shortfall, which are demonstrable.")
    sys.exit(0 if ok else 1)


main.add_command(measure_cmd, name="measure")
main.add_command(detect_cmd, name="detect")


if __name__ == "__main__":  # pragma: no cover
    main()
