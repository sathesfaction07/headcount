# ADR 002: Extraction accuracy and collection completeness are measured separately, and completeness is usually unknowable

## Status

Accepted

## Context

"Did we get all the item codes?" sounds like one question. It is two, and they fail
independently.

**Extraction accuracy** — of the text we scanned, did we correctly identify the codes in it?
Measurable against a truth set, in both directions: precision and recall.

**Collection completeness** — did we scan all the text there was? A page dropped by a fetcher,
an attachment that failed to parse, a listing that paginated inconsistently. **No amount of
care in the extractor can detect this**, because the missing text was never seen.

Collapsing them produces a number that cannot be acted on. Suppose recall is 93%. Is the
extractor missing codes that are present, or is it perfect on text that was incomplete? Those
have opposite remedies — tighten the parser, or re-fetch the source — and one number cannot
distinguish them.

Worse: completeness is usually **unanswerable from the extracted data alone.** You know you
have all of something only if the source told you how many there were, and most do not.

## Decision

**Two independent reports, and completeness is a five-valued verdict rather than a boolean.**

```python
class Completeness(StrEnum):
    COMPLETE = "complete"            # a declared total exists and was matched
    SHORT = "short"                  # a declared total exists and was NOT matched
    OVER = "over"                    # more found than declared
    UNVERIFIABLE = "unverifiable"    # no declared total: nothing can be concluded
    GAP_IN_PAGES = "gap_in_pages"    # page numbers are not contiguous
```

A boolean would force `UNVERIFIABLE` to masquerade as either success or failure. It is
neither, and it is the **most common case** — two of the five sources in the shipped corpus.

### The precedence, and why

`GAP_IN_PAGES` outranks every total comparison. A missing page number means data was
*definitely* missed; a total mismatch has innocent explanations. It is also the only
completeness signal available **without** a declared total, which makes it the one check that
always applies.

### Unique codes, never occurrences

The comparison against a declared total uses distinct codes. A code legitimately appears on
several pages of a listing, so comparing occurrences would report a spurious surplus on any
source that repeats itself — and repetition is normal.

The subtlety that keeps this a function rather than a subtraction: a source's declared total
might itself count occurrences rather than distinct items, in which case even the unique
comparison is apples-to-oranges. Nothing here can detect that, so `OVER` is reported as an
**ambiguity naming both possible causes** rather than as a failure.

### Exit codes follow what is demonstrable

`extract` exits non-zero on `GAP_IN_PAGES` or `SHORT` — problems you can prove. It exits
**zero** on `UNVERIFIABLE`, despite that being the weakest state, because most sources are
unverifiable and a check that always fails carries no information. The output says so in a
full sentence instead:

```
[unverifiable] export-c: 18 unique code(s) found. The source declares no total, so
               whether anything was missed CANNOT be determined from this data.
```

`measure` exits non-zero if any known code was never extracted, because a miss is the failure
that nothing in the normal output reveals.

### Dedup keeps provenance

The obvious `{c.token: c for c in candidates}` keeps one occurrence and discards the rest,
losing the answer to "where did this come from" for every code seen more than once. That is
the question actually asked when a code turns out to be wrong. So a `Finding` carries every
`Candidate`, and six codes in the shipped corpus appear in two sources each as a single
finding with two provenances.

### Precision and recall are `float | None`

Precision is undefined when nothing was extracted; recall when the truth set is empty.
Reporting 0% for the first reads as "everything we found was wrong" rather than "we found
nothing".

They are genuinely independent, which a test of mine got wrong: with an empty extraction over
a non-empty truth set, precision is `n/a` and recall is a real **0.0%** — there were codes,
we found none of them. My assertion that no `0.0%` appeared anywhere failed, correctly.

### Per-source precision, and an honest caveat on per-source recall

An aggregate over a structured CSV export and a free-text email body describes neither: at
the `shaped` tier the CSV runs at 100% and the email body at 61.5%. But the truth set is
global, so a code present only in source A counts as a miss for source B. The recall column
is printed with a footnote marking it diagnostic rather than being quietly wrong or silently
omitted.

## The vindication

On the shipped corpus the strict tier reports **precision 100%, recall 93.3%**. The six
missing codes are *exactly* the six that were never in the corpus at all, because `listing-b`
page 3 was never fetched:

```
  [gap_in_pages] listing-b: page(s) [3] were never seen. Anything on them was missed,
                 regardless of the totals.
```

So the recall shortfall is entirely a collection failure with a perfect extractor, and the
completeness report names the cause. There is a property test asserting the two sets are
identical — if the extractor ever starts genuinely missing codes, that test fails rather than
the shortfall being absorbed into an existing 93.3%.

## Validation

- `TestCompletenessIsNotABoolean` — all five verdicts, the gap-outranks-totals precedence,
  a gap detected with no declared total, and unique-not-occurrences against a total
- `TestDedupeKeepsProvenance` — occurrences survive, sources are listed, ordering is stable
- `TestMeasurement` — both `None` cases, distinct-token scoring, precision rising with
  strictness
- `TestPropertiesOverTheShippedCorpus` — precision is exactly 1.0; the misses equal the codes
  never present; the gap is page 3; order independence across 5 shuffles
- `setup.sh` and CI both re-derive the whole chain on a fresh clone

## Consequences

**Good**

- A recall shortfall can be attributed to extraction or to collection, which are different
  jobs to fix
- `UNVERIFIABLE` makes "we cannot tell" a first-class result instead of silence that reads as
  success
- A page gap is caught without any cooperation from the source
- Exit codes gate on what is demonstrable, so the check stays meaningful
- Provenance survives dedup, so a wrong code can be traced to the source that supplied it

**Bad**

- Five verdicts is a lot of vocabulary for what a reader may think is a yes/no question, and
  `UNVERIFIABLE` will be the answer most of the time — which risks being tuned out
- `OVER` is genuinely ambiguous and the tool cannot resolve it. Naming both causes is the best
  available, and it is still a shrug
- Completeness is per-source, so there is no total. Summing `unique_found` across sources
  double-counts anything shared, and the report does not offer a combined figure at all
- A source that repeats a page (rather than dropping one) is invisible: dedup absorbs it and
  page numbers stay contiguous. Only a declared total would catch it

**Neutral**

- `declared_total` takes the maximum when pages disagree, on the reasoning that a listing
  usually reports the full count on every page. A source that reports a per-page count would
  be misread, and nothing detects that
- Findings sort by token so two runs agree; occurrence order within a finding sorts by
  `(source, page, offset)` for the same reason
- `problem_sources` and `unverifiable_sources` are separate properties rather than one
  severity-ranked list, because only the first should ever fail a build
