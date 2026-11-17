"""Price rules (spec §7). All arithmetic is done here, in code, with exact decimals.

parse_price()  reads the price text of an offer (plus its description).
apply_image()  applies what the image shows (member price, text-vs-image conflict, rule 6).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

NUM = r"\d+(?:[.,]\d+)?"
CENT = Decimal("0.01")


def money(v) -> Decimal:
    return Decimal(str(v).replace(",", ".")).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Price:
    raw: str | None = None
    comparison: Decimal | None = None      # the price used for comparing (after all rules)
    selling: Decimal | None = None         # regular selling price, kept beside a member price
    member: Decimal | None = None
    multi_total: Decimal | None = None     # "2 for $7" -> 7.00
    multi_qty: int | None = None           #              -> 2
    unit_price: Decimal | None = None      #              -> 3.50
    single_price: Decimal | None = None    # "or $3.79 ea." when buying fewer
    buy_requirement: str | None = None     # shown in the file
    range_low: Decimal | None = None
    range_high: Decimal | None = None
    from_wording: str | None = None        # "from $7.99-39.99", kept visible
    basis: str = "each"                    # each | per_lb | per_kg | per_100g | per_lb_or_each
    save_only: bool = False
    text_price: Decimal | None = None      # what the text said, kept when the image wins
    image_price: Decimal | None = None     # what the image said
    usable: bool = True                    # False = must never be used as a winner
    flags: list[str] = field(default_factory=list)

    def flag(self, t: str) -> None:
        if t not in self.flags:
            self.flags.append(t)

    def note(self) -> str:
        """Wording shown next to the number in the Excel file."""
        parts = []
        if self.buy_requirement:
            parts.append(self.buy_requirement)
        if self.from_wording:
            parts.append(self.from_wording)
        if self.member is not None and self.comparison == self.member:
            parts.append("member" + (f"; reg. ${self.selling}" if self.selling is not None else ""))
        if self.basis != "each":
            parts.append({"per_lb": "/lb", "per_kg": "/kg", "per_100g": "/100 g", "per_lb_or_each": "/lb or ea."}[self.basis])
        return "; ".join(parts)


def _norm(s: str) -> str:
    s = s.replace(" ", " ").replace("–", "-").replace("—", "-")
    return " ".join(s.split())


def _cents(s: str) -> str:
    """66¢ -> $0.66 so it is read as money, not as 66 dollars."""
    return re.sub(r"(\d+)\s*¢", lambda m: f"${int(m.group(1)) / 100:.2f}", s)


MEMBER_A = re.compile(rf"member\S*(?:[ -]only)?\s+price\s*:?\s*\$?\s*({NUM})", re.I)
MEMBER_B = re.compile(rf"\$?\s*({NUM})\s*(?:ea\.?\s*)?(?:for\s+)?members?\b(?!\S*-only\s+price)", re.I)
NONMEMBER = re.compile(rf"(?:special price|regular price|non-?member price)\s*\$?\s*({NUM})|\$?\s*({NUM})\s*(?:ea\.?\s*)?for\s+non-?members", re.I)
WASNOW = re.compile(rf"was\s*\$?\s*({NUM}).*?now\s*\$?\s*({NUM})", re.I)
BOGO = re.compile(r"\bbogo\b|buy\s*(?:1|one)\b.{0,12}get\s*(?:1|one)\b.{0,6}free", re.I)
MULTI_FOR = re.compile(rf"\b(\d+)\s*for\s*\$?\s*({NUM})", re.I)           # 2 for $7
MULTI_SLASH = re.compile(rf"(?<![\d.])(\d+)\s*/\s*\$\s*({NUM})")           # 2/$5
MULTI_BARE = re.compile(rf"^(\d+)\s*/\s*({NUM})$")                         # 2/7
RADDAR_MULTI = re.compile(rf"^\$\s*({NUM})(\s.*?)?\s(\d+)(?:\s+for)?$", re.I)  # "$6.0 $OR 3.79 EA. 2 for", "$7.0 2"
SINGLE_ALT = re.compile(rf"(?:or(?:\s+pay)?|less than \d+ pay)\s*\$?\s*({NUM})\s*(ea|each|case)", re.I)
RANGE = re.compile(rf"\$\s*({NUM})\s*-\s*\$?\s*({NUM})")
FROM = re.compile(rf"\b(from|starting at|starting from)\s*\$?\s*({NUM})", re.I)
SAVE = re.compile(rf"\bsave\b\s*(?:up to\s*)?\$?\s*{NUM}\s*%?", re.I)
PER = r"(lb|kg|100\s*g)"


def _basis(t: str, first_end: int) -> str:
    """Is the FIRST number a pack price or a per-weight price?"""
    tail = t[first_end:]
    low = tail.lower()
    attached = re.match(rf"\s*/\s*{PER}\b", low)
    each = bool(re.search(r"(?:^|[/\s])(ea|each)\b", low))
    if attached:
        unit = attached.group(1).replace(" ", "")
        rest = low[attached.end():]
        if unit == "lb" and re.match(r"\s*(/|or)\s*ea", rest):
            return "per_lb_or_each"
        return {"lb": "per_lb", "kg": "per_kg", "100g": "per_100g"}[unit]
    if re.match(r"\s*ea\s*/\s*lb", low):
        return "per_lb_or_each"
    # "$2.99 $6.59/kg /lb": the trailing unit that is NOT glued to a number belongs to the first price
    loose = re.search(rf"(?<![\d.])\s/\s*{PER}\b(?!\s*\d)", " " + low)
    if loose:
        unit = loose.group(1).replace(" ", "")
        if unit == "lb" and (re.search(r"/\s*lb\s*(/|or)\s*ea", low) or each):
            return "per_lb_or_each"
        return {"lb": "per_lb", "kg": "per_kg", "100g": "per_100g"}[unit]
    return "each"      # "$10.99 $2.43/100 g": pack price; the unit-price part is ignored


def parse_price(price_text: str | None, description: str | None = None) -> Price:
    p = Price(raw=price_text)
    desc = _norm(description) if description else ""
    if price_text is None or not str(price_text).strip():
        _from_description(p, desc)
        if p.comparison is None:
            p.flag("no price")
        return p
    t = _cents(_norm(str(price_text)))

    # Rule 3 - SAVE-only: a saving is never a price.
    if SAVE.search(t):
        rest = SAVE.sub(" ", t)
        p.save_only = True
        if re.search(r"\d", rest):
            p.flag("SAVE offer - prices unclear")
        else:
            p.flag("SAVE only - no price")
        _from_description(p, desc)
        return p

    # Rule 1 - member price in the text.
    m = MEMBER_A.search(t) or MEMBER_B.search(t)
    if m:
        p.member = money(m.group(1))
        n = NONMEMBER.search(t)
        if n:
            p.selling = money(n.group(1) or n.group(2))
        p.comparison = p.member
        return p

    # Rule 4 - was/now: only the new price.
    m = WASNOW.search(t)
    if m:
        p.comparison = p.selling = money(m.group(2))
        return p

    # Rule 2 - BOGO: unit price = price / 2.
    if BOGO.search(t):
        nums = re.findall(NUM, BOGO.sub(" ", t))
        if nums:
            _multi(p, money(nums[0]), 2, "buy 1 get 1 free")
        else:
            p.flag("no price")
        return p

    # Rule 2 - multi-buy.
    m = MULTI_BARE.match(t) or MULTI_SLASH.search(t)
    if m:
        _multi(p, money(m.group(2)), int(m.group(1)))
    else:
        m = RADDAR_MULTI.match(t)
        if m and "/" not in (m.group(2) or "") and int(m.group(3)) >= 2:
            _multi(p, money(m.group(1)), int(m.group(3)))
        else:
            m = MULTI_FOR.search(t)
            if m and int(m.group(1)) >= 2:
                _multi(p, money(m.group(2)), int(m.group(1)))
    if p.multi_qty:
        s = SINGLE_ALT.search(t)
        if s:
            p.single_price = money(s.group(1))
            p.buy_requirement += f" (or ${p.single_price} {s.group(2).lower()})"
        return p

    # Rule 5 - price range / "from $X".
    r, f = RANGE.search(t), FROM.search(t)
    if r:
        lo, hi = sorted((money(r.group(1)), money(r.group(2))))
        p.range_low, p.range_high = lo, hi
        p.comparison = p.selling = lo
        p.from_wording = (f"{f.group(1).lower()} " if f else "") + f"${lo}-${hi}"
        return p
    if f:
        p.range_low = p.comparison = p.selling = money(f.group(2))
        p.from_wording = f"{f.group(1).lower()} ${p.range_low}"
        return p

    # Plain price. Cleaning: words like NOW / ONLY / EA are dropped, only the number is kept.
    m = re.search(NUM, t)
    if not m:
        p.flag("no price")
        return p
    p.comparison = p.selling = money(m.group())
    p.basis = _basis(t, m.end())           # rule 6: pack price, or per-lb / per-100 g only
    _from_description(p, desc)
    return p


def _multi(p: Price, total: Decimal, qty: int, label: str | None = None) -> None:
    p.multi_total, p.multi_qty = total, qty
    p.unit_price = money(total / qty)
    p.comparison = p.selling = p.unit_price
    p.buy_requirement = label or f"{qty} for ${total}"
    if label:
        p.buy_requirement = f"{label}: {qty} for ${total}"


def _from_description(p: Price, desc: str) -> None:
    """Rule 1: a member price can also be written in the description."""
    if not desc:
        return
    m = MEMBER_A.search(desc)
    if m:
        member = money(m.group(1))
        if p.comparison is not None and p.selling is None:
            p.selling = p.comparison
        p.member = member
        p.comparison = member
        p.save_only = False


def apply_image(p: Price, image) -> Price:
    """Apply what the image shows (image = ocr.ImageRead or None). Rules 1, 6, 7."""
    if image is None or not getattr(image, "ok", False) or not image.prices:
        return p            # no image price: no problem, the text price stands
    known = {v for v in (p.comparison, p.selling, p.member, p.multi_total, p.unit_price,
                         p.single_price, p.range_low, p.range_high) if v is not None}
    members = sorted({money(x.value) for x in image.prices if x.kind == "member"})
    multis = [x for x in image.prices if x.kind == "multi_total" and x.qty]
    plain = sorted({money(x.value) for x in image.prices if x.kind == "price"})

    # Rule 1 - member price shown in the image is the comparison price.
    if members:
        m = members[0]
        if p.member is None or p.member != m:
            if p.comparison is not None and p.comparison != m:
                p.text_price = p.comparison
                if p.selling is None or p.selling == p.member:
                    p.selling = p.comparison
                if m not in known:
                    p.flag(f"price conflict: text ${p.comparison}, image ${m} (image used)")
            p.member = m
        p.image_price = m
        p.comparison = m
        p.usable = True
        return p

    if multis and not p.multi_qty:
        x = multis[0]
        before = p.comparison
        _multi(p, money(x.value), int(x.qty))
        p.image_price = p.unit_price
        if before is not None and before not in (p.unit_price, p.multi_total):
            p.text_price = before
            p.flag(f"price conflict: text ${before}, image {x.qty} for ${money(x.value)} (image used)")
        return p

    new = [v for v in plain if v not in known]
    if not plain or not new:
        if plain:
            p.image_price = p.comparison if p.comparison in plain else plain[0]
        return p            # the image agrees with the text
    if len(new) > 1:
        # several unexplained prices in one image: never guess which one belongs to the product
        p.flag("shared image - price unclear")
        p.usable = False
        return p
    img = new[0]
    p.image_price = img
    if p.comparison is None:
        p.comparison = p.selling = img          # text had no price; the image has one
        p.flags = [f for f in p.flags if f not in ("no price", "SAVE only - no price")]
        p.flag("price read from image")
    elif p.basis != "each":
        p.text_price = p.comparison             # rule 6: the image shows a price -> use it, no flag
        p.comparison = p.selling = img
        p.basis = "each"
    else:
        p.text_price = p.comparison             # rule 7: image wins, both kept, conflict flagged
        p.flag(f"price conflict: text ${p.text_price}, image ${img} (image used)")
        p.comparison = img
    return p


LB_IN_G = Decimal("453.59237")
OZ_IN_G = Decimal("28.349523125")


def cheapest(prices: list[Price]) -> Price | None:
    """Rule 9 / 'same product twice': the lowest usable comparison price."""
    ok = [p for p in prices if p.usable and p.comparison is not None]
    return min(ok, key=lambda p: p.comparison) if ok else None
