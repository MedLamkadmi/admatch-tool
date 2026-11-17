"""Step 4 tests: matching rules (spec §6 and the matching lines of §11), then 5 real list
items, then the whole list."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import pytest

from admatch.config import Config
from admatch.match import Matcher
from admatch.models import Offer
from admatch.pipeline import Run, load_fixed_list, status_of, Row, drop_shared_sizeless

CFG = Config()
M = Matcher(CFG)
RADDAR = Path("/mnt/user-data/uploads/de76fc1f-raddar_EXTRACTED.xlsx")
LIST = Path("/mnt/user-data/uploads/2ac16e04-master_list_.xlsx")
real = pytest.mark.skipif(not (RADDAR.exists() and LIST.exists()), reason="sample files not present")
_n = 0


def offer(title, desc=None, store="No Frills", price="4.99"):
    global _n
    _n += 1
    if desc:
        title = f"{title} {desc}"
    return Offer(uid=f"t{_n}", source="test", store=store, flyer_id="f", title=title, description=desc,
                 price_text=price, image_url=None, page=1, page_raw="0", valid_from=date(2026, 11, 12), valid_to=None)


def hit(item_text, title, desc=None, store="No Frills", per_weight=False):
    """-> (matched?, flags, usable)"""
    o = offer(title, desc, store)
    item = M.read_list_item(0, item_text)
    ms, _ = M.find(item, [(o, M.read_offer(o))], home=(store == "Superstore"), per_weight={o.uid} if per_weight else set())
    return (bool(ms), ms[0].flags if ms else [], ms[0].usable if ms else None)


def yes(*a, **k):
    return hit(*a, **k)[0]


# ---- FIX 1 (16 Nov 2026): same brand required, both sides ---------------------------------
def test_fix1_maker_brand_must_be_the_same():
    assert not yes("Huggies Diapers 60-208 ct", "ROYALE Premium Diapers", "44-96 pk")
    assert yes("Huggies Diapers 60-208 ct", "HUGGIES ECONO PACK DIAPERS", "60-208'S")
    assert not yes("Lactantia Butter 454 g", "GAY LEA BUTTER", "454 g")
    assert not yes("Maple Leaf or Schneiders Bacon 375 g", "BOB'S Bacon", "375 g")
    assert yes("Maple Leaf or Schneiders Bacon 375 g", "SCHNEIDERS Bacon", "375 g")

def test_fix1_store_brand_accepts_any_store_brand():
    assert yes("No Name Butter 454 g", "PC BUTTER", "454 g")
    assert not yes("No Name Butter 454 g", "GAY LEA BUTTER", "454 g")
    assert yes("No Name Butter 454 g", "PC BUTTER", "454 g", store="Superstore")          # Superstore side too

def test_fix1_no_brand_in_list_matches_any_brand():
    assert yes("Strawberries 1 lb", "STRAWBERRIES", "1 lb")
    assert yes("Strawberries 1 lb", "COMPLIMENTS STRAWBERRIES", "1 lb")

def test_fix1_brand_not_stated_goes_to_review():
    o = offer("BUTTER", "454 g")
    ms, near = M.find(M.read_list_item(0, "Lactantia Butter 454 g"), [(o, M.read_offer(o))], home=False)
    assert not ms and near and near[0].reason == "brand not stated in flyer"

def test_never_match_a_different_product():
    assert not yes("Lactantia Butter 454 g", "NO NAME PEANUT BUTTER", "454 g")
    assert not yes("Lactantia Butter 454 g", "BECEL MARGARINE", "454 g")
    assert not yes("Lactantia Butter 454 g", "BUTTER CROISSANTS", "454 g")
    assert not yes("Chicken Breast 1 kg", "CHICKEN THIGHS", "1 kg")
    assert not yes("Kraft Cream Cheese 250 g", "ICE CREAM", "250 g")

def test_slices_vs_block():
    assert yes("Cheese Slices 400 g", "BLACK DIAMOND CHEESE SLICES", "400 g")
    assert not yes("Cheese Slices 400 g", "BLACK DIAMOND CHEESE BLOCK", "400 g")

# ---- normalisation ---------------------------------------------------------------------
def test_text_normalised_before_comparing():
    assert yes("PC Bacon 375 g", "PC® BACON", "375 g")
    assert yes("Schneiders Pepperettes 250–375 g", "SCHNEIDER'S PEPPERETTES™", "225—375 g")
    assert yes("Creme Brulee 200 g", "CRÈME BRÛLÉE", "200 G")

def test_french_names_translated():
    assert yes("Butter 454 g", "BEURRE", "454 g") and yes("Milk 4 l", "LAIT", "4 L")

def test_short_forms():
    assert M.norm.tokens("PC butter") == M.norm.tokens("President's Choice butter")
    assert M.norm.tokens("3 pc set") == ["3", "pc", "set"]
    assert yes("Royale FT 6x126", "ROYALE FACIAL TISSUE", "6x126 sheets")

# ---- size ------------------------------------------------------------------------------
def test_size_rules():
    assert yes("Butter 454 g", "BUTTER", "463 g") and not yes("Butter 454 g", "BUTTER", "400 g")
    assert yes("Butter 400-500 g", "BUTTER", "420 g") and yes("Butter 400-500 g", "BUTTER", "470 g")
    assert not yes("Yogurt 6 x 100 g", "YOGURT", "600 g")
    assert yes("Cashmere Bathroom Tissue 12=24", "CASHMERE BATHROOM TISSUE", "24 rolls")
    assert yes("Kraft Peanut Butter 750 g", "KRAFT PEANUT BUTTER", "750 g / 1 kg")
    assert not yes("Kraft Peanut Butter 1 kg", "KRAFT PEANUT BUTTER", "750 g / 1 kg")
    assert yes("Ground Chicken 300 g", "GROUND CHICKEN", "up to 454 g")

def test_missing_size_matches_on_name_with_flag():
    ok, flags, _ = hit("Butter 454 g", "LACTANTIA BUTTER", "selected varieties")
    assert ok and "size unclear" in flags
    ok, flags, _ = hit("Broccoli Crown 28", "BROCCOLI CROWNS", "product of Canada")
    assert ok and "size unclear" in flags

def test_grade_matches_grade():
    assert yes("Grade A Eggs Large 12 ct", "LARGE WHITE EGGS", "12's")
    assert not yes("Grade A Eggs Large 12 ct", "MEDIUM WHITE EGGS", "12's")
    assert M.read_list_item(0, "Prestige Grade A LRG Eggs, EA").grade == "large"

def test_per_weight_offer_is_not_a_fixed_pack():
    assert not yes("Schneiders Ham 700 g", "DELI SLICED HAM", None, per_weight=True)
    assert yes("Cauliflower 1 ea", "CAULIFLOWER", "product of USA", per_weight=True)

# ---- variants --------------------------------------------------------------------------
def test_variant_never_blocks():
    assert yes("Salted Butter 454 g", "UNSALTED BUTTER", "454 g")
    assert yes("Lactantia Butter 454 g", "LACTANTIA BUTTER", "selected varieties, 454 g")
    assert yes("Butter 454 g", "Gay Lea salted or unsalted butter", "454 g")

def test_colour_is_required_only_when_the_list_names_it():
    assert yes("Potatoes 10 lb", "YELLOW POTATOES", "10 LB BAG")
    assert not yes("Onion Green 1 ea", "YELLOW ONIONS", None)
    assert yes("Onion Green 1 ea", "GREEN ONIONS", "product of Mexico")

# ---- OR offers -------------------------------------------------------------------------
def test_or_in_list_any_branch():
    assert yes("Coke or Pepsi 2 l", "PEPSI", "2 L") and yes("Coke or Pepsi or Sprite 2 l", "SPRITE", "2 L")

def test_or_in_offer_any_branch():
    assert yes("Unico Tuna 198 g", "UNICO BEANS, TOMATOES or TUNA", "540 mL, 796 mL or 198 g selected varieties")

def test_each_branch_keeps_its_own_size():
    br = M.read("wings 505–675 g and strips 550–560 g")
    assert [(b.words, [(s.lo, s.hi) for s in b.sizes]) for b in br] == [(["wing"], [(505, 675)]), (["strip"], [(550, 560)])]
    assert yes("Unico Beans 540 ml", "UNICO BEANS, TOMATOES or TUNA", "540 mL, 796 mL or 198 g")
    assert not yes("Unico Beans 198 g", "UNICO BEANS, TOMATOES or TUNA", "540 mL, 796 mL or 198 g")   # 198 g belongs to tuna
    assert yes("Butter 454 g", "Armstrong cheese block or shreds or slices or Gay Lea salted or unsalted butter 400 g, 320 g, 220 g – 240 g, 454 g")
    assert not yes("Butter 400 g", "Armstrong cheese block or shreds or slices or Gay Lea salted or unsalted butter 400 g, 320 g, 220 g – 240 g, 454 g")

def test_unclear_size_link_is_never_a_winner():
    # comma list of sizes that does not line up with the products: still never a winner
    ok, flags, usable = hit("Bacon 375 g", "MAPLE LEAF Bacon, Ham Steak, Juicy Jumbos or Bologna", "375 g, 450 g")
    assert ok and not usable and "OR offer - size link unclear" in flags

# ---- FIX 4 (16 Nov 2026): every branch's own size is checked ------------------------------
def test_fix4_or_sizes_are_options_for_every_product():
    off = "BURNBRAE FARMS or GRAY RIDGE Large White Eggs or COMPLIMENTS Free Run Eggs"
    ok, flags, usable = hit("Grade A Eggs Large White 12 ct", off, "18 pk or 12 pk", store="Sobeys")
    assert ok and usable
    assert yes("Grade A Eggs Large White 18 ct", off, "18 pk or 12 pk", store="Sobeys")
    assert not yes("Grade A Eggs Large White 30 ct", off, "18 pk or 12 pk", store="Sobeys")
    ok, flags, usable = hit("Bacon 375 g", "MAPLE LEAF Bacon, Ham Steak, Juicy Jumbos or Bologna", "375 g or 450 g")
    assert ok and usable and "OR offer - size link unclear" in flags    # flag stays, match is used

# ---- brands ----------------------------------------------------------------------------
def test_store_brands_match_each_other():
    for title in ("PC BUTTER", "SELECTION BUTTER", "COMPLIMENTS BUTTER", "KIRKLAND SIGNATURE BUTTER", "GREAT VALUE BUTTER"):
        assert yes("No Name Butter 454 g", title, "454 g"), title

def test_store_brand_aliases():
    t = M.norm.tokens
    assert t("Kirkland butter") == t("Kirkland Signature butter") and t("Irrésistible") == t("Irresistibles")
    assert t("Signal") == t("S!gnal") and t("NO NAME®") == t("no name")
    assert M._find_brands(t("Compliments Balance yogurt"))[0] == {"compliment"}

def test_superstore_side_same_brand_rule():
    assert yes("Lactantia Butter 454 g", "LACTANTIA BUTTER", "454 G", store="Superstore")
    assert not yes("Gay Lea Butter 454 g", "LACTANTIA BUTTER", "454 G", store="Superstore")
    assert not yes("no name Butter 454 g", "LACTANTIA BUTTER", "454 G", store="Superstore")
    assert yes("Strawberries 1 lb", "STRAWBERRIES", "1 LB", store="Superstore")

# ---- FIX 3 (16 Nov 2026): describing words never block -----------------------------------
def test_fix3_describing_words_do_not_block():
    assert yes("Kraft Spreads Peanut Butter 750 g - 1 kg", "KRAFT Peanut Butter", "750 g-1 kg")
    assert yes("PC Gourmet Coffee 800 - 930 g", "PC ROAST AND GROUND COFFEE", "800-930 g")
    assert yes("Tide Liquid Laundry Detergent 3.46-3.90 l", "TIDE LAUNDRY DETERGENT OR PODS 3.69 L")
    assert yes("Taylor Farms Chopped Salad Kits 267-383 g", "TAYLOR FARMS SALAD KITS", "267-383 g")

def test_brand_that_stands_for_a_product():
    assert yes("Coke 6x710 ml", "COCA-COLA SOFT DRINKS", "6 x 710 ml")           # Coke also means Coca-Cola
    assert not yes("Coke 6x710 ml", "PEPSI SOFT DRINKS", "6x710 mL")             # Coke = cola, but brand must be the same
    assert not yes("Coke 6x710 ml", "COCA-COLA SOFT DRINKS", "12x355 mL")
    assert not yes("Coke 6x710 ml", "CANADA DRY GINGER ALE", "6x710 mL")
    assert yes("Coke 6x710 ml", "COCA-COLA OR PEPSI SOFT DRINKS", "6x710 mL", store="Superstore")
    assert not yes("Kawartha 1500 ml", "COMPLIMENTS Ice Cream", "1.5 L")
    assert yes("Kawartha 1500 ml", "KAWARTHA DAIRY Ice Cream", "1.5 L")

def test_title_missing_tries_description():
    o = Offer(uid="x", source="t", store="Metro", flyer_id="f", title=None, description="LACTANTIA BUTTER 454 g",
              price_text="5", image_url=None, page=1, page_raw="0", valid_from=date(2026, 11, 12), valid_to=None)
    assert M.find(M.read_list_item(0, "Butter 454 g"), [(o, M.read_offer(o))], home=False)[0]

# ---- status and order ------------------------------------------------------------------
def test_status():
    assert status_of(D("5"), D("4")) == ("HIGH", D("1")) and status_of(D("4"), D("5")) == ("LOW", D("-1"))
    assert status_of(D("4"), D("4"))[0] == "EQUAL" and status_of(None, D("4")) == ("NOT IN SUPERSTORE", None)
    assert status_of(D("4"), None) == ("NOT IN COMPETITOR", None) and status_of(None, None) == ("EMPTY", None)

# ---- FIX 2 (16 Nov 2026): one sizeless offer = one pack size ------------------------------
def test_fix2_sizeless_offer_not_used_for_different_pack_sizes():
    o = offer("COCA-COLA OR PEPSI SOFT DRINKS SELECTED VARIETIES", store="Superstore", price="2")
    pool = [(o, M.read_offer(o))]
    found = []
    for i, t in enumerate(["Coke 6x710 ml", "Coke 12x355 ml", "Coke 24x355 ml", "Pepsi 6x710 ml"]):
        item = M.read_list_item(i, t)
        found.append((item, M.find(item, pool, home=True)[0]))
    assert all(ms for _, ms in found)                     # each one alone would match on the name
    removed = drop_shared_sizeless(found, M.tol)
    assert all(not ms for _, ms in found) and len(removed) == 4

def test_fix2_sizeless_offer_kept_for_one_pack_size():
    o = offer("COCA-COLA OR PEPSI SOFT DRINKS SELECTED VARIETIES", store="Superstore", price="2")
    pool = [(o, M.read_offer(o))]
    found = []
    for i, t in enumerate(["Coke 6x710 ml", "Pepsi 6x710 ml"]):
        item = M.read_list_item(i, t)
        found.append((item, M.find(item, pool, home=True)[0]))
    drop_shared_sizeless(found, M.tol)
    assert all(ms and "size unclear" in ms[0].flags for _, ms in found)

def test_fixed_list_columns_are_not_assumed(tmp_path):
    import pandas as pd
    p = tmp_path / "list.xlsx"
    pd.DataFrame({"Code": ["1"], "Product": ["Butter 454 g"]}).to_excel(p, index=False)
    with pytest.raises(ValueError, match="will not guess"):
        load_fixed_list(p, CFG)


# ---- 5 real list items, then all -------------------------------------------------------
@pytest.fixture(scope="module")
def full(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    return Run(CFG, out, use_images=False).run(LIST, RADDAR, date(2026, 11, 10))


def by(result, name):
    return next(r for r in result.rows if r.item == name)


@real
def test_five_real_items(tmp_path):
    names = load_fixed_list(LIST, CFG)
    five = [names.index(n) for n in ("Lactantia Butter 454 g", "Coke 6x710 ml", "Maple Leaf or Schneiders Bacon 375 g",
                                     "Schneiders Family Pack Pepperettes 250-375 g", "Tostitos Tortilla Chips 210-300 g")]
    res = Run(CFG, tmp_path, use_images=False).run(LIST, RADDAR, date(2026, 11, 10), only_items=five)
    assert len(res.rows) == 5
    r = by(res, "Lactantia Butter 454 g")          # Gay Lea is another maker brand: not a match any more
    assert r.home.value == D("4.97") and (r.comp.offer is None or "Gay Lea" not in r.comp.offer.title)
    r = by(res, "Coke 6x710 ml")                    # Coca-Cola = Coke; same pack 6 x 710
    assert (r.comp.value, r.comp.offer.store) == (D("3.98"), "Walmart") and "6 x 710" in r.comp.offer.title
    r = by(res, "Maple Leaf or Schneiders Bacon 375 g")   # same brand both sides: BOB'S bacon is not used
    assert r.comp.offer is None or "BOB'S" not in r.comp.offer.title
    r = by(res, "Schneiders Family Pack Pepperettes 250-375 g")
    assert (r.status, r.diff, r.comp.offer.store) == ("HIGH", D("6.00"), "No Frills")
    r = by(res, "Tostitos Tortilla Chips 210-300 g")             # QUE PASA no longer counts: Tostitos only
    assert r.home.value == D("3.99") and "TOSTITOS" in r.comp.offer.title.upper()


@real
def test_all_list_items_get_a_row(full):
    assert len(full.rows) == 80                                   # every list item, duplicates included
    assert sum(r.item == "Kawartha 1500 ml" for r in full.rows) == 2
    assert [r.item for r in full.rows] == load_fixed_list(LIST, CFG)      # FIX 5: master-list order, row for row

@real
def test_fix2_real_sizeless_coke_pepsi_offer_used_at_most_once(full):
    used = [r.item for r in full.rows if r.home.offer is not None
            and r.home.offer.title.upper().startswith("COCA-COLA OR PEPSI SOFT DRINKS SELECTED VARIETIES")]
    assert len(used) <= 1, used

@real
def test_same_product_twice_takes_cheapest(full):
    r = by(full, "Ruffles Potato Chips 180-220 g")               # several Ruffles offers; cheapest wins
    ruffles = [o for o in full.offers if o.store != "Superstore" and "RUFFLES" in (o.title or "").upper()]
    assert len(ruffles) > 1 and "RUFFLES" in r.comp.offer.title.upper()
    assert any("not the cheapest" in x[4] for x in r.review)

@real
def test_confirmed_size_beats_name_only_match(full):
    r = by(full, "Lemon 2 lb")                                    # Sobeys lemons $0.89 has no size -> not preferred
    assert r.comp.value == D("4.99") and "size unclear" not in r.comp.flags

@real
def test_missing_store_warning_in_full_run(full):
    assert any("COSTCO" in w and "STORE MISSING" in w for w in full.warnings)


# ---- FIX 6 (16 Nov 2026): variant words never count as extra product words ---------------
def test_fix6_variant_words_are_not_extra_words():
    assert yes("Chapmans Premium Ice Cream 2 l",
               "CHAPMAN'S Premium or No Sugar Added Ice Cream, Frozen Yogurt or Sorbet", "1-2 L", store="Sobeys")
    ok, flags, _ = hit("Country Harvest Bread 570-650 g", "Country Harvest Farmhouse white or whole wheat bread", store="Walmart")
    assert ok and "size unclear" in flags

def test_fix6_real_product_words_still_block():
    assert not yes("Chapmans Premium Ice Cream 2 l", "CHAPMAN'S Frozen Yogurt", "2 L")
    assert not yes("Cheddar Cheese 400 g", "MOZZARELLA CHEESE", "400 g")

# ---- FIX 7 (16 Nov 2026): Doritos means tortilla chips ------------------------------------
def test_fix7_doritos_means_tortilla_chips():
    assert yes("Doritos Tortilla Chips 150-280 g", "Doritos", "Each. 235 g.", store="Walmart")
    assert not yes("Doritos Tortilla Chips 150-280 g", "Tostitos", "235 g", store="Walmart")

# ---- FIX 8 (16 Nov 2026): sizeless rule is applied per branch ----------------------------
def test_fix8_sizeless_rule_per_branch():
    o = offer("FARMER'S MARKET SWEET PEPPERS or FARMER'S MARKET LEMONS or FARMER'S MARKET YELLOW ONIONS",
              "product of Canada or Mexico", price="2.99")
    pool = [(o, M.read_offer(o))]
    found = []
    for i, t in enumerate(["Farmers Market Peppers 4 ct", "10lb Onions"]):
        item = M.read_list_item(i, t)
        found.append((item, M.find(item, pool, home=False)[0]))
    drop_shared_sizeless(found, M.tol)
    assert all(ms for _, ms in found)            # one list item per branch: both kept

@real
def test_fix8_real_rows(full):
    for name in ("Farmers Market Peppers 4 ct", "10lb Onions"):
        r = by(full, name)
        assert (r.comp.offer.store, r.comp.value) == ("No Frills", D("2.99")) and "FARMER'S MARKET" in r.comp.offer.title
    r = by(full, "Doritos Tortilla Chips 150-280 g")
    assert (r.comp.offer.store, r.comp.value) == ("Walmart", D("3.97"))
