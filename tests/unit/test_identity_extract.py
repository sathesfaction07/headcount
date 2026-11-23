"""The code scheme and extraction: checksum properties, tiers, and the embedded-code bug."""

from __future__ import annotations

import random
import string

import pytest

from multi_extract.core.extract import (
    MAX_ENCLOSING_RUN,
    candidates_in,
    grade,
)
from multi_extract.identity import (
    ALPHABET,
    BODY_LENGTH,
    CODE_LENGTH,
    MODULUS,
    SHAPED,
    check_character,
    is_valid,
    make_code,
    normalise,
)
from multi_extract.models import Tier
from tests.conftest import document, shaped_but_invalid, valid_code


class TestCheckCharacter:
    def test_a_completed_body_validates(self):
        for seed in range(20):
            assert is_valid(valid_code(seed))

    def test_the_check_character_is_drawn_from_the_code_alphabet(self):
        """The modulus is 36 so the last position looks like any other.

        If it were mod-11 with an 'X' overflow, the FORMAT check alone could reject tokens
        the CHECKSUM is supposed to catch, which would confuse the tier comparison.
        """
        assert MODULUS == len(ALPHABET) == 36
        seen = {check_character(valid_code(seed)[:BODY_LENGTH]) for seed in range(300)}
        assert seen <= set(ALPHABET)
        assert len(seen) > 20, "the check character should range widely"

    def test_a_body_of_the_wrong_length_raises(self):
        with pytest.raises(ValueError, match="a body is 9 characters"):
            check_character("TOOSHORT")

    def test_a_character_outside_the_alphabet_raises(self):
        with pytest.raises(ValueError, match="not in the code alphabet"):
            check_character("ABCDEFG-H")

    def test_every_adjacent_transposition_of_differing_characters_is_caught(self):
        """Provable, not merely likely.

        Swapping positions i and i+1 shifts the weighted sum by exactly
        value[i] - value[i+1], which is nonzero mod 36 whenever the characters differ. So the
        detection rate for adjacent transpositions is 100%, and an unweighted sum would catch
        none of them.
        """
        rng = random.Random(11)
        checked = 0
        for _ in range(2000):
            body = rng.choice(string.ascii_uppercase) + "".join(
                rng.choice(ALPHABET) for _ in range(8)
            )
            index = rng.randrange(BODY_LENGTH - 1)
            if body[index] == body[index + 1]:
                continue
            swapped = list(body)
            swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
            checked += 1
            assert check_character("".join(swapped)) != check_character(body)
        assert checked > 1500, f"only exercised {checked} transpositions"

    def test_a_single_character_change_is_caught_unless_it_wraps_the_modulus(self):
        """Honest about the limit: a change of exactly 36/weight is invisible.

        Position weights are 1..9, so at position 1 a change of 36 in the character value
        would be undetectable -- but the alphabet only spans 36 values, so no single
        substitution can achieve it. At higher positions the weight multiplies the delta,
        making wraparound possible: position 6 with a delta of 6 gives 36.
        """
        rng = random.Random(12)
        missed = 0
        total = 0
        for _ in range(3000):
            body = rng.choice(string.ascii_uppercase) + "".join(
                rng.choice(ALPHABET) for _ in range(8)
            )
            index = rng.randrange(1, BODY_LENGTH)
            replacement = rng.choice(ALPHABET)
            if replacement == body[index]:
                continue
            altered = body[:index] + replacement + body[index + 1 :]
            total += 1
            if check_character(altered) == check_character(body):
                missed += 1
        # Not asserted as zero, because it is not zero. Asserted as small, and the exact
        # figure is quoted in the README rather than being implied to be perfect.
        assert missed / total < 0.15, f"{missed}/{total} substitutions went undetected"

    def test_a_random_shaped_token_passes_about_one_time_in_thirty_six(self):
        """The residual false-positive rate of the checksum, measured not assumed."""
        rng = random.Random(13)
        passed = sum(
            1
            for _ in range(40000)
            if is_valid(
                rng.choice(string.ascii_uppercase)
                + "".join(rng.choice(ALPHABET) for _ in range(9))
            )
        )
        rate = passed / 40000
        assert 0.020 < rate < 0.036, f"expected about 1/36, measured {rate:.4f}"


class TestShapeAndNormalisation:
    @pytest.mark.parametrize(
        "token",
        ["TOOSHORT1", "TOOLONGTOKEN", "1EADINGDIG", "lowercase1", "HAS-HYPHEN"],
    )
    def test_a_badly_shaped_token_fails_the_shape_test(self, token):
        assert not SHAPED.fullmatch(token)

    def test_normalisation_strips_surrounding_punctuation_and_upper_cases(self):
        code = valid_code(3)
        for wrapped in [f"({code})", f"[{code}]", f'"{code}".', code.lower(), f" {code} "]:
            assert normalise(wrapped) == code

    def test_normalisation_does_not_touch_the_interior(self):
        """Stripping interior hyphens would manufacture candidates from dashed references."""
        assert normalise("ABCD-EFGH-IJ") == "ABCD-EFGH-IJ"
        assert len(normalise("AB CD EF GH")) == len("AB CD EF GH")


