#!/usr/bin/env bash
set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${BLUE}==>${NC} $1"; }
ok()   { echo -e "${GREEN} ok ${NC} $1"; }
fail() { echo -e "${RED}fail${NC} $1"; exit 1; }

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            cat <<'USAGE'
Usage: bash setup.sh

No options and no extras. Nothing here needs credentials, a network, or a model: the
accuracy problem is identifier validation, which is arithmetic.
USAGE
            exit 0 ;;
        *) fail "unknown option: $arg (try --help)" ;;
    esac
done

info "Checking Python"
command -v python3 >/dev/null 2>&1 || fail "python3 not found (macOS: brew install python@3.12)"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
[ "$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')" = "1" ] \
    || fail "Python 3.11+ required (StrEnum, typing.Self). Found $PY_VER."
ok "Python $PY_VER"

info "Creating virtual environment"
[ -d .venv ] || python3 -m venv .venv || fail "could not create .venv"
ok ".venv ready"

info "Installing dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]" || fail "pip install failed"
ok "dependencies installed"

info "Verifying imports"
.venv/bin/python - <<'PYCHECK' || fail "core imports failed"
import click                                                    # noqa: F401
from pydantic_settings import BaseSettings                       # noqa: F401
from multi_extract.core.collect import collect                   # noqa: F401
from multi_extract.core.extract import candidates_in             # noqa: F401
from multi_extract.core.measure import measure                   # noqa: F401
from multi_extract.render.text import render                     # noqa: F401
PYCHECK
ok "imports resolve"

info "Deriving the checksum's properties rather than trusting them"
.venv/bin/python - <<'PYCHECK' || fail "a checksum property does not hold"
import random
import string

from multi_extract.identity import (
    ALPHABET, BODY_LENGTH, MODULUS, check_character, is_valid, make_code,
)

rng = random.Random(2026)

def body():
    return rng.choice(string.ascii_uppercase) + "".join(rng.choice(ALPHABET) for _ in range(8))

# 1. Round trip.
for _ in range(2000):
    assert is_valid(make_code(body()))
print("     2000 completed bodies all validate")

# 2. EVERY adjacent transposition of differing characters is caught. This is provable:
#    swapping positions i and i+1 shifts the weighted sum by exactly value[i]-value[i+1],
#    which is nonzero mod 36 whenever the characters differ. An unweighted sum catches none.
checked = 0
for _ in range(4000):
    original = body()
    index = rng.randrange(BODY_LENGTH - 1)
    if original[index] == original[index + 1]:
        continue
    swapped = list(original)
    swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
    checked += 1
    assert check_character("".join(swapped)) != check_character(original)
assert checked > 3000, checked
print(f"     {checked}/{checked} adjacent transpositions detected (provably 100%)")

# 3. The residual: a random well-shaped token passes about one time in MODULUS.
trials = 60000
passed = sum(
    1
    for _ in range(trials)
    if is_valid(rng.choice(string.ascii_uppercase)
                + "".join(rng.choice(ALPHABET) for _ in range(9)))
)
rate = passed / trials
assert 0.020 < rate < 0.036, rate
print(f"     {passed}/{trials} random shaped tokens pass by chance = {rate:.3%} "
      f"(1/{MODULUS} = {1/MODULUS:.3%})")

# 4. Single substitutions are NOT all caught, and the figure is stated rather than implied
#    to be perfect: a delta d at position p is invisible when d*p is a multiple of 36.
missed = total = 0
for _ in range(40000):
    original = body()
    index = rng.randrange(1, BODY_LENGTH)
    replacement = rng.choice(ALPHABET)
    if replacement == original[index]:
        continue
    total += 1
    altered = original[:index] + replacement + original[index + 1:]
    missed += int(check_character(altered) == check_character(original))
print(f"     single substitutions missed: {missed}/{total} = {missed/total:.2%} "
      "(not zero, and not claimed to be)")
PYCHECK
ok "checksum behaves as documented"

info "Checking that a code embedded in a longer run is still found"
.venv/bin/python - <<'PYCHECK' || fail "the embedded-code recall bug is back"
from multi_extract.core.extract import MAX_ENCLOSING_RUN, candidates_in
from multi_extract.identity import make_code
from multi_extract.models import Document

code = make_code("K7X2M9QR4")

def tokens(text):
    return {item.token for item in candidates_in(Document(source_id="t", text=text))}

# A non-overlapping fixed-width scan consumes greedily from the left, so a code not starting
# on a multiple-of-ten boundary inside a longer run was invisible. A false negative leaves no
# trace in the output, which is what made it worth hunting.
for prefix, suffix in [("", ""), ("XX", ""), ("", "99"), ("item", ""), ("req9f2", "abc")]:
    text = f"{prefix}{code}{suffix}"
    assert code in tokens(text), f"{text} lost the code"
print("     found in all 5 embeddings, including offsets 2, 4 and 6")

# Slashes are non-alphanumeric, so a path needs no window scan at all.
assert tokens(f"/items/{code}/reviews") == {code}
print("     a code in a URL path is a run of exactly ten: no window scan needed")

# A run FAR longer than a code is a different kind of token. Mining it manufactures false
# positives at roughly 1 per 36 windows, so it is not mined.
digest = "dd4e0a91e448d181b9ceb5ebea340561"
assert len(digest) > MAX_ENCLOSING_RUN
assert candidates_in(Document(source_id="t", text=digest)) == []
print(f"     a {len(digest)}-character digest is not mined (cap is {MAX_ENCLOSING_RUN})")
PYCHECK
ok "embedded codes are found, digests are not mined"

info "Measuring the shipped corpus"
.venv/bin/python - <<'PYCHECK' || fail "the corpus did not behave as documented"
from pathlib import Path

