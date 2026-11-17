"""Step 4 tests, part 1: sizes (spec §6 and the size lines of §11)."""
import pytest
from admatch.sizes import sizes_in, same_size, any_same_size, find_sizes


def one(text):
    s = sizes_in(text)
    assert len(s) == 1, (text, s)
    return s[0]


def match(a, b):
    return any_same_size(sizes_in(a), sizes_in(b))


def test_454_vs_463_match_within_2_percent():
    assert match("butter 454 g", "butter 463 g")

def test_454_vs_400_no_match():
    assert not match("butter 454 g", "butter 400 g") and not match("400 g", "454 g")

def test_range_400_500_matches_420_and_470():
    assert match("butter 400-500 g", "420 g") and match("butter 400–500 g", "470 g")
    assert not match("butter 400-500 g", "520 g")

def test_pack_6x100_vs_600_no_match():
    assert not match("6 x 100 g", "600 g") and not match("600 g", "6x100 g")
    assert match("6x100 g", "6 x 100 g")

def test_n_equals_m_is_total_count():
    s = one("Cashmere Bathroom Tissue 12=24")
    assert (s.dim, s.lo, s.nums) == ("count", 24, (12, 24))          # both numbers kept, never a range
    assert match("12=24", "24 rolls") and match("12=24", "12=24") and match("8=24", "24's")
    assert not match("12=24", "12 rolls") and not match("30=60", "12=24")

def test_nxm_keeps_all_numbers():
    s = one("Royale FT 6x126")
    assert s.dim == "nxm" and s.nums == (6, 126.0)
    assert match("6x126", "6x126 sheets") and not match("6x126", "6x100/72 sheets")

def test_bare_number_is_not_a_size():
    assert sizes_in("Broccoli Crown 28") == []

def test_slash_takes_first_size():
    s = one("KRAFT PEANUT BUTTER 750 g / 1 kg")
    assert (s.lo, s.hi) == (750, 750)
    assert one("OIKOS 650/750 g").lo == 650
    assert not match("1 kg", "750 g / 1 kg") and match("750 g", "750 g / 1 kg")

def test_up_to():
    s = one("GROUND CHICKEN up to 454 g")
    assert s.upto and s.hi == 454
    assert match("300 g", "up to 454 g") and match("454 g", "up to 454 g") and not match("500 g", "up to 454 g")

def test_weight_units_convert():
    assert match("Strawberries 1 lb", "454 g") and match("Raspberries 6 oz", "170 g")
    assert match("10 lb", "4.54 kg") and match("Kawartha 1500 ml", "1.5 L") and match("4 l", "4 L")
    assert not match("1 lb", "2 lb")

def test_range_with_two_units():
    s = one("Kraft Peanut Butter 750 g - 1 kg")
    assert (s.lo, s.hi) == (750, 1000)
    s = one("MCCAIN 454 g-1.9 kg")
    assert (s.lo, s.hi) == (454, 1900)

def test_ranges_overlap_rule():
    assert match("Lay's 177-255 g", "200-235 g") and match("Tide 3.46-3.90 l", "1.09-3.46 L")
    assert not match("177-255 g", "300-340 g")

def test_pack_with_volume():
    s = one("Coke 6x710 ml")
    assert (s.pack, s.lo) == (6, 710)
    assert match("6x710 ml", "6 x 710 mL") and not match("6x710 ml", "12x355 mL") and not match("6x710 ml", "710 ml")
    assert match("Pepsi 24 pk", "24x355 mL") and not match("Pepsi 24 pk", "12x355 mL") and not match("24 pk", "2 L")

def test_counts():
    assert match("12 ct", "12's") and match("5 pk", "PKG OF 5") and match("3pk", "3 pk") and match("12 ct", "dozen")
    assert match("Huggies 60-208 ct", "88-120's") and not match("12 ct", "18's")

def test_glued_units():
    assert one("10lb Onions").lo == pytest.approx(4535.9, abs=0.1)
    assert one("Flour 10kg").lo == 10000 and one("2pk").lo == 2

def test_slash_inside_pack():
    s = one("Danone Oikos Multi 4x95/100g")
    assert (s.pack, s.lo) == (4, 95)

def test_sizes_keep_reading_order():
    s = sizes_in("wings 505–675 g and strips 550–560 g")
    assert [(x.lo, x.hi) for x in s] == [(505, 675), (550, 560)]

def test_price_per_kg_is_not_a_size():
    assert sizes_in("2.18/kg, product of Canada") == []
