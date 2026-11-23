# Headcount — Technical Architecture

## Overview

Extracts item codes from paginated listings, CSV exports, email bodies and attachments, then
answers two separate questions: **were the codes we found real**, and **did we see all the
text there was**.

**Design principle:** those two questions fail independently and must be measured
independently. No model — the accuracy problem is identifier validation and completeness
accounting, both arithmetic.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          CLI (headcount)                                  │
│                                                                              │
│  extract   scan -> grade -> dedupe -> account for completeness                │
│            EXIT 1 on a DEMONSTRABLE problem: a page gap or a shortfall.       │
│            EXIT 0 on an unverifiable source -- most sources are, so failing   │
│            on those would fail always and mean nothing                        │
│  measure   precision and recall at EVERY tier against a truth set             │
│            EXIT 1 if any known code was never extracted                       │
│  check     why one token reaches the tier it does, incl. the expected check   │
│  detect    identify files by CONTENT; exit 1 on any extension mismatch        │
│  doctor    config, inputs, and how to read `unverifiable`                     │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                            identity.py                                        │
│                                                                              │
│  An item code: 10 base-36 chars, leading letter, last is a CHECK CHARACTER    │
│                                                                              │
│  three patterns, deliberately separate -- each answers a different question   │
│    LOOSE    [0-9A-Za-z]{10}          "does it look like one"                  │
│    SHAPED   \b[A-Z][0-9A-Z]{9}\b     "is it shaped like one"                  │
│    RUN      [0-9A-Za-z]+             MAXIMAL runs: what scanning uses         │
│                                                                              │
│  check_character(body)                                                        │
│    POSITIONAL weights 1..9, mod 36                                            │
│    weights because an unweighted sum cannot see a transposition                │
│    mod 36 so the check char is drawn from the SAME alphabet as the body --    │
│      a mod-11 'X' overflow would let a FORMAT test do the CHECKSUM's job      │
│    swapping adjacent i,i+1 shifts the sum by exactly value[i]-value[i+1],     │
│      nonzero mod 36 whenever the chars differ -> transposition detection is   │
│      PROVABLY 100%                                                            │
│    residual: a random shaped token passes ~1/36 (measured 2.79%)              │
│    single substitutions missed 7.9% (d*p a multiple of 36)                    │
│                                                                              │
│  normalise()  strips only the OUTSIDE. Stripping interior hyphens would turn  │
│               ABCD-EFGH-IJ into a candidate -- another system's reference      │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │
        ┌────────────────────────┴────────────────────────┐
        ▼                                                 ▼
┌────────────────────────────────┐   ┌──────────────────────────────────────┐
│      core/extract.py           │   │          core/collect.py             │
│                                │   │                                      │
│  scans MAXIMAL runs, not a     │   │  dedupe()                            │
│  fixed width -- see below      │   │    keyed on token, keeps EVERY        │
│                                │   │    occurrence. {tok: c for c in ...}  │
│  run == 10   -> one candidate  │   │    keeps one and loses provenance     │
│  run 11..20  -> WINDOW SCAN,   │   │    highest tier wins a disagreement   │
│                 emit only      │   │                                      │
│                 windows that   │   │  account_for_pages()                 │
│                 VALIDATE,      │   │    5 verdicts, NOT a boolean:         │
│                 embedded=True  │   │      complete | short | over |        │
│  run > 20    -> NOT MINED      │   │      unverifiable | gap_in_pages      │
│                 a digest is    │   │    GAP outranks totals: a missing     │
│                 not a code     │   │      page is DEFINITE loss            │
│                 with noise on  │   │    compares UNIQUE not occurrences    │
│                 it             │   │    OVER names both causes rather      │
│                                │   │      than pretending to resolve       │
│  grade() -> the strictest tier │   └──────────────────────────────────────┘
│  a token reaches               │
└────────────────────────────────┘
        │                                                 │
        └────────────────────────┬────────────────────────┘
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│              core/measure.py                    render/ + sources.py          │
│                                                                              │
│  precision  of what we found, how much real     text.py                       │
│  recall     of what was there, how much found     n/a NEVER 0.0%              │
│  both float | None: precision undefined with       worst-first completeness    │
│    nothing extracted, recall with an empty         funnel shows AT and        │
│    truth set. 0% reads as "all wrong"                AT-OR-ABOVE separately   │
│                                                                              │
│  scores DISTINCT tokens, not occurrences        export.py                      │
│    else one popular code decides the score        null NEVER 0, header always  │
│                                                                              │
│  by_source(): an aggregate over a CSV export    sources.py                     │
│    and an email body describes NEITHER            detect by MAGIC BYTES first,  │
│    (100% vs 61.5% at the shaped tier)             then text heuristics -- a    │
│    per-source RECALL is labelled diagnostic       zip holds decodable bytes    │
│    because the truth set is global                                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## The Bug: A Genuine Code Inside A Longer Run Was Invisible

