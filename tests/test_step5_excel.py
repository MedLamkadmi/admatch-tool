"""Step 5 tests: Excel output (spec §9 and the output lines of §11).

Real flyer images cannot be downloaded in the build workspace, so these tests build a small
flyer export whose image 'URLs' are local test images (some carry a printed price)."""
import zipfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import pandas as pd
import pytest
from openpyxl import load_workbook

from admatch.config import Config
from admatch.excel import write_excel, COLUMNS
from admatch.pipeline import Run
from admatch.ocr import ocr_available
import make_fixtures as mf

CFG = Config()
COLS = CFG.settings["sources"]["raddar"]["columns"]
EXPECTED_HEADERS = ["List item", "Status", "Price difference", "Competitor store", "Competitor price",
                    "Competitor product (as written in flyer)", "Competitor image", "Competitor flyer page",
                    "Superstore price", "Superstore product (as written in flyer)", "Superstore image", "Superstore flyer page"]


def row(store, title, desc, price, page, img):
    return {COLS["store"]: store, COLS["flyer_id"]: f"{store}/wk46", COLS["valid_from"]: "2026-11-12", COLS["valid_to"]: "2026-11-18",
            COLS["title"]: f"{title} {desc}" if desc else title, COLS["price_text"]: price, COLS["description"]: desc,
            COLS["page"]: str(page), COLS["image_url"]: str(img) if img else None, COLS["item_no"]: None}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("xl")
    P = lambda n, label: mf.product_image(n, label)
    rows = [
        # 1 Butter: Superstore 5.99; Metro text $5 but image shows MEMBER PRICE 4.00 -> compares 4.00 -> HIGH 1.99
        row("Superstore", "LACTANTIA BUTTER", "454 g", "5.99", 0, P("x_ss_butter.png", "LACTANTIA BUTTER")),
        row("Metro", "LACTANTIA BUTTER", "454 g", "$5.00", 2, mf.price_image("x_member.png", "4", "00", "MEMBER PRICE")),
        row("Walmart", "GAY LEA BUTTER", "454 g", "4.97", 1, P("x_wm_butter.png", "GAY LEA BUTTER")),
        # 2 Bacon: 2 for $7 at Sobeys (3.50), Superstore 6.00 -> HIGH 2.50 (biggest gap)
        row("Superstore", "MAPLE LEAF BACON", "375 g", "6", 3, P("x_ss_bacon.png", "MAPLE LEAF BACON")),
        row("Sobeys", "MAPLE LEAF Bacon", "375 g", "$7.0 2 for", 4, None),                       # no image
        # 3 Flour: only competitor -> NOT IN SUPERSTORE; text $7, image 3.50 -> conflict flag
        row("No Frills", "ROBIN HOOD FLOUR", "10 kg", "$7", 5, mf.price_image("x_conflict.png", "3", "50")),
        # 4 Carrots: only Superstore -> NOT IN COMPETITOR
        row("Superstore", "CARROTS", "3 LB BAG", "2.50", 6, P("x_ss_carrots.png", "CARROTS")),
        # 5 Chips: equal
        row("Superstore", "TOSTITOS TORTILLA CHIPS", "210-295 G", "3.99", 7, P("x_ss_chips.png", "TOSTITOS TORTILLA CHIPS")),
        row("FreshCo", "Tostitos Tortilla Chips", "205-300 g", "3.99", 8, P("x_fc_chips.png", "TOSTITOS TORTILLA CHIPS")),
        # 6 Pepperettes: Superstore cheaper -> LOW ; 7 Strawberries: nowhere -> EMPTY
        row("Superstore", "SCHNEIDERS PEPPERETTES", "250-375 G", "4.00", 9, P("x_ss_pep.png", "SCHNEIDERS PEPPERETTES")),
        row("Food Basics", "SCHNEIDERS PEPPERETTES", "250-375 g", "5.00", 10, P("x_fb_pep.png", "SCHNEIDERS PEPPERETTES")),
    ]
    for s in ("Superstore", "No Frills", "Walmart", "Sobeys", "Metro", "FreshCo", "Food Basics"):   # filler so stores are not "very few offers"
        rows += [row(s, f"FILLER ITEM {i}", None, "1.00", 20, None) for i in range(30)]
    raddar = tmp / "raddar.xlsx"
    pd.DataFrame(rows).to_excel(raddar, index=False)
    lst = tmp / "list.xlsx"
    items = ["Strawberries 1 lb", "Schneiders Pepperettes 250-375 g", "Tostitos Tortilla Chips 210-300 g", "3lb Carrots",
             "Robin Hood Flour 10kg", "Lactantia Butter 454 g", "Maple Leaf Bacon 375 g"]
    pd.DataFrame({"Item ": items}).to_excel(lst, index=False)
    result = Run(CFG, tmp / "out").run(lst, raddar, date(2026, 11, 10))
    path = write_excel(result, tmp / "out" / "ad_match.xlsx")
    return result, path, items


