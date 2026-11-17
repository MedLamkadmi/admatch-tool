"""Step 3 tests: every example in spec §7 and the price lines of §11, plus the price
wordings found in the real Raddar sample."""
from decimal import Decimal as D
from pathlib import Path
import pytest

from admatch.prices import parse_price, apply_image, cheapest, Price
from admatch.ocr import read_image, ImageRead, ImagePrice, ocr_available
import make_fixtures as mf


def img(*vals, kind="price"):
    return ImageRead(ok=True, prices=[ImagePrice(v, kind=kind) for v in vals])


# ---- price field cleaning -------------------------------------------------------------
def test_cleaning_now_ea():
    assert parse_price("NOW $24.00 EA.").comparison == D("24.00")

@pytest.mark.parametrize("text,val", [("5.97", "5.97"), ("3", "3.00"), ("$28.00 EA.", "28.00"), ("ONLY $6.47", "6.47"),
    ("$3.97 Each.", "3.97"), ("SALE $4.50", "4.50"), ("OR $5.99", "5.99"), ("ALWAYS $5.00", "5.00"),
    ("HOT PRICE $3.99", "3.99"), ("CHOICE $1.29 ea.", "1.29"), ("$2.49 ea.", "2.49")])
def test_cleaning_real_wordings(text, val):
    p = parse_price(text)
    assert p.comparison == D(val) and p.basis == "each" and not p.flags

# ---- rule 1: member price -------------------------------------------------------------
def test_member_price_5_selling_4_member():
    p = apply_image(parse_price("$5.00"), img(4.00, kind="member"))
    assert p.comparison == D("4.00") and p.member == D("4.00") and p.selling == D("5.00")

def test_member_price_in_description():
    p = parse_price("$5.00", "selected varieties, member price $4.00")
    assert p.comparison == D("4.00") and p.selling == D("5.00")

def test_member_price_real_metro_wording():
    p = parse_price("members-only price $6.49 $SPECIAL PRICE 7.49 ea. for non-members")
    assert (p.comparison, p.member, p.selling) == (D("6.49"), D("6.49"), D("7.49"))

def test_no_member_price_in_image_uses_selling():
    p = apply_image(parse_price("$5.00"), img(5.00))
    assert p.comparison == D("5.00") and p.member is None and not p.flags

# ---- rule 2: multi-buy ----------------------------------------------------------------
@pytest.mark.parametrize("text,total,qty,unit", [("2 for $7", "7.00", 2, "3.50"), ("2/$5", "5.00", 2, "2.50"),
    ("2/7", "7.00", 2, "3.50"), ("$10.0 2 for", "10.00", 2, "5.00"), ("$7.0 2", "7.00", 2, "3.50"),
    ("$6.0 $OR 3.79 EA. 2 for", "6.00", 2, "3.00"), ("$15.0 $LESS THAN 2 PAY 9.49 EA. 2 for", "15.00", 2, "7.50"),
    ("$9.99 $or pay 4.99 ea. 3 for", "9.99", 3, "3.33"), ("$1.0 $or 9.99 case 2 for", "1.00", 2, "0.50")])
def test_multi_buy(text, total, qty, unit):
    p = parse_price(text)
    assert (p.multi_total, p.multi_qty, p.unit_price, p.comparison) == (D(total), qty, D(unit), D(unit))
    assert f"{qty} for ${total}" in p.buy_requirement          # buy requirement shown

def test_multi_buy_keeps_single_price():
    p = parse_price("$6.0 $OR 3.79 EA. 2 for")
    assert p.single_price == D("3.79") and "or $3.79 ea" in p.buy_requirement

def test_bogo_5_is_2_50():
    p = parse_price("BOGO $5")
    assert (p.comparison, p.multi_total, p.multi_qty) == (D("2.50"), D("5.00"), 2)
    assert parse_price("$5.00 buy 1 get 1 free").comparison == D("2.50")

