"""Step 2 tests: config, week rule, extraction (spec §§3, 5, 11)."""
from datetime import date
from pathlib import Path
import filecmp
import pandas as pd
import pytest

from admatch.config import Config
from admatch.week import target_week
from admatch.extract import extract_raddar, read_export, save_raw_copy

CFG = Config()
COLS = CFG.settings["sources"]["raddar"]["columns"]
WEEK = target_week(date(2026, 11, 10), CFG.settings["week"])
SAMPLE = Path("/mnt/user-data/uploads/de76fc1f-raddar_EXTRACTED.xlsx")


def row(store="No Frills", flyer="nf/wk46", start="2026-11-12 00:00:00", end="2026-11-18 00:00:00",
        title="LACTANTIA BUTTER 454 g", price="5.99", desc="454 g", page="0.0",
        img="https://x/img.jpeg", item="1.0"):
    return {COLS["store"]: store, COLS["flyer_id"]: flyer, COLS["valid_from"]: start, COLS["valid_to"]: end,
            COLS["title"]: title, COLS["price_text"]: price, COLS["description"]: desc, COLS["page"]: page,
            COLS["image_url"]: img, COLS["item_no"]: item}


def frame(rows):
    return pd.DataFrame(rows, dtype=object)


def many(n=40, **kw):
    return [row(title=f"ITEM {i}", item=str(i), **kw) for i in range(n)]


def test_config_stores():
    assert CFG.home_store == "Superstore"
    assert CFG.competitors == ["No Frills", "Walmart", "Sobeys", "Metro", "FreshCo", "Food Basics", "Costco"]
    assert "Loblaws" not in CFG.all_stores and CFG.canonical_store("Loblaws") is None
    assert CFG.canonical_store("real-canadian-superstore") == "Superstore"


def test_week_run_tuesday_nov_10_takes_nov_12_to_18():
    assert (WEEK.start, WEEK.end) == (date(2026, 11, 12), date(2026, 11, 18))
    assert WEEK.number == 46


def test_week_never_goes_back():
    for d in range(5, 12):   # any run day Thu Nov 5 .. Wed Nov 11 never yields a past week
        w = target_week(date(2026, 11, d), CFG.settings["week"])
        assert w.start >= date(2026, 11, d)


def test_previous_week_flyers_excluded_even_if_still_valid():
    rows = many() + [row(flyer="nf/wk45", start="2026-11-05", end="2026-11-11", title="OLD A"),
                     row(flyer="nf/long", start="2026-11-03", end="2026-12-30", title="OLD B")]
    offers, rep = extract_raddar(frame(rows), CFG, WEEK, only_stores=["No Frills"])
    assert len(offers) == 40 and all(o.valid_from >= date(2026, 11, 12) for o in offers)
    assert len(rep.excluded_other_week) == 2


def test_flyer_starting_inside_week_included_whatever_its_end():
    rows = many() + [row(flyer="nf/euro", start="2026-11-15", end="2026-12-09", title="LATE START")]
    offers, _ = extract_raddar(frame(rows), CFG, WEEK, only_stores=["No Frills"])
    assert any(o.title == "LATE START" for o in offers)


def test_no_start_date_excluded():
    rows = many() + [row(flyer="nf/nodate", start=None, title="NO DATE")]
    offers, rep = extract_raddar(frame(rows), CFG, WEEK, only_stores=["No Frills"])
    assert not any(o.title == "NO DATE" for o in offers) and rep.excluded_no_start


def test_same_flyer_twice_keeps_one_copy():
    rows = many() + many()
    offers, rep = extract_raddar(frame(rows), CFG, WEEK, only_stores=["No Frills"])
    assert len(offers) == 40 and rep.duplicates_removed == 40


def test_missing_data_flagged_not_dropped_no_crash():
    rows = many() + [row(title=None, item="t"), row(img=None, item="i", title="NO IMG"),
                     row(price=None, item="p", title="NO PRICE"), row(desc=None, page=None, item="s", title="NO SIZE")]
    offers, _ = extract_raddar(frame(rows), CFG, WEEK, only_stores=["No Frills"])
    assert len(offers) == 44
    flags = {o.item_no: o.flags for o in offers}
    assert "no title" in flags["t"] and "no image" in flags["i"] and "no price" in flags["p"] and "no page" in flags["s"]


def test_missing_store_loud_warning_names_the_store():
    offers, rep = extract_raddar(frame(many()), CFG, WEEK)
    text = "\n".join(rep.warnings)
    assert "COSTCO" in text and "STORE MISSING" in text and "WALMART" in text
    assert "NO FRILLS" not in text


def test_few_offers_loud_warning():
    _, rep = extract_raddar(frame(many(5)), CFG, WEEK, only_stores=["No Frills"])
    assert any("VERY FEW OFFERS - NO FRILLS" in w for w in rep.warnings)


def test_costco_no_exception(tmp_path):
    rows = many() + many(40, store="Costco", flyer="costco/1", start="2026-11-09", end="2026-11-22")
    offers, rep = extract_raddar(frame(rows), CFG, WEEK, only_stores=["No Frills", "Costco"])
    assert not any(o.store == "Costco" for o in offers)
    assert any("COSTCO" in w for w in rep.warnings)


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample export not present")
def test_real_sample_one_store(tmp_path):
    raw = save_raw_copy(SAMPLE, tmp_path)
    assert filecmp.cmp(SAMPLE, raw, shallow=False)          # raw copy untouched
    offers, rep = extract_raddar(read_export(SAMPLE), CFG, WEEK, only_stores=["No Frills"])
    assert len(offers) == 307 and rep.duplicates_removed == 0
    assert all(o.page >= 1 for o in offers)