A non-overlapping `finditer` for exactly ten characters consumes greedily from the left, so a
code that does not begin on a multiple-of-ten boundary inside a longer run is **never seen**:

```
XXK7X2M9QR4J         -> XXK7X2M9QR      the real code at offset 2, gone
itemK7X2M9QR4J       -> ITEMK7X2M9
req9f2K7X2M9QR4Jabc  -> REQ9F2K7X2
```

A false negative leaves no trace in the output — you cannot see what you did not find.

**And the docstring justifying the design was wrong.** It claimed a word-boundary scan "would
suppress a genuine code embedded in a longer path like `/items/CODE/reviews`". It does not:
slashes are non-word characters, so `\b` boundaries exist and the code matches fine. The design
rested on a premise never checked.

### The fix made things worse, which is why it has a cap

| | extracted | FP | precision | recall | F1 |
|:--|--:|--:|--:|--:|--:|
| original fixed-width scan | 85 | 1 | 98.8% | 93.3% | 0.960 |
| unbounded window scan | 93 | 9 | 90.3% | 93.3% | 0.918 |
| **capped at 2× code length** | 84 | 0 | **100%** | 93.3% | **0.966** |

Recall did not move — this corpus has no embedded genuine codes — while precision fell 8.5
points because it has plenty of 32-character hex digests and the checksum admits ~1/36 of
windows. All nine extra findings were spurious.

Then the observation that settled it: **every spurious hit came from a 32-character digest.**
Nothing in the 11–20 range produced a bad window. A run far longer than a code is not a code
with noise attached — it is a different kind of token. So runs beyond the cap are not mined,
which also beats the original scan: that one mined the first ten characters of every digest,
and this one declines to look.

The cost is on the record: a code buried inside a 32-character run is now lost, and a test
asserts exactly that.

### And a self-inflicted one worth recording

A shell heredoc's escaping turned `r"\b[A-Z]..."` into a literal backslash, so `SHAPED`
matched nothing, every token graded `looks_like`, and extraction returned **0 of 90 codes**.
Caught in one probe because the corpus assertions are absolute counts — a ratio-based check
would have read 0/0 and reported something less alarming.

---

## Data Flow