from multi_extract.core.collect import Completeness, collect
from multi_extract.core.extract import candidates_in
from multi_extract.core.measure import by_source, measure
from multi_extract.models import SourceKind, Tier
from multi_extract.sources import load_documents, load_truth

documents, problems = load_documents(Path("data/manifest.jsonl"))
truth = load_truth(Path("data/truth.txt"))
assert not problems, problems
print(f"     {len(documents)} documents, {len(truth)} known codes")

every = [item for document in documents for item in candidates_in(document)]
report = measure(every, truth)

strict = report.at(Tier.VALIDATED)
loose = report.at(Tier.SHAPED)
assert strict.precision == 1.0, f"{strict.false_positives} false positive(s)"
assert strict.precision >= loose.precision
print(f"     shaped    P={loose.precision:.1%}  R={loose.recall:.1%}  F1={loose.f1:.3f}")
print(f"     validated P={strict.precision:.1%}  R={strict.recall:.1%}  F1={strict.f1:.3f}")

# The 26 shaped-but-invalid look-alikes are other systems' identifiers. The checksum is the
# only test that rejects them, and none of them is in the truth set.
shaped_only = {item.token for item in every if item.tier is Tier.SHAPED}
assert shaped_only, "the corpus must contain shaped-but-invalid look-alikes"
assert not (shaped_only & truth), "a rejected token was actually real"
print(f"     {len(shaped_only)} shaped-but-invalid token(s) rejected, none of them real")

# Recall below 1.0 here is a COMPLETENESS failure, not an extraction one: the missed codes
# are exactly the ones that were never in the corpus, because a page was dropped.
present = {code for code in truth for document in documents if code in document.text.upper()}
assert set(report.missed_at_strictest) == truth - present
print(f"     {len(report.missed_at_strictest)} missed, and all of them were never in the "
      "corpus at all")

collection = collect(documents)
gaps = [item for item in collection.pages if item.verdict is Completeness.GAP_IN_PAGES]
assert gaps and gaps[0].missing_pages == [3], gaps
print(f"     {gaps[0].source_id}: page {gaps[0].missing_pages} never seen -- which is the "
      "cause of the recall shortfall")

unverifiable = sorted(collection.unverifiable_sources)
assert unverifiable == ["email-d", "export-c"], unverifiable
print(f"     {len(unverifiable)} source(s) declare no total: {unverifiable}")
print("       -> their completeness is UNKNOWN, not confirmed, and the report says so")

# Precision differs sharply by source, which is why an aggregate figure describes neither.
scores = by_source(every, truth, tier=Tier.SHAPED)
assert SourceKind.CSV_EXPORT in scores and SourceKind.EMAIL_BODY in scores
print("     per-source precision at the SHAPED tier:")
for kind, score in scores.items():
    shown = "n/a" if score.precision is None else f"{score.precision:.1%}"
    print(f"       {kind.value:<18} {shown}")

# A code seen in two sources is one finding with two provenances, not two findings.
shared = [item for item in collection.findings if len(item.sources) > 1]
assert shared, "the corpus must exercise cross-source overlap"
print(f"     {len(shared)} code(s) appear in more than one source, kept as single findings")

# Order independence.
import random
reference = [item.token for item in collection.findings]
for seed in range(6):
    shuffled = list(documents)
    random.Random(seed).shuffle(shuffled)
    assert [item.token for item in collect(shuffled).findings] == reference
print("     order-independent across 6 shuffles")
PYCHECK
ok "the corpus behaves as documented"

info "Checking that format detection reads content, not extensions"
.venv/bin/python - <<'PYCHECK' || fail "format detection is broken"
import tempfile
from pathlib import Path

from multi_extract.sources import detect

directory = Path(tempfile.mkdtemp())

cases = [
    ("export.txt", b"PK\x03\x04" + b"\x00" * 64, "zip", True),
    ("book.xlsx", b"PK\x03\x04" + b"\x00" * 64, "zip", False),
    ("report.txt", b"%PDF-1.7\n" + b"x" * 40, "pdf", True),
    ("page.doc", b"<!DOCTYPE html><html>hi</html>", "markup", True),
    ("rows.csv", b"a,b,c\n1,2,3\n4,5,6\n", "delimited", False),
    ("note.txt", b"Hello, team.\nNothing tabular here at all.\n", "text", False),
]
for name, content, expected, mismatched in cases:
    path = directory / name
    path.write_bytes(content)
    result = detect(path)
    assert result.detected == expected, f"{name}: got {result.detected}"
    assert result.mismatched is mismatched, f"{name}: mismatch flag wrong"
    flag = "MISMATCH" if result.mismatched else "consistent"
    print(f"     {name:<12} content={result.detected:<10} {flag}")
print("     an attachment named .txt that is really a zip would have yielded ZERO codes,")
print("     and zero is indistinguishable from a file that genuinely had none")
PYCHECK
ok "format detection works on content"

info "Running the test suite"
.venv/bin/python -m pytest -q || fail "tests failed"
ok "tests pass"

cat <<'DONE'

Setup complete.

  source .venv/bin/activate

  headcount doctor
  headcount extract  -i data/manifest.jsonl
  headcount measure  -i data/manifest.jsonl -k data/truth.txt
  headcount extract  -i data/manifest.jsonl -t shaped     # see the trade
  headcount check    <a-token>
  headcount detect   some-attachment.txt

`extract` exits non-zero only for a DEMONSTRABLE completeness problem -- a gap in page
numbers or a shortfall against a declared total. It does NOT fail on a source that
declares no total, because most sources are like that and the signal would be worthless.
It says so in the output instead: unverifiable is not the same as complete.

`measure` exits non-zero if any known code was never extracted, because a miss is the
failure that nothing in the normal output reveals.
DONE
