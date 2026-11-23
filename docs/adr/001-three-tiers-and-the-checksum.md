# ADR 001: Three validation tiers, graded not filtered, with the checksum as the only real test

## Status

Accepted

## Context

An item code is ten base-36 characters. That shape is the accuracy problem, because a
ten-character alphanumeric run with no separators collides with nearly everything else that
appears in the same documents:

| Look-alike | Why it collides |
|:--|:--|
| hex digest | 32 characters of `[0-9a-f]`, any ten of which look like a code |
| order reference | digits and separators; a fragment can match |
| tracking number | uppercase alphanumeric, right character class |
| other systems' SKUs | **exactly the right shape**, deliberately so |

The last row is the hard one. A different platform's identifier can be ten uppercase
alphanumerics beginning with a letter — indistinguishable from ours by any structural test.

So "extract the item codes" has three defensible answers and they differ materially:

- **Loose.** Ten alphanumerics in a row. Maximum recall, and it captures every digest
  fragment and session-token slice in the corpus.
- **Structural.** Correct length, leading letter, uppercase, word boundaries. Better, and it
  still admits every foreign SKU.
- **Validated.** The check character agrees. The only test that can reject a well-formed
  look-alike.

Choosing one and shipping it hides the choice. Worse, it makes the choice unreviewable: with
a single number there is no way to tell whether the strict tier is discarding real codes or
correctly rejecting noise.

## Decision

**A code carries a weighted mod-36 check character, and extraction reports all three tiers.**

```python
def check_character(body: str) -> str:
    total = sum(VALUES[char] * position for position, char in enumerate(body, start=1))
    return ALPHABET[total % MODULUS]
```

### Mod-36, not mod-11

ISBN-10 uses mod-11 and needs an out-of-alphabet `X` for the eleventh residue. That makes
the final position visibly different from the rest — which would let a *format* check reject
tokens the *checksum* is supposed to catch, muddying the exact comparison this design exists
to expose. Mod-36 draws the check character from the same alphabet as the body, so the tiers
stay cleanly separated.

### Positional weights, not a plain sum

An unweighted sum cannot see a transposition: `AB` and `BA` produce the same total, and
transposition is the commonest error a human retyping a code makes.

With weights 1..9, swapping adjacent positions *i* and *i+1* shifts the sum by exactly
`value[i] - value[i+1]`, which is nonzero mod 36 whenever the characters differ. So
**adjacent-transposition detection is provably 100%**, not merely likely. `setup.sh` derives
it over 4,000 cases rather than trusting the argument.

### Grading, not filtering

Scanning happens once, and each hit is graded upward to the strictest tier it reaches. Three
separate regexes unioned would count the same span up to three times, so the numbers a reader
compares between tiers would be counts of matches rather than counts of tokens.

A token rejected at the validated tier is still reported at the tier it reached. That makes
"153 look like codes, 5 stopped at shaped, 148 validate" a statement about one population.

### Honest about what the checksum cannot do

Two limits, both measured and both stated in the README rather than implied away:

- A random well-shaped token passes **one time in thirty-six** — 2.79% measured over 200,000
  trials against 1/36 = 2.78%. On the shipped corpus that residual produced exactly one
  false positive under the original scan: `DD4E0A91E4`, the first ten characters of the
  digest `dd4e0a91e448d181...`, valid by pure chance.
- Single-character substitutions go undetected **7.9%** of the time, because a delta *d* at
  position *p* is invisible when *d·p* is a multiple of 36.

## Validation

`TestCheckCharacter` covers the round trip, both raising paths, the provable transposition
property over 2,000 cases, the substitution limit asserted as *small rather than zero*, and
the 1/36 residual measured over 40,000 trials.

On the shipped corpus the tiers separate as designed: 26 distinct shaped-but-invalid tokens
were rejected by the checksum and **none of them was in the truth set**, so every rejection
was correct. Precision rises 94.4% → 94.4% → 100% across the three tiers.

Test codes are **generated from the checksum**, never hard-coded, so a change to the scheme
fails the suite instead of leaving stale literals asserting against a scheme that no longer
exists. A pre-commit hook and a CI step both verify every code in `truth.txt` validates.

## Consequences

**Good**

- The only test that can reject a foreign SKU exists, and it is the default
- The precision/recall trade is visible at every tier on the same input, so the tier choice
  is reviewable rather than baked in
- Transposition detection is provable, not empirical
- The residual false-positive rate is a measured number in the docs, not an omission
- `check` explains one token — including what the check character *should* have been — which
  turns "why was this rejected" into a one-command answer

**Bad**

- Ten characters of payload buy only ~1/36 of discrimination. A longer check field would do
  much better, and the scheme is fixed by the data rather than chosen
- `Tier` is an `IntEnum` so ordering works, which means it compares equal to bare ints:
  `tier == 2` is accepted and should not be
- Three tiers is three numbers, and a reader who wants one answer has to be told which
- The check character depends on the whole body, so a code truncated by a column-width limit
  fails validation rather than being recoverable as a partial match

**Neutral**

- `LOOSE` remains in the module although scanning now uses `RUN`. It is the definition of the
  loosest tier and is still what `grade` implicitly answers against; removing it would leave
  the tier undefined in code
- `normalise` is deliberately separate from `is_valid`, so case handling is a decision at the
  call site rather than a convenience buried in the validator
- The alphabet excludes nothing. Real schemes often drop `I`, `O`, `0`, `1` to reduce human
  transcription error; this one does not, because the data it models does not
