"""Extraction (spec §5): read the flyer export, keep the raw copy, apply the week rule,
remove duplicates, flag what is missing, warn loudly about missing stores. Never crashes
on missing data and never silently drops an offer."""
from __future__ import annotations
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .config import Config
from .models import Offer
from .week import FlyerWeek


@dataclass
class ExtractionReport:
    week: FlyerWeek
    rows_read: int = 0
    kept: int = 0
    excluded_other_week: dict = field(default_factory=dict)   # flyer -> (start, count)
    excluded_no_start: dict = field(default_factory=dict)     # flyer -> count
    unknown_stores: dict = field(default_factory=dict)        # raw store name -> count
    duplicates_removed: int = 0
    per_store: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"Target {self.week}", f"Rows read: {self.rows_read}",
               f"Offers kept: {self.kept}", f"Duplicates removed: {self.duplicates_removed}"]
        for fl, (start, n) in sorted(self.excluded_other_week.items()):
            out.append(f"Excluded (starts {start}, outside target week): {fl} - {n} offers")
        for fl, n in sorted(self.excluded_no_start.items()):
            out.append(f"Excluded (no start date): {fl} - {n} offers")
        for st, n in sorted(self.unknown_stores.items()):
            out.append(f"Ignored (store not in config): {st} - {n} rows")
        for st, n in self.per_store.items():
            out.append(f"  {st}: {n} offers")
        out += self.warnings
        return out


def _clean(v) -> str | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


def _to_date(v) -> date | None:
    s = _clean(v)
    if s is None:
        return None
    try:
        ts = pd.to_datetime(s, errors="coerce")
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.date() if isinstance(ts, (pd.Timestamp, datetime)) else None


def read_export(path: Path | str) -> pd.DataFrame:
    """Read an export file as text only (nothing is converted or guessed)."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, dtype=str)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str)
    elif path.suffix.lower() == ".json":
        df = pd.read_json(path, dtype=str)
    else:
        raise ValueError(f"Unsupported export format: {path.name}")
    return df.dropna(axis=1, how="all")


def save_raw_copy(path: Path | str, out_dir: Path) -> Path:
    """Reference copy of the raw export, byte for byte."""
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / Path(path).name
    shutil.copy2(path, dest)
    return dest


def extract_raddar(df: pd.DataFrame, cfg: Config, week: FlyerWeek,
                   only_stores: list[str] | None = None) -> tuple[list[Offer], ExtractionReport]:
    cols = cfg.settings["sources"]["raddar"]["columns"]
    rep = ExtractionReport(week=week, rows_read=len(df))
    offset = int(cfg.settings["extraction"].get("page_number_offset", 0))
    wanted = only_stores or cfg.all_stores

    missing_cols = [c for k, c in cols.items() if c not in df.columns and k in ("store", "title", "price_text", "valid_from")]
    if missing_cols:
        raise ValueError(f"Export is missing required columns: {missing_cols}. Columns found: {list(df.columns)}")

    def get(row, key):
        col = cols.get(key)
        return _clean(row[col]) if col in row.index else None

    offers: list[Offer] = []
    seen: set = set()
    for idx, row in df.iterrows():
        raw_store = get(row, "store")
        store = cfg.canonical_store(raw_store)
        if store is None:
            rep.unknown_stores[raw_store or "(blank)"] = rep.unknown_stores.get(raw_store or "(blank)", 0) + 1
            continue
        if store not in wanted:
            continue
        flyer_id = get(row, "flyer_id")
        flyer_key = f"{store} / {flyer_id or '(no flyer id)'}"
        start = _to_date(row[cols["valid_from"]])
        if start is None:                       # §5: no start date -> excluded
            rep.excluded_no_start[flyer_key] = rep.excluded_no_start.get(flyer_key, 0) + 1
            continue
        if not week.contains(start):            # §3: filter by START date only
            _, n = rep.excluded_other_week.get(flyer_key, (start, 0))
            rep.excluded_other_week[flyer_key] = (start, n + 1)
            continue

        title, desc, price = get(row, "title"), get(row, "description"), get(row, "price_text")
        page_raw = get(row, "page")
        try:
            page = int(float(page_raw)) + offset if page_raw is not None else None
        except ValueError:
            page = None
        item_no = get(row, "item_no")
        if item_no and item_no.endswith(".0"):
            item_no = item_no[:-2]

        # §5: same flyer extracted twice -> keep one copy.
        key = (store, flyer_id, item_no, title, price, page_raw, get(row, "image_url"))
        if key in seen:
            rep.duplicates_removed += 1
            continue
        seen.add(key)

        o = Offer(uid=f"raddar-{idx}", source="raddar", store=store, flyer_id=flyer_id, title=title,
                  description=desc, price_text=price, image_url=get(row, "image_url"), page=page,
                  page_raw=page_raw, valid_from=start, valid_to=_to_date(row[cols["valid_to"]]) if cols["valid_to"] in row.index else None,
                  category=get(row, "category"), item_no=item_no, source_url=get(row, "source_url"),
                  pdf_path=get(row, "pdf_path"), pdf_status=get(row, "pdf_status"))
        if not title:
            o.flag("no title")
        if not price:
            o.flag("no price")
        if not o.image_url:
            o.flag("no image")
        if page is None:
            o.flag("no page")
        if o.valid_to is None:
            o.flag("no end date")
        offers.append(o)

    rep.kept = len(offers)
    min_offers = int(cfg.settings["extraction"]["min_offers_per_store"])
    for store in wanted:
        n = sum(1 for o in offers if o.store == store)
        rep.per_store[store] = n
        if n == 0:
            rep.warnings.append(f"!!! WARNING: STORE MISSING - {store.upper()} has NO flyer offers for {week}. The file is NOT complete.")
        elif n < min_offers:
            rep.warnings.append(f"!!! WARNING: VERY FEW OFFERS - {store.upper()} has only {n} offers for {week} (minimum expected {min_offers}). Check this store.")
    return offers, rep


def offers_to_frame(offers: list[Offer]) -> pd.DataFrame:
    return pd.DataFrame([o.to_row() for o in offers])