# ---- rule 3: SAVE-only ----------------------------------------------------------------
@pytest.mark.parametrize("text", ["SAVE $2", "SAVE $1.50", "SAVE $20%", "SAVE $28"])
def test_save_only_is_never_a_price(text):
    p = parse_price(text)
    assert p.comparison is None and p.save_only and p.member is None and p.selling is None

def test_save_with_unclear_prices_is_missing_not_guessed():
    p = parse_price("SAVE $30% $3.63/100 g 2.72/100 g")
    assert p.comparison is None and "SAVE offer - prices unclear" in p.flags

# ---- rule 4: was/now ------------------------------------------------------------------
def test_was_now():
    assert parse_price("was $6.99, now $4.99").comparison == D("4.99")

# ---- rule 5: range --------------------------------------------------------------------
def test_range_keeps_both_ends_uses_cheapest():
    p = parse_price("FROM $7.99-39.99")
    assert (p.range_low, p.range_high, p.comparison) == (D("7.99"), D("39.99"), D("7.99"))
    assert "from" in p.from_wording and "from" in p.note()
    assert parse_price("$5.00-26.25").multi_qty is None and parse_price("$5.00-26.25").comparison == D("5.00")
    p = parse_price("$26.00-39.00")
    assert (p.range_low, p.range_high, p.comparison) == (D("26.00"), D("39.00"), D("26.00"))

@pytest.mark.parametrize("text,val", [("FROM $6.00", "6.00"), ("Starting From $24.00", "24.00"),
                                       ("STARTING AT $39.99 ea.", "39.99"), ("From $9.97", "9.97")])
def test_from_wording_visible(text, val):
    p = parse_price(text)
    assert p.comparison == D(val) and p.from_wording and p.from_wording in p.note()

# ---- rule 6: weight -------------------------------------------------------------------
def test_pack_price_plus_unit_price_uses_pack_price():
    p = parse_price("$10.99 $2.43/100 g")
    assert p.comparison == D("10.99") and p.basis == "each"
    p = parse_price("$1.88 $4.14/kg")
    assert p.comparison == D("1.88") and p.basis == "each"

@pytest.mark.parametrize("text,val,basis", [("$4.88 /LB", "4.88", "per_lb"), ("$12.50 /lb", "12.50", "per_lb"),
    ("$1.92/lb", "1.92", "per_lb"), ("ONLY $2.00 /lb", "2.00", "per_lb"), ("$4.49 /100 g", "4.49", "per_100g"),
    ("$2.99 $6.59/kg /lb", "2.99", "per_lb"), ("$1.99 $4.39/KG /lb", "1.99", "per_lb"),
    ("$2.99 $66¢/100 g /lb", "2.99", "per_lb"), ("$3.49 $15.83/lb /100 g", "3.49", "per_100g"),
    ("$0.99 EA/LB", "0.99", "per_lb_or_each"), ("$2.49 /lb/ea", "2.49", "per_lb_or_each"),
    ("$5.88 $12.96/ kg /lb or ea.", "5.88", "per_lb_or_each"), ("ONLY $3.99 /lb or ea", "3.99", "per_lb_or_each")])
def test_per_weight_only(text, val, basis):
    p = parse_price(text)
    assert p.comparison == D(val) and p.basis == basis and not p.flags      # no flag

def test_per_lb_only_image_checked_first():
    p = apply_image(parse_price("$4.88 /LB"), img(9.76))        # image shows a price -> use it
    assert p.comparison == D("9.76") and not p.flags
    p = apply_image(parse_price("$4.88 /LB"), ImageRead(ok=True))   # image shows none -> $4.88/LB
    assert p.comparison == D("4.88") and p.basis == "per_lb" and not p.flags

# ---- rule 7: text vs image conflict ---------------------------------------------------
def test_text_4_99_image_3_99_takes_image_and_flags():
    p = apply_image(parse_price("4.99"), img(3.99))
    assert (p.comparison, p.text_price, p.image_price) == (D("3.99"), D("4.99"), D("3.99"))
    assert any("price conflict" in f for f in p.flags)

