# Headcount

Item codes arrive scattered across paginated listings, CSV exports, email bodies and
attachments, next to order numbers, tracking IDs and hashes that look just like them.
Extracting them is the easy half. Headcount also answers the question a total cannot: **did
we get all of them?** It validates every candidate against the code's check character, and for
each source says whether completeness is proven, contradicted (a page never arrived), or
impossible to know.

All of it is identifier rules and bookkeeping; nothing is learned.

---

## Why This Is Hard

An item code is **10 base-36 characters**, first a letter, last a weighted check character. That shape collides with almost everything else in the same documents: order references, tracking numbers, hex digests, base64 fragments, other systems' SKUs.

So there are three progressively stricter questions, and on real input they give materially different answers:

| Tier | Test | On the shipped corpus |
|:--|:--|:--|
| `looks_like` | ten alphanumerics in a row | precision 94.4% |
| `shaped` | correct length, leading letter, uppercase | precision 94.4% |
| `validated` | the check character agrees | **precision 100%** |

The checksum is the only test that rejects a well-formed look-alike. On this corpus it rejected **26 shaped-but-invalid tokens, none of which was real.**

It does not reject all of them. A random well-shaped token passes **one time in thirty-six** (measured: 2.79% over 200,000 trials against 1/36 = 2.78%). That residual is reported rather than glossed over.

---

## The Two Numbers That Matter

```
accuracy
  truth set: 90 known code(s)
  tier          extracted    TP    FP    FN  precision   recall      F1
  looks_like           89    84     5     6      94.4%    93.3%   0.939
  shaped               89    84     5     6      94.4%    93.3%   0.939
  validated            84    84     0     6     100.0%    93.3%   0.966

completeness
  [gap_in_pages] listing-b: page(s) [3] were never seen. Anything on them was
                 missed, regardless of the totals.
  [unverifiable] email-d: 8 unique code(s) found. The source declares no total, so
                 whether anything was missed CANNOT be determined from this data.
  [unverifiable] export-c: 18 unique code(s) found. ...
  [complete]     attach-e: 6 unique matches the declared 6.
  [complete]     listing-a: 40 unique matches the declared 40.
```

**Recall is 93.3%, and not one point of that shortfall is an extraction failure.** The six missing codes are exactly the six that were never in the corpus, because `listing-b` page 3 was never fetched. The completeness accounting caught it, and there is a test asserting the two sets are identical.

That is the point of separating the two measurements. Extraction accuracy and collection completeness fail independently, and a single "did we get them all" number cannot tell you which one broke.

---

## Completeness Is Not A Boolean

Five verdicts, because "we got everything" and "we cannot tell whether we got everything" are different claims:

| Verdict | Meaning |
|:--|:--|
| `complete` | a declared total exists and was matched |
| `short` | a declared total exists and was not matched — codes are unaccounted for |
| `over` | more found than declared — either the total counts something else, or false positives inflated us |
| `gap_in_pages` | page numbers are not contiguous. **Data was definitely missed** |
| `unverifiable` | no declared total. Nothing can be concluded |

`gap_in_pages` outranks the totals, because a missing page means data was definitely lost while a total mismatch has innocent explanations. And it is the only completeness signal available without a declared total, which most sources do not provide.

`extract` exits non-zero for a *demonstrable* problem — a gap or a shortfall — but **not** for an unverifiable source. Most sources declare no total, so failing on those would fail always and the signal would be worthless. It says so loudly in the output instead.

---

## Running

```bash
bash setup.sh                 # derives the checksum properties, not just installs
source .venv/bin/activate

headcount doctor
headcount extract  -i data/manifest.jsonl
headcount measure  -i data/manifest.jsonl -k data/truth.txt
headcount extract  -i data/manifest.jsonl -t shaped     # see the trade
headcount check    K7X2M9QR4J
headcount detect   some-attachment.txt
```

`check` explains a single token:

```
$ headcount check E4G7VSUOD6
  input      'E4G7VSUOD6'
  normalised 'E4G7VSUOD6'
  tier       shaped
  why        check character is '6' but should be '4'
             shaped like a code, but not one -- most likely another system's
```

| Command | Purpose | Exit |
|:--|:--|:--|
| `extract` | Extract, dedupe, account for completeness | **1** on a page gap or a shortfall |
| `measure` | Precision and recall at every tier | **1** if any known code was never extracted |
| `check` | Why one token reaches the tier it does | 1 if it does not validate |
| `detect` | Identify files by content | 1 on any extension mismatch |
| `doctor` | Config, inputs, how to read the report | 1 if inputs unreadable |

