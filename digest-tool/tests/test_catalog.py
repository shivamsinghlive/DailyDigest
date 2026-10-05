"""catalog.py: nicknames map to one official part, typos still match, unknown parts get flagged."""
import pytest

from digest_tool.catalog import find_mentions, match_parts


@pytest.mark.parametrize("text, official", [
    ("Molex sent an EOL notice for 43045-0412", "J4 connector"),
    ("the wrist conn is loose again", "J4 connector"),
    ("J4 latch popped", "J4 connector"),
    ("bench test on the new TMC9660", "motor driver"),
    ("pushed a fw update", "firmware"),
])
def test_alias_maps_to_official_name(catalog, text, official):
    assert official in match_parts(text, catalog)


def test_reports_what_was_actually_written(catalog):
    assert match_parts("the molex connector is EOL", catalog) == {"J4 connector": "molex connector"}


def test_longest_nickname_wins(catalog):
    # "wrist conn" is the J4 connector; it must not also count as a mention of the wrist assembly.
    assert match_parts("wrist conn sits at the bend", catalog) == {"J4 connector": "wrist conn"}


def test_case_does_not_matter(catalog):
    assert "J4 connector" in match_parts("MOLEX CONNECTOR shortage", catalog)


@pytest.mark.parametrize("text, official, written", [
    ("replacement for the molex conector", "J4 connector", "molex conector"),   # missing letter
    ("J-4 latch keeps popping", "J4 connector", "J-4"),                         # punctuation
    ("swap the TMC-9660 board", "motor driver", "TMC-9660"),
])
def test_typos_and_punctuation_still_match(catalog, text, official, written):
    assert match_parts(text, catalog).get(official) == written


def test_similar_but_different_part_numbers_do_not_match(catalog):
    assert match_parts("J5 header is fine", catalog) == {}


def test_generic_words_do_not_fuzzy_match(catalog):
    # Regression: a bare "connector" once fuzzy-matched a "... connector" nickname.
    assert match_parts("who owns that connector again?", catalog) == {}


def test_part_numbers_not_in_catalog_are_flagged_unknown(catalog):
    found, unknown = find_mentions("ams flagged AS5048A as NRND", catalog)
    assert found == {}
    assert "AS5048A" in unknown


def test_unknown_hardware_phrase_is_flagged(catalog):
    _, unknown = find_mentions("vendor wants PVC for the arm umbilical", catalog)
    assert "arm umbilical" in unknown


def test_known_parts_are_not_flagged_unknown(catalog):
    _, unknown = find_mentions("EOL notice for 43045-0412, the molex connector", catalog)
    assert unknown == []


def test_plain_chatter_flags_nothing(catalog):
    assert find_mentions("pizza in the kitchen at noon", catalog) == ({}, [])


def test_a_single_word_one_letter_longer_is_another_word(catalog):
    # Regression: "failed hardness check" once matched the cable harness.
    assert "gripper" not in match_parts("the fingerss are fine", catalog)   # one letter more: not a typo
    assert "J4 connector" in match_parts("ordered 43045-0413 by mistake", catalog)  # same length: a typo