class TestGrading:
    def test_a_valid_code_reaches_validated(self):
        assert grade(valid_code(4)) is Tier.VALIDATED

    def test_a_shaped_token_with_a_wrong_check_character_stops_at_shaped(self):
        assert grade(shaped_but_invalid(4)) is Tier.SHAPED

    def test_a_badly_shaped_token_stops_at_looks_like(self):
        assert grade("1234567890") is Tier.LOOKS_LIKE
        assert grade("lower-case") is Tier.LOOKS_LIKE

    def test_tiers_are_ordered_so_filtering_works(self):
        assert Tier.LOOKS_LIKE < Tier.SHAPED < Tier.VALIDATED
        assert Tier.VALIDATED >= Tier.SHAPED


class TestEmbeddedCodesAreFound:
    """The recall bug.

    A non-overlapping fixed-width scan consumes greedily from the left, so a genuine code not
    starting on a multiple-of-ten boundary inside a longer run was never seen at all. A false
    negative leaves no trace in the output, which is what made it worth finding.
    """

    def test_a_standalone_code_is_found_and_not_marked_embedded(self):
        code = valid_code(5)
        found = candidates_in(document(f"item {code} here"))
        assert [item.token for item in found] == [code]
        assert not found[0].embedded

    @pytest.mark.parametrize(
        ("prefix", "suffix"),
        [("XX", ""), ("", "99"), ("item", ""), ("req9f2", "abc"), ("A", "B")],
    )
    def test_a_code_embedded_in_a_longer_run_is_found(self, prefix, suffix):
        code = valid_code(6)
        found = candidates_in(document(f"{prefix}{code}{suffix}"))
        matching = [item for item in found if item.token == code]
        assert matching, f"{prefix}{code}{suffix} lost the code entirely"
        assert matching[0].embedded, "a code found inside a longer run must be marked"

    def test_a_code_in_a_path_needs_no_window_scan(self):
        """Slashes are non-alphanumeric, so the run is exactly the code."""
        code = valid_code(7)
        found = candidates_in(document(f"/items/{code}/reviews"))
        assert [(item.token, item.embedded) for item in found] == [(code, False)]

    def test_a_run_far_longer_than_a_code_is_not_mined(self):
        """The cap. Every spurious hit on the shipped corpus came from a 32-char digest.

        A run much longer than a code is not a code with noise attached, it is a different
        kind of token -- and the checksum admits about one window in thirty-six, so mining a
        32-character run manufactures false positives at no benefit.
        """
        digest = "dd4e0a91e448d181b9ceb5ebea340561"
        assert len(digest) > MAX_ENCLOSING_RUN
        assert candidates_in(document(digest)) == []

    def test_a_code_inside_a_digest_length_run_is_deliberately_sacrificed(self):
        """Stating the cost honestly: the cap does lose a code hidden in a long run."""
        code = valid_code(8)
        buried = "a" * 15 + code + "b" * 15
        assert len(buried) > MAX_ENCLOSING_RUN
        assert code not in {item.token for item in candidates_in(document(buried))}

    def test_a_run_at_exactly_the_cap_is_still_mined(self):
        code = valid_code(9)
        run = code + "0123456789"[: MAX_ENCLOSING_RUN - CODE_LENGTH]
        assert len(run) == MAX_ENCLOSING_RUN
        assert code in {item.token for item in candidates_in(document(run))}


class TestScanning:
    def test_a_run_shorter_than_a_code_yields_nothing(self):
        assert candidates_in(document("SHORT ABC 12345")) == []

    def test_offsets_locate_the_token_in_the_original_text(self):
        code = valid_code(10)
        text = f"prefix text {code} suffix"
        found = candidates_in(document(text))
        assert text[found[0].offset : found[0].offset + CODE_LENGTH] == code

    def test_context_is_captured_for_review(self):
        code = valid_code(11)
        found = candidates_in(document(f"warehouse WH3 holds {code} on shelf 12"))
        assert "warehouse" in found[0].context or "shelf" in found[0].context

    def test_a_lower_case_code_is_recovered(self):
        code = valid_code(12)
        found = candidates_in(document(f"please check {code.lower()} today"))
        assert [item.token for item in found] == [code]
        assert found[0].tier is Tier.VALIDATED

    def test_scanning_is_deterministic(self):
        doc = document(f"{valid_code(13)} and {shaped_but_invalid(13)} and 1234567890")
        first = [(item.token, item.tier, item.offset) for item in candidates_in(doc)]
        second = [(item.token, item.tier, item.offset) for item in candidates_in(doc)]
        assert first == second


class TestMakeCode:
    def test_make_code_produces_a_full_length_code(self):
        assert len(make_code("ABCDEFGHI")) == CODE_LENGTH

    def test_make_code_rejects_a_wrong_length_body(self):
        with pytest.raises(ValueError):
            make_code("SHORT")
