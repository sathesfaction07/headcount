"""The item code scheme: what makes a token a genuine identifier rather than one shaped like it.

An item code is **10 base-36 characters**, the first a letter, the last a weighted check
character over the preceding nine. That shape is the entire accuracy problem, because a
10-character alphanumeric run with no separators collides with almost everything else that
appears in the same documents: order references, tracking numbers, hex digests, base64
fragments, and other platforms' internal SKUs.

So there are three progressively stricter questions, and they give materially different
answers on the same text:

    does it LOOK like one     10 alphanumerics in a row
    is it SHAPED like one     first char a letter, correct length, uppercase
    does it VALIDATE          the check character agrees

The third is the only one that rejects a well-formed look-alike, and it does not reject all
of them: one in thirty-six random tokens of the right shape passes by chance. That residual
is measured rather than glossed over -- see `core/measure.py` and the README.
"""

from __future__ import annotations

import re
import string

# Base-36, digits before letters, so '0'->0 and 'Z'->35.
ALPHABET = string.digits + string.ascii_uppercase
VALUES = {char: index for index, char in enumerate(ALPHABET)}

CODE_LENGTH = 10
BODY_LENGTH = CODE_LENGTH - 1

# Deliberately three separate patterns rather than one configurable one. Each answers a
# different question and the difference between their results is the finding, so a caller
# choosing a "strictness level" on one regex would hide the comparison.
LOOSE = re.compile(r"[0-9A-Za-z]{10}")
SHAPED = re.compile(r"\b[A-Z][0-9A-Z]{9}\b")
# Maximal alphanumeric runs. Scanning uses THIS rather than the fixed-width LOOSE
# pattern, because a fixed-width non-overlapping scan cannot see a code that starts
# part-way into a longer run -- see `core/extract.candidates_in`.
RUN = re.compile(r"[0-9A-Za-z]+")

# The modulus is 36 so the check character is drawn from the same alphabet as the body. A
# smaller modulus (11, as ISBN-10 uses) would need an out-of-alphabet symbol like 'X' for
# one residue, which makes the last position distinguishable from the rest -- and that
# would let a FORMAT check do work that should belong to the CHECKSUM, muddying exactly the
# comparison this module exists to make.
MODULUS = len(ALPHABET)


def check_character(body: str) -> str:
    """The check character for a 9-character body.

    Positional weights (1..9), not a plain sum. An unweighted sum cannot see a
    transposition -- 'AB' and 'BA' produce the same total -- and transposition is the most
    common way a human retyping a code gets it wrong.

    Raises on a body of the wrong length or with a character outside the alphabet, rather
    than returning something. A caller that has already failed to produce a well-formed
    body has not asked a meaningful question, and returning a plausible character would
    manufacture a valid-looking code out of malformed input.
    """
    if len(body) != BODY_LENGTH:
        raise ValueError(f"a body is {BODY_LENGTH} characters, got {len(body)}: {body!r}")
    total = 0
    for position, char in enumerate(body, start=1):
        try:
            total += VALUES[char] * position
        except KeyError as error:
            raise ValueError(f"{char!r} is not in the code alphabet") from error
    return ALPHABET[total % MODULUS]


def is_valid(token: str) -> bool:
    """Whether a token is a well-shaped code whose check character agrees.

    Case-sensitive on purpose: see `normalise` for why upper-casing is a separate,
    deliberate step rather than something done here for convenience.
    """
    if not SHAPED.fullmatch(token):
        return False
    return check_character(token[:BODY_LENGTH]) == token[-1]


def make_code(body: str) -> str:
    """Complete a 9-character body into a valid code. Used to build fixtures."""
    return body + check_character(body)


def normalise(token: str) -> str:
    """Upper-case and strip surrounding punctuation, without touching the interior.

    Sources are inconsistent about case and about wrapping codes in brackets, quotes, or
    trailing full stops. Normalising makes those recoverable.

    What this deliberately does NOT do is remove interior characters. Stripping hyphens or
    spaces from the middle would turn `ABCD-EFGH-IJ` into a 10-character candidate, and
    that token is far more likely to be some other system's dashed reference than an item
    code -- so "helpfully" cleaning it manufactures a false positive out of a token that was
    unambiguously not a code.
    """
    return token.strip(string.punctuation + string.whitespace).upper()
