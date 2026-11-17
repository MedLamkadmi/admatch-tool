"""The weekly run: list + flyers -> one result row per list item (spec §§6-8)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .config import Config
from .extract import extract_raddar, read_export, save_raw_copy, offers_to_frame, ExtractionReport
from .images import ImageStore
from .match import Matcher, Match, ListItem
from .models import Offer
from .prices import Price, parse_price, apply_image
from .sizes import any_same_size
from .week import target_week, FlyerWeek
from . import ocr

@dataclass
class Side:
    """The winning offer on one side (Superstore or cheapest competitor)."""
    offer: Offer | None = None
    price: Price | None = None
    image: Path | None = None
    image_note: str | None = None      # text shown when there is no picture
    flags: list = field(default_factory=list)

    @property
    def value(self) -> Decimal | None:
        return self.price.comparison if self.price else None


@dataclass
class Row:
    item: str
    list_row: int
    status: str = "EMPTY"
    diff: Decimal | None = None
    comp: Side = field(default_factory=Side)
    home: Side = field(default_factory=Side)
    flags: list = field(default_factory=list)
    review: list = field(default_factory=list)      # (side, store, text, price, reason)


@dataclass
class RunResult:
    week: FlyerWeek
    rows: list
    report: ExtractionReport
    offers: list
    warnings: list
    items: list


def load_fixed_list(path: Path | str, cfg: Config) -> list[str]:
    """Reads the real file. Does not assume its columns."""
    path = Path(path)
    df = pd.read_csv(path, dtype=str) if path.suffix.lower() == ".csv" else pd.read_excel(path, dtype=str)
    df = df.dropna(axis=1, how="all")
    want = str(cfg.settings.get("fixed_list", {}).get("item_column", "auto"))
    if want != "auto":
        cols = {str(c).strip().lower(): c for c in df.columns}
        if want.strip().lower() not in cols:
            raise ValueError(f"Fixed list: column '{want}' not found. Columns in the file: {list(df.columns)}")
        col = cols[want.strip().lower()]
    elif df.shape[1] == 1:
        col = df.columns[0]
    else:
        raise ValueError("Fixed list has several columns and I will not guess which one holds the product names. "
                         f"Set fixed_list.item_column in config/settings.yaml. Columns in the file: {list(df.columns)}")
    return [str(v).strip() for v in df[col] if isinstance(v, str) and v.strip()]


def status_of(home: Decimal | None, comp: Decimal | None) -> tuple[str, Decimal | None]:
    if home is not None and comp is not None:
        diff = home - comp
        return ("HIGH" if diff > 0 else "LOW" if diff < 0 else "EQUAL"), diff
    if comp is not None:
        return "NOT IN SUPERSTORE", None
    if home is not None:
        return "NOT IN COMPETITOR", None
    return "EMPTY", None


def item_sizes(item: ListItem) -> list:
    return [s for b in item.branches for s in b.sizes]


def drop_shared_sizeless(found: list, tol: float) -> dict:
    """Mohamed, 16 Nov 2026: one product has one size. A sizeless offer may be the match for at
    most ONE pack size of the list. The rule is applied PER BRANCH of the offer (fix 8): each OR
    branch is its own product, so 'peppers or lemons or onions' may match one list item per branch.
    When one branch matches list items with different stated pack sizes, that branch is matched
    to NONE of them.
    found = [(item, matches)]. Changes the match lists in place; -> {list row: [offers removed]}."""
    by_offer: dict = {}
    for item, matches in found:
        if not item_sizes(item):
            continue                                # list item states no size: not a pack size
        for m in matches:
            if not m.branch.sizes:
                by_offer.setdefault((m.offer.uid, id(m.branch)), []).append((item, m))
    removed: dict = {}
    for key, hits in by_offer.items():
        sizes = [item_sizes(i) for i, _ in hits]
        one_size = all(any_same_size(a, b, tol) for k, a in enumerate(sizes) for b in sizes[k + 1:])
        if one_size:
            continue
        for item, m in hits:
            removed.setdefault(item.row, []).append(m)
    if removed:
        for item, matches in found:
            gone = removed.get(item.row, [])
            matches[:] = [m for m in matches if not any(m is g for g in gone)]
    return {row: [m.offer for m in ms] for row, ms in removed.items()}


class Run:
    def __init__(self, cfg: Config, out_dir: Path | str, use_images: bool | None = None):
        self.cfg = cfg
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        icfg = cfg.settings.get("images", {})
        self.use_images = icfg.get("download", True) if use_images is None else use_images
        self.read_prices = bool(icfg.get("read_prices", True)) and ocr.ocr_available()
        self.store = ImageStore(self.out / "images", timeout=int(icfg.get("timeout_seconds", 20)))
        self.matcher = Matcher(cfg)
        self.warnings: list[str] = []
        self._img_cache: dict = {}

    # ---- price of one offer, with its image ------------------------------------------
    def priced(self, offer: Offer) -> tuple[Price, Path | None, str | None]:
        if offer.uid in self._img_cache:
            return self._img_cache[offer.uid]
        price = parse_price(offer.price_text, offer.description)
        path, note = None, None
        if not offer.image_url:
            note = "no image"
        elif self.use_images:
            path = self.store.get(offer.image_url)
            if path is None:
                note = "no image (download failed)"
            elif self.read_prices:
                read = ocr.read_image(path)
                if read.ok and self._image_is_other_product(offer, read.text):
                    price.flag("image shows a different product than the title - price not used")
                    price.usable = False
                else:
                    apply_image(price, read)
        else:
            note = "no image (images switched off)"
        self._img_cache[offer.uid] = (price, path, note)
        return price, path, note

    def _image_is_other_product(self, offer: Offer, image_text: str) -> bool:
        """Only when the image carries clear product wording that shares nothing with the title."""
        img = [t for t in self.matcher.norm.tokens(image_text) if len(t) >= 4 and t.isalpha()]
        if len(img) < 3:
            return False
        title = {t for t in self.matcher.norm.tokens((offer.title or "") + " " + (offer.description or "")) if len(t) >= 4}
        if not title:
            return False
        return not any(self.matcher._has(t, title) for t in img)

    # ---- choosing the winner of one side ---------------------------------------------
    def pick(self, matches: list[Match], row: Row, side_name: str) -> Side:
        cands = []
        for m in matches:
            price, path, note = self.priced(m.offer)
            cands.append((m, price, path, note))
        usable = [c for c in cands if c[0].usable and c[1].usable and c[1].comparison is not None]
        sized = [c for c in usable if "size unclear" not in c[0].flags]
        pool = sized or usable            # a confirmed size beats a match made on the name alone
        for m, price, _, _ in cands:
            if (m, price) not in [(c[0], c[1]) for c in pool]:
                why = ("no usable price" if price.comparison is None or not price.usable else
                       "not used as winner: " + ", ".join(m.flags) if not m.usable else
                       "size unclear - a match with a confirmed size was preferred" if c_in(m, usable) and sized else
                       "matched, higher price")
                if price.comparison is None or not price.usable or not m.usable or (c_in(m, usable) and sized):
                    row.review.append((side_name, m.offer.store, m.offer.title, price, "; ".join([why] + price.flags)))
        if not pool:
            return Side()
        order = {s: i for i, s in enumerate(self.cfg.all_stores)}
        m, price, path, note = min(pool, key=lambda c: (c[1].comparison, order.get(c[0].offer.store, 99)))
        side = Side(offer=m.offer, price=price, image=path, image_note=note,
                    flags=list(m.flags) + list(price.flags) + list(m.offer.flags))
        for o_m, o_p, _, _ in pool:
            if o_m is not m:
                row.review.append((side_name, o_m.offer.store, o_m.offer.title, o_p, "matched, not the cheapest"))
        return side

    # ---- the run ---------------------------------------------------------------------
    def run(self, fixed_list: Path | str, raddar: Path | str, run_date: date,
            only_items: list[int] | None = None, week: FlyerWeek | None = None) -> RunResult:
        cfg = self.cfg
        week = week or target_week(run_date, cfg.settings["week"])
        save_raw_copy(raddar, self.out)
        save_raw_copy(fixed_list, self.out)
        offers, report = extract_raddar(read_export(raddar), cfg, week)
        offers_to_frame(offers).to_csv(self.out / "offers_extracted.csv", index=False, encoding="utf-8-sig")
        self.warnings += report.warnings

        names = load_fixed_list(fixed_list, cfg)
        idx = only_items if only_items is not None else range(len(names))
        items: list[ListItem] = [self.matcher.read_list_item(i, names[i]) for i in idx]

        parsed = []
        for o in offers:
            branches = self.matcher.read_offer(o)
            if not (o.title or o.description):
                o.flag("unidentified offer - no title and no description")
            parsed.append((o, branches))
        per_weight = {o.uid for o in offers if parse_price(o.price_text, o.description).basis != "each"}
        home_pool = [(o, b) for o, b in parsed if o.store == cfg.home_store]
        comp_pool = [(o, b) for o, b in parsed if o.store != cfg.home_store]
        limit = int(cfg.table("matching.yaml").get("max_review_lines_per_item", 8))

        found = []
        for item in items:
            hm, hnear = self.matcher.find(item, home_pool, home=True, per_weight=per_weight)
            cm, cnear = self.matcher.find(item, comp_pool, home=False, per_weight=per_weight)
            found.append((item, hm, hnear, cm, cnear))
        tol = self.matcher.tol
        h_removed = drop_shared_sizeless([(f[0], f[1]) for f in found], tol)
        c_removed = drop_shared_sizeless([(f[0], f[3]) for f in found], tol)

        rows = []                                   # master-list order, one row per list item (no sorting)
        for item, hm, hnear, cm, cnear in found:
            row = Row(item=item.raw, list_row=item.row, flags=list(item.flags))
            for side_name, gone in (("Superstore", h_removed.get(item.row, [])), ("Competitor", c_removed.get(item.row, []))):
                for o in gone:
                    row.review.append((side_name, o.store, o.title, parse_price(o.price_text, o.description),
                                       "NOT matched: sizeless offer, size unknown (it matches list items with different pack sizes)"))
            row.home = self.pick(hm, row, "Superstore")
            row.comp = self.pick(cm, row, "Competitor")
            row.status, row.diff = status_of(row.home.value, row.comp.value)
            for side_name, near in (("Superstore", hnear), ("Competitor", cnear)):
                for n in near[:limit]:
                    row.review.append((side_name, n.offer.store, n.offer.title, parse_price(n.offer.price_text, n.offer.description),
                                       "NOT matched: " + n.reason))
            rows.append(row)

        used = {o.image_url for r in rows for o in (r.home.offer, r.comp.offer) if o is not None and o.image_url}
        failed = [u for u in used if u in self.store.failed]
        share = float(cfg.settings.get("images", {}).get("warn_if_failed_share", 0.2))
        if self.use_images and used and len(failed) / len(used) > share:
            self.warnings.append(f"!!! WARNING: {len(failed)} of {len(used)} IMAGES COULD NOT BE DOWNLOADED. "
                                 "Pictures are missing in the file and image prices (member price, conflicts) were NOT checked. "
                                 f"First error: {self.store.failed[failed[0]]}")
        if self.use_images and bool(cfg.settings.get("images", {}).get("read_prices", True)) and not ocr.ocr_available():
            self.warnings.append("!!! WARNING: TESSERACT OCR IS NOT INSTALLED. Image prices (member price, conflicts) were NOT checked.")
        return RunResult(week=week, rows=rows, report=report, offers=offers, warnings=self.warnings, items=items)


def c_in(m: Match, cands: list) -> bool:
    return any(c[0] is m for c in cands)