```
1.  headcount measure -i data/manifest.jsonl -k data/truth.txt
2.  load_documents -> 10 documents across 5 sources
      pagination metadata (page, declared_total) comes from the MANIFEST, because a
      filename cannot carry it and it is the whole basis of completeness accounting
3.  load_truth -> 90 known codes
      NOT derived by running the extractor: that would make recall 100% by construction
4.  per document, candidates_in():
      maximal runs via RUN
        len < 10  -> skip
        len == 10 -> normalise, grade, embedded=False
        11..20    -> window scan, emit only VALIDATED windows, embedded=True
        > 20      -> skip entirely (a digest is not a code with noise on it)
      "please check k7x2m9qr4j today" -> normalise upper-cases -> VALIDATED
      "digest dd4e0a91e448d181b9ceb5ebea340561" -> 32-char run -> not mined
5.  grade -> 153 candidates: 0 looks_like, 5 shaped, 148 validated
6.  measure against truth at every tier
      shaped     P=94.4% R=93.3%   26 distinct shaped-but-invalid tokens admitted
      validated  P=100%  R=93.3%   all 26 rejected, none of them real
7.  dedupe -> 84 findings from 148 occurrences
      6 codes appear in two sources, kept as single findings with two provenances
8.  account_for_pages per source
      listing-a  pages 1-4, declares 40, found 40      -> complete
      listing-b  pages 1,2,4 -- page 3 MISSING         -> gap_in_pages
      export-c   no declared total                     -> unverifiable
      email-d    no declared total                     -> unverifiable
      attach-e   declares 6, found 6                   -> complete
9.  the 6 missed codes == exactly the 6 never present in any document
      so recall 93.3% is a COLLECTION failure with a perfect extractor
10. exit 1, because a known code was never extracted
```

Observed by `setup.sh`:

```
     10 documents, 90 known codes
     shaped    P=94.4%  R=93.3%  F1=0.939
     validated P=100.0%  R=93.3%  F1=0.966
     26 shaped-but-invalid token(s) rejected, none of them real
     6 missed, and all of them were never in the corpus at all
     listing-b: page [3] never seen -- which is the cause of the recall shortfall
     2 source(s) declare no total: ['email-d', 'export-c']
     per-source precision at the SHAPED tier:
       csv_export         100.0%
       email_attachment   100.0%
       email_body         61.5%
       html_listing       100.0%
     6 code(s) appear in more than one source, kept as single findings
     order-independent across 6 shuffles
```

---

## Format Detection

Content first, extension second. Attachments are misnamed constantly, and handing a zip
archive to a text parser reports **zero codes from a file that was full of them** — a zero
indistinguishable from a source that genuinely had none.

Binary signatures are checked before any text heuristic, because a zip container can hold a
run of decodable bytes: asking "is it text" first misclassifies an `.xlsx`.

`_looks_like_delimited` requires **two lines agreeing** on a delimiter count. One line with a
comma is a sentence; two lines with the same number of commas is a table. Without the
agreement check, any prose containing a comma parses as a single-row CSV.

---

## Configuration

`ME_`-prefixed environment variables or a `.env` file. No list-typed fields, since Pydantic
Settings parses those as JSON and `ME_KINDS=csv,html` would fail with a decode error that
reads like a bug in the tool.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ME_", env_file=".env", extra="ignore")

    manifest: Path = Path("data/manifest.jsonl")
    truth: Path = Path("data/truth.txt")
    output_dir: Path = Path("output")
    min_tier: int = 3
```

---

## Testing

```bash
pytest                          # 115 tests, ~0.3s
pytest --cov=multi_extract --cov-report=term-missing
```

| File | Covers |
|:--|:--|
| `conftest.py` | the corpus as session fixtures; `valid_code()` **generates** from the checksum so a scheme change fails loudly instead of leaving stale literals |
| `test_identity_extract.py` | round trip; both raising paths; the **provable** transposition property; the substitution limit asserted as small *rather than zero*; the 1/36 residual over 40,000 trials; grading; **the embedded-code bug** in five embeddings; the run cap in both directions |
| `test_collect_measure_render.py` | dedup keeping provenance; all five completeness verdicts and the precedence; both `None` cases; per-source scoring; `n/a` in render; null-not-zero in export; format detection incl. the prose-with-a-comma case; loading |
| `integration/test_cli.py` | all five commands, both exit-code reasons, stdout staying pipeable |

The property tests run against `data/`, so editing the corpus and breaking an invariant fails
the build. Two exist to stop the others being vacuous: every tier and at least four source
kinds must be exercised. A pre-commit hook and a CI step both verify every code in `truth.txt`
actually validates — a stale literal there would make every recall figure quietly wrong.

Nothing is mocked, because nothing talks to a network.