---

## Format Detection Reads Content, Not Extensions

Attachments are misnamed constantly. A spreadsheet saved as `.txt`, a CSV called `.xls`, an HTML page emailed as `.doc`. Trusting the extension hands a zip archive to a text parser and reports **zero codes from a file that was full of them** — and zero is indistinguishable from a source that genuinely had none.

```
export.txt   content=zip        MISMATCH
book.xlsx    content=zip        consistent
report.txt   content=pdf        MISMATCH
page.doc     content=markup     MISMATCH
rows.csv     content=delimited  consistent
note.txt     content=text       consistent
```

Binary signatures are checked before any text heuristic, because a zip container can hold a run of decodable bytes — so asking "is it text" first misclassifies an `.xlsx` and extracts nothing from it.

---

## Testing

```bash
pytest                          # 115 tests, ~0.3s
pytest --cov=multi_extract --cov-report=term-missing
```

Test codes are **generated from the checksum**, not hard-coded. A change to the scheme makes the tests fail loudly instead of leaving them asserting against stale literals that no longer validate. A pre-commit hook and a CI step both verify that every code in `truth.txt` actually validates, for the same reason.

The property tests run against the shipped corpus, so editing `data/` and breaking an invariant fails the build. Two of them exist to stop the others being vacuous: every tier and at least four source kinds must be exercised.

---

## Tradeoffs and Decisions I Made Along the Way

**A genuine code embedded in a longer alphanumeric run was invisible.** A non-overlapping `finditer` for exactly ten characters consumes greedily from the left, so a code that does not begin on a multiple-of-ten boundary inside a longer run is never seen at all:

```
XXK7X2M9QR4J         -> XXK7X2M9QR      the real code at offset 2, gone
itemK7X2M9QR4J       -> ITEMK7X2M9
req9f2K7X2M9QR4Jabc  -> REQ9F2K7X2
```

This is a **false negative**, which is the failure mode nothing in the output reveals — you cannot see what you did not find. It arises from broken exports and concatenated log fields, both of which the spec calls out.

**And the docstring I had written to justify the design was wrong.** I claimed a word-boundary-anchored scan "would suppress a genuine code embedded in a longer path like `/items/CODE/reviews`". It does not: slashes are non-word characters, so `\b` boundaries exist and the code matches fine. I only found out by probing it. The design rested on a premise I had never checked, which is the second time in this portfolio that an unverifiable comment turned out to be false.

**Fixing it made things measurably worse, and that is why the fix has a cap.** Window-scanning every long run restored the embedded-code recall and then:

| | extracted | FP | precision | recall | F1 |
|:--|--:|--:|--:|--:|--:|
| original fixed-width scan | 85 | 1 | 98.8% | 93.3% | 0.960 |
| unbounded window scan | 93 | 9 | **90.3%** | 93.3% | 0.918 |
| capped at 2× code length | 84 | 0 | **100%** | 93.3% | **0.966** |

Recall did not move at all, because this corpus has no embedded genuine codes — while precision fell 8.5 points, because it has plenty of 32-character hex digests and the checksum admits about one window in thirty-six. All nine extra findings were spurious.

Then the useful observation: **every single spurious hit came from a 32-character digest.** Nothing in the 11-to-20 range produced a bad window. That is not luck — a run far longer than a code is not a code with noise stuck to it, it is a different kind of token. So runs beyond the cap are not mined at all, which beats the original scan too: that one dutifully mined the first ten characters of every digest, and this one declines to look.

The cost is stated rather than hidden: a code genuinely buried inside a 32-character run is now lost. There is a test asserting exactly that, so the sacrifice is on the record.

**A shell heredoc silently corrupted a regex and every test still had something to say about it.** Escaping in a generated edit turned `r"\b[A-Z]..."` into a literal backslash, so `SHAPED` matched nothing, every token graded as `looks_like`, and extraction returned **zero of 90 codes**. Caught within one probe because the corpus assertions are absolute numbers rather than ratios — a percentage-based check would have read 0/0 and reported something less alarming.

**Deduplication keeps every occurrence, not a count.** The obvious `{c.token: c for c in candidates}` keeps one and discards the rest, which loses the answer to "where did this come from" for every code seen more than once — and that is the question actually asked when a code turns out to be wrong. Six codes in this corpus appear in two sources; each is one finding with two provenances.