def table(path):
    ws = load_workbook(path)["Ad Match"]
    rows = list(ws.iter_rows(values_only=True))
    h = next(i for i, r in enumerate(rows) if r[0] == "List item")
    return ws, rows[h], rows[h + 1:], h + 1


def test_twelve_columns_exact_order(built):
    ws, header, body, _ = table(built[1])
    assert list(header) == EXPECTED_HEADERS and ws.max_column == 12 and len(COLUMNS) == 12

def test_all_list_items_present_including_empty(built):
    _, _, body, _ = table(built[1])
    assert sorted(r[0] for r in body) == sorted(built[2])
    assert [r for r in body if r[0] == "Strawberries 1 lb"][0][1] == "EMPTY"

def test_row_order(built):                       # master-list order, no sorting (16 Nov 2026)
    _, _, body, _ = table(built[1])
    assert [r[0] for r in body] == built[2]
    assert [r[1] for r in body] == ["EMPTY", "LOW", "EQUAL", "NOT IN COMPETITOR", "NOT IN SUPERSTORE", "HIGH", "HIGH"]

def test_difference_is_superstore_minus_competitor_and_empty_when_a_price_is_missing(built):
    _, _, body, _ = table(built[1])
    for r in body:
        if r[4] is not None and r[8] is not None:
            assert r[2] == pytest.approx(r[8] - r[4])
        else:
            assert r[2] is None

def test_multi_buy_shows_unit_price_and_buy_requirement(built):
    ws, _, body, h = table(built[1])
    i = [r[0] for r in body].index("Maple Leaf Bacon 375 g")
    cell = ws.cell(h + 1 + i, 5)
    assert cell.value == pytest.approx(3.50) and "2 for $7.00" in cell.number_format
    assert body[i][3] == "Sobeys" and body[i][5] == "MAPLE LEAF Bacon 375 g" and body[i][7] == 5   # exact flyer text, page

def test_no_image_text(built):
    _, _, body, _ = table(built[1])
    i = [r[0] for r in body].index("Maple Leaf Bacon 375 g")
    assert body[i][6] == "no image" and body[i][10] is None      # competitor has none; Superstore picture is embedded

def test_pictures_embedded_not_linked(built):
    result, path, _ = built
    ws = load_workbook(path)["Ad Match"]
    expected = sum(1 for r in result.rows for s in (r.comp, r.home) if s.image is not None)
    assert expected == 9 and len(ws._images) == expected
    with zipfile.ZipFile(path) as z:
        media = [n for n in z.namelist() if n.startswith("xl/media/")]
        assert len(media) == expected                              # the picture bytes are inside the file
        rels = "".join(z.read(n).decode("utf8", "ignore") for n in z.namelist() if n.endswith(".rels"))
        assert 'TargetMode="External"' not in rels and "http" not in rels.replace("http://schemas", "")   # opens offline

def test_pictures_sit_in_their_own_row_and_column(built):
    ws, _, body, h = table(built[1])
    cols = {(i.anchor._from.col + 1) for i in ws._images}
    assert cols == {7, 11}
    rows_with_pic = {i.anchor._from.row + 1 for i in ws._images}
    assert rows_with_pic <= set(range(h + 1, h + 1 + len(body)))

@pytest.mark.skipif(not ocr_available(), reason="Tesseract not installed")
def test_image_member_price_and_conflict_reach_the_file(built):
    result, path, _ = built
    butter = next(r for r in result.rows if r.item.startswith("Lactantia"))
    assert (butter.comp.offer.store, butter.comp.value, butter.comp.price.selling) == ("Metro", D("4.00"), D("5.00"))
    assert butter.status == "HIGH" and butter.diff == D("1.99")
    flour = next(r for r in result.rows if r.item.startswith("Robin Hood"))
    assert flour.comp.value == D("3.50") and any("price conflict" in f for f in flour.comp.flags)
    flags = [r[4] for r in load_workbook(path)["Flags"].iter_rows(min_row=2, values_only=True)]
    assert any("price conflict: text $7.00, image $3.50" in f for f in flags)

def test_missing_store_warning_is_written_into_the_file(built):
    ws = load_workbook(built[1])["Ad Match"]
    assert "COSTCO" in str(ws.cell(1, 1).value) and "STORE MISSING" in str(ws.cell(1, 1).value)
