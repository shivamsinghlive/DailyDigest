"""The AI intro may only restate facts from the digest items; anything specific it adds is flagged."""
from digest_tool.digest import invented_details

FACTS = "Person: Ana Lee, mechanical engineer.\n- [New problem] J4 connector EOL, last-time-buy by 11/30, $9k (#supply-chain; why: Ravi owns it)"


def test_grounded_intro_passes(team):
    assert invented_details("Hi Ana. The J4 connector is EOL, with a $9k buy due 11/30.", FACTS, team) == []


def test_month_name_for_a_given_date_is_fine(team):
    assert invented_details("Hi Ana. The J4 buy is due by November 30.", FACTS, team) == []


def test_new_number_name_or_part_is_flagged(team):
    found = invented_details("Hi Ana. The J4 buy costs $12k, Dana says the driver board is late.", FACTS, team)
    assert "12" in found and "Dana" in found and "motor driver" in found