def test_price_field_7_image_3_50():
    p = apply_image(parse_price("$7"), img(3.50))
    assert p.comparison == D("3.50") and any("price conflict" in f for f in p.flags)

def test_image_agreeing_with_multi_buy_total_is_not_a_conflict():
    p = apply_image(parse_price("2 for $7"), img(7.00))
    assert p.comparison == D("3.50") and not p.flags

def test_several_unexplained_image_prices_never_a_winner():
    p = apply_image(parse_price("4.99"), img(2.49, 6.99))
    assert not p.usable and "shared image - price unclear" in p.flags
    assert cheapest([p]) is None

def test_unreadable_image_does_not_change_price():
    p = apply_image(parse_price("4.99"), ImageRead(ok=False, error="x"))
    assert p.comparison == D("4.99") and not p.flags

# ---- rule 8: big dollars + small cents (local OCR on generated flyer-style images) -----
needs_ocr = pytest.mark.skipif(not ocr_available(), reason="Tesseract not installed")

@needs_ocr
@pytest.mark.parametrize("big,small,val,kw", [("7", "50", 7.50, {}), ("3", "99", 3.99, {}), ("12", "97", 12.97, {}),
    ("24", "00", 24.00, {}), ("3", "50", 3.50, dict(fg="yellow", bg="navy"))])
def test_big_dollars_small_cents_combined(big, small, val, kw):
    r = read_image(mf.price_image(f"r8_{big}_{small}_{len(kw)}.png", big, small, "BUTTER 454 g", **kw))
    assert r.ok and r.values() == [val]              # never only the big part, never the size

@needs_ocr
def test_image_member_price_end_to_end():
    r = read_image(mf.price_image("r8_member.png", "4", "00", "MEMBER PRICE"))
    p = apply_image(parse_price("$5.00"), r)
    assert p.comparison == D("4.00") and p.selling == D("5.00")

@needs_ocr
def test_image_conflict_end_to_end():
    p = apply_image(parse_price("$7"), read_image(mf.price_image("r8_conflict.png", "3", "50")))
    assert p.comparison == D("3.50") and any("price conflict" in f for f in p.flags)

@needs_ocr
def test_image_save_amount_is_not_a_price():
    r = read_image(mf.text_image("r8_save.png", ["PC TORTILLA BOWLS 300 g", "SAVE $1.50"]))
    assert r.ok and r.values() == []

@needs_ocr
def test_image_multi_buy_text():
    r = read_image(mf.text_image("r8_multi.png", ["2 for $7.00"]))
    p = apply_image(parse_price(None), r)
    assert p.comparison == D("3.50") and p.multi_qty == 2

def test_missing_image_file_never_crashes():
    r = read_image("does/not/exist.png")
    assert not r.ok and r.error

# ---- rule 9 / same product twice ------------------------------------------------------
def test_cheapest_wins():
    ps = [parse_price("5.99"), parse_price("2 for $7"), parse_price("SAVE $2"), parse_price(None)]
    assert cheapest(ps).comparison == D("3.50")

def test_missing_price_flagged():
    p = parse_price(None)
    assert p.comparison is None and "no price" in p.flags

def test_every_price_in_real_sample_parses_without_crash():
    sample = Path("/mnt/user-data/uploads/de76fc1f-raddar_EXTRACTED.xlsx")
    if not sample.exists():
        pytest.skip("sample not present")
    import pandas as pd
    df = pd.read_excel(sample, dtype=str)
    n_price = 0
    for t, d in zip(df["Advertised Price"], df["Fine Print / Size / Conditions"]):
        p = parse_price(None if pd.isna(t) else t, None if pd.isna(d) else d)
        assert p.comparison is not None or p.flags          # a price, or a flag saying why not
        n_price += p.comparison is not None
    assert n_price > 2300
