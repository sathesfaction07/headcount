"""Finding candidate item codes in text.

Scanning is done ONCE at the loosest tier, and each hit is then graded upward. The
alternative -- running three regexes and unioning the results -- looks equivalent and is
not: the same span would appear up to three times, and the counts a reader compares between
tiers would be counts of matches rather than counts of tokens.

Grading rather than filtering is what makes the comparison honest. A token rejected at the
validated tier is still reported at the tier it reached, so "we found 412 things that look
like codes, of which 180 are shaped right, and 63 validate" is a statement about one
population rather than three separate searches.
"""

from __future__ import annotations

import re

from multi_extract.identity import CODE_LENGTH, RUN, SHAPED, is_valid, normalise
from multi_extract.models import Candidate, Document, Tier

CONTEXT_WIDTH = 24

# Twice a code length: enough to hold two concatenated codes, or one code plus ten characters
# of adjacent noise from a broken export. Beyond this a run is some other kind of token and
# mining it only manufactures false positives -- see `candidates_in` for the measurement.
MAX_ENCLOSING_RUN = CODE_LENGTH * 2


def grade(token: str) -> Tier:
    """The strictest tier a token reaches.

    Normalisation happens before grading, so a lower-case or bracket-wrapped code is graded
    on its cleaned form. `normalise` deliberately does not touch the interior of the token --
    stripping interior hyphens would manufacture candidates out of other systems' dashed
    references.
    """
    cleaned = normalise(token)
    if not SHAPED.fullmatch(cleaned):
        return Tier.LOOKS_LIKE
    return Tier.VALIDATED if is_valid(cleaned) else Tier.SHAPED


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - CONTEXT_WIDTH)
    right = min(len(text), end + CONTEXT_WIDTH)
    return text[left:right].replace("\n", " ").strip()


def _runs(text: str) -> list[tuple[int, str]]:
    """Maximal alphanumeric runs, as (offset, text).

    Maximal, not fixed-width -- that distinction is the whole reason this exists. See
    `candidates_in`.
    """
    return [(match.start(), match.group(0)) for match in RUN.finditer(text)]


def candidates_in(document: Document) -> list[Candidate]:
    """Candidates in the document, graded.

    Works over MAXIMAL alphanumeric runs rather than a fixed-width scan, and the difference is
    a recall bug that had to be measured to be found.

    A non-overlapping `finditer` for exactly ten characters is the obvious implementation. It
    consumes greedily from the left, so a genuine code not beginning on a multiple-of-ten
    boundary within a longer run is **never seen at all**:

        XXK7X2M9QR4J         -> XXK7X2M9QR     the real code at offset 2 is invisible
        itemK7X2M9QR4J       -> ITEMK7X2M9
        req9f2K7X2M9QR4Jabc  -> REQ9F2K7X2

    That is a false negative, and nothing in the output reveals one. It arises from broken
    exports and concatenated log fields, both of which the spec calls out. A word-boundary
    scan does NOT have this problem and does not fix it either -- `/items/CODE/reviews`
    already sits on clean boundaries, so boundaries were never what was at stake here.

    So: a run of exactly ten characters is graded as one candidate, and a run of eleven to
    `MAX_ENCLOSING_RUN` characters is window-scanned -- emitting only windows that VALIDATE,
    marked `embedded=True`.

    The cap is the part that had to be measured. Window-scanning every long run fixed the
    recall bug and made the shipped corpus WORSE overall: precision fell from 98.8% to 90.3%
    while recall did not move at all, because this corpus contains no embedded genuine codes
    but plenty of 32-character hex digests. All nine extra findings were spurious -- the
    checksum admits about one window in thirty-six, and a 32-character run offers 23 windows.

    Every single spurious hit came from a 32-character digest. Nothing in the eleven-to-twenty
    range produced a bad window. That is not a coincidence: a run FAR longer than a code is
    not a code with noise stuck to it, it is a different kind of token altogether -- a digest,
    a session identifier, a base64 fragment. So runs beyond the cap are not mined at all.

    With the cap the shipped corpus reaches 100% precision, better than the original
    fixed-width scan managed, because that scan mined the first ten characters of every digest
    whereas this one declines to look. Recall protection for the realistic broken-export case
    is retained and is covered by tests.
    """
    found: list[Candidate] = []

    def add(token: str, offset: int, *, embedded: bool) -> None:
        found.append(
            Candidate(
                token=token,
                tier=grade(token),
                source_id=document.source_id,
                kind=document.kind,
                page=document.page,
                offset=offset,
                embedded=embedded,
                context=_context(document.text, offset, offset + CODE_LENGTH),
            )
        )

    for start, run in _runs(document.text):
        if len(run) < CODE_LENGTH:
            continue
        if len(run) == CODE_LENGTH:
            add(normalise(run), start, embedded=False)
            continue

        # Far longer than a code: not a code with noise around it, but a different kind of
        # token. Mining it yields ~1 spurious hit per 36 windows and no real ones.
        if len(run) > MAX_ENCLOSING_RUN:
            continue

        # Long enough to hide a code plus lost delimiters, short enough to be worth mining.
        for index in range(len(run) - CODE_LENGTH + 1):
            window = normalise(run[index : index + CODE_LENGTH])
            if is_valid(window):
                add(window, start + index, embedded=True)

    return found


def extract(documents: list[Document], *, min_tier: Tier = Tier.VALIDATED) -> list[Candidate]:
    """Candidates from every document at or above `min_tier`.

    The default is VALIDATED because that is what a caller should act on. But the lower
    tiers are reachable, and `measure` reports all three, because the difference between them
    is the only evidence about whether the strict tier is discarding real codes.
    """
    result: list[Candidate] = []
    for document in documents:
        result.extend(
            item for item in candidates_in(document) if item.tier >= min_tier
        )
    return result


# Tokens that are shaped like codes but demonstrably are not, kept so fixtures and tests can
# refer to a shared vocabulary of the look-alikes that actually occur.
LOOKALIKE_PATTERNS: dict[str, re.Pattern[str]] = {
    "hex_digest": re.compile(r"\b[0-9a-f]{32,}\b"),
    "order_reference": re.compile(r"\b\d{3}-\d{7}-\d{7}\b"),
    "tracking_number": re.compile(r"\b1Z[0-9A-Z]{16}\b"),
}