**Completeness compares UNIQUE codes to the declared total, never occurrences.** A code legitimately appears on several pages of a listing, so comparing occurrences would report a spurious surplus on any source that repeats itself — and repetition is normal. The subtlety that keeps this a function rather than a subtraction: a source's declared total might itself count occurrences, in which case even the unique comparison is apples-to-oranges. Nothing here can detect that, so `over` is reported as an ambiguity naming both possible causes rather than as a failure.

**The check character is mod-36, not mod-11.** ISBN-10 uses mod-11 and needs an out-of-alphabet `X` for one residue, which makes the last position visibly different from the rest — and that would let a *format* check do work belonging to the *checksum*, muddying the exact comparison this tool exists to make.

**Positional weights, not a plain sum.** An unweighted sum cannot see a transposition — `AB` and `BA` give the same total — and transposition is the commonest way a human retyping a code gets it wrong. With weights 1..9, swapping adjacent positions shifts the sum by exactly `value[i] − value[i+1]`, which is nonzero mod 36 whenever the characters differ. So **adjacent transposition detection is provably 100%**, not merely likely.

It is not a perfect check and the docs do not pretend otherwise: single-character substitutions go undetected **7.9%** of the time, because a delta *d* at position *p* is invisible when *d·p* is a multiple of 36. Measured over 194,413 trials.

**`normalise` strips only the outside of a token.** Upper-casing and removing wrapping brackets or a trailing full stop recovers real codes. Stripping *interior* hyphens would turn `ABCD-EFGH-IJ` into a ten-character candidate — and that token is far more likely to be another system's dashed reference, so "helpfully" cleaning it manufactures a false positive out of something unambiguously not a code.

**Precision and recall are `float | None`.** Precision is undefined when nothing was extracted; recall when the truth set is empty. Reporting 0% for the first would read as "everything we found was wrong" rather than "we found nothing". They are genuinely independent: with an empty extraction over a non-empty truth set, precision is `n/a` and recall is a real **0.0%**. A test of mine asserted no `0.0%` appeared anywhere and failed — correctly, because the code was right and the assertion was too broad.

**Per-source precision is reported, per-source recall is labelled diagnostic.** An aggregate over a structured CSV export and a free-text email body describes neither: at the `shaped` tier the CSV runs at 100% and the email body at 61.5%, and which one dominates an average depends only on which is larger. But the truth set is global, so a code present only in source A counts as a miss for source B — the per-source recall column carries a footnote saying so rather than being quietly wrong.

**Three regexes, not one with a strictness parameter.** Each answers a different question, and the *difference between their answers* is the finding. A single configurable pattern would hide the comparison.

**`Tier` is an `IntEnum`.** Ordering is what makes it a tier rather than a label, so `candidate.tier >= Tier.VALIDATED` reads naturally. The wart is real: it compares equal to bare ints, so `tier == 2` works and shouldn't.

**The tier funnel shows two columns that could both be called "validated".** "Graded AT" counts tokens that stopped at a tier; "at or above" is what a run reporting at that tier includes. They differ sharply — 148 versus 148 at the strict tier but 0 versus 153 at the loosest. The header names both, and that ambiguity caught one of my own integration tests, which had grabbed the funnel row instead of the accuracy row.

**No Playwright, despite the spec suggesting it.** Fetching is a solved problem handled by a sibling project. What is unsolved here is deciding whether a ten-character token is an identifier, and that needs no browser. The manifest format carries the pagination metadata a fetcher would produce, so the two compose.

---

## Configuration

```bash
ME_MANIFEST=data/manifest.jsonl
ME_TRUTH=data/truth.txt
ME_MIN_TIER=3
```

---

## Stack

| Concern | Choice |
|:--|:--|
| Language | Python 3.11+ |
| Models + config | Pydantic, Pydantic Settings |
| CLI | Click |
| Output | text report, JSON Lines, CSV, JSON |
| Tests | pytest, Click's CliRunner |
| Lint | ruff |
| Models/AI | none, deliberately |

The corpus is synthetic: 90 codes planted across five sources in the contexts they actually occur in — table cells, URLs, quoted lists, prose — alongside the look-alikes that genuinely collide. One listing is missing a page on purpose. Nothing is arranged to flatter the extractor; the digests that defeat the unbounded window scan are there because real documents contain digests.
