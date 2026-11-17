"""Size reading and comparison (spec §6).

Everything is converted to one unit per kind (grams, millilitres, pieces) so that lb and g,
or l and ml, are comparable. Sizes are compared; a price per weight is never computed."""
from __future__ import annotations
import re
from dataclasses import dataclass

from .text import clean

NUM = r"\d+(?:\.\d+)?"
MASS = {"kg": 1000.0, "g": 1.0, "gr": 1.0, "gram": 1.0, "grams": 1.0, "mg": 0.001,
        "lb": 453.59237, "lbs": 453.59237, "pound": 453.59237, "pounds": 453.59237, "oz": 28.349523125}
VOL = {"ml": 1.0, "l": 1000.0, "lt": 1000.0, "litre": 1000.0, "litres": 1000.0, "liter": 1000.0, "liters": 1000.0, "cl": 10.0}
COUNT = ["count", "ct", "packs", "pack", "pk", "units", "unit", "un", "rolls", "roll", "sheets", "sheet", "pods", "pod",
         "pieces", "piece", "pcs", "capsules", "tablets", "loads", "wipes", "diapers", "bags", "eggs", "'s", "s"]
EACH = ["each", "ea"]
_units = sorted(list(MASS) + list(VOL) + COUNT + EACH, key=len, reverse=True)
UNIT = "(?:" + "|".join(re.escape(u) for u in _units) + r")(?![a-z])"


@dataclass(frozen=True)
class Size:
    dim: str                    # mass | vol | count | nxm
    lo: float = 0.0             # in g / ml / pieces
    hi: float = 0.0
    pack: int | None = None     # 6 in "6 x 710 ml"
    nums: tuple = ()            # numbers exactly as written for 'NxM' and 'N=M'
    upto: bool = False
    each: bool = False
    raw: str = ""

    def __str__(self) -> str:
        return self.raw


def _conv(value: str, unit: str | None):
    """-> (dim, converted value). Unknown unit -> None."""
    v = float(value)
    if unit is None:
        return None
    u = unit.strip().lower()
    if u in MASS:
        return "mass", v * MASS[u]
    if u in VOL:
        return "vol", v * VOL[u]
    if u in EACH:
        return "each", v
    return "count", v


P_EQ = re.compile(rf"(?<![\d.])(\d+)\s*=\s*(\d+)(?:\s*rolls?)?")
P_X = re.compile(rf"(?<![\d.a-z])(\d+)\s*x\s*({NUM})\s*({UNIT})?(?:\s*(-|/)\s*({NUM})\s*({UNIT})?)?")
P_UPTO = re.compile(rf"up to\s*({NUM})\s*({UNIT})")
P_RANGE = re.compile(rf"(?<![\d.])({NUM})\s*({UNIT})?\s*(?:-|to)\s*({NUM})\s*({UNIT})")
P_SLASH = re.compile(rf"(?<![\d./])({NUM})\s*({UNIT})?\s*/\s*({NUM})\s*({UNIT})")
P_OF = re.compile(r"(?:pkg|pack|package|bag|box|case) of\s*(\d+)(?![\d.])")
P_SINGLE = re.compile(rf"(?<![\d.=x/-])({NUM})(?:\s*|-)({UNIT})")
P_DOZEN = re.compile(r"\bdozen\b")
P_EACH = re.compile(r"(?<![a-z\d/])(?:each|ea)(?![a-z/])")


def find_sizes(text: str | None) -> list[tuple[Size, tuple[int, int]]]:
    """All sizes written in a text, in reading order, with their position.
    A number with no unit is not a size and is ignored ('Broccoli Crown 28')."""
    t = clean(text)
    found: list[tuple[Size, tuple[int, int]]] = []
    taken = [False] * (len(t) + 1)

    def free(m):
        return not any(taken[m.start():m.end()])

    def take(m, size):
        if size is None:
            return
        for i in range(m.start(), m.end()):
            taken[i] = True
        found.append((size, (m.start(), m.end())))

    for m in P_EQ.finditer(t):                      # 12=24 -> 24 pieces in total, both kept
        n, total = int(m.group(1)), int(m.group(2))
        take(m, Size("count", total, total, nums=(n, total), raw=m.group().strip()))
    for m in P_X.finditer(t):                       # 6x710 ml / 4x95/100g / 6x126
        if not free(m):
            continue
        pack, a, ua, sep, b, ub = m.groups()
        unit = ua or ub
        raw = m.group().strip()
        if unit is None:                            # NxM with no unit: keep ALL numbers as written
            nums = (int(pack), float(a)) if sep is None else (int(pack), float(a), sep, float(b))
            take(m, Size("nxm", nums=nums, raw=raw))
            continue
        ca = _conv(a, ua or ub)
        if sep == "-" and b is not None:
            cb = _conv(b, ub or ua)
            lo, hi = sorted((ca[1], cb[1]))
            take(m, Size(_d(ca[0]), lo, hi, pack=int(pack), nums=(int(pack), float(a)), raw=raw))
        else:                                       # slash: always the FIRST size
            take(m, Size(_d(ca[0]), ca[1], ca[1], pack=int(pack), nums=(int(pack), float(a)), raw=raw))
    for m in P_UPTO.finditer(t):                    # 'up to 454 g': X is the maximum
        if free(m):
            c = _conv(m.group(1), m.group(2))
            take(m, Size(_d(c[0]), 0.0, c[1], upto=True, raw=m.group().strip()))
    for m in P_RANGE.finditer(t):                   # 400-500 g / 750 g - 1 kg
        if free(m):
            a, ua, b, ub = m.groups()
            ca, cb = _conv(a, ua or ub), _conv(b, ub)
            if _d(ca[0]) != _d(cb[0]):
                continue
            lo, hi = sorted((ca[1], cb[1]))
            take(m, Size(_d(ca[0]), lo, hi, raw=m.group().strip()))
    for m in P_SLASH.finditer(t):                   # '750 g / 1 kg' -> 750 g (never the second)
        if free(m):
            a, ua, b, ub = m.groups()
            ca = _conv(a, ua or ub)
            take(m, Size(_d(ca[0]), ca[1], ca[1], raw=m.group().strip()))
    for m in P_OF.finditer(t):
        if free(m):
            n = int(m.group(1))
            take(m, Size("count", n, n, raw=m.group().strip()))
    for m in P_SINGLE.finditer(t):
        if free(m):
            c = _conv(m.group(1), m.group(2))
            take(m, Size(_d(c[0]), c[1], c[1], each=(c[0] == "each"), raw=m.group().strip()))
    for m in P_DOZEN.finditer(t):
        if free(m):
            take(m, Size("count", 12, 12, raw="dozen"))
    found.sort(key=lambda x: x[1][0])
    return found


def _d(dim: str) -> str:
    return "count" if dim == "each" else dim


def sizes_in(text: str | None) -> list[Size]:
    return [s for s, _ in find_sizes(text)]


def same_size(wanted: Size, offer: Size, tol: float = 0.02) -> bool:
    """Is the flyer size the same as the wanted (list) size? Max difference = tol (2%).
    Ranges match when they overlap. Pack structure must match (6 x 100 g is not 600 g)."""
    def overlap(wlo, whi, olo, ohi):
        return olo <= whi * (1 + tol) and ohi >= wlo * (1 - tol)

    if wanted.dim == "nxm" or offer.dim == "nxm":
        a = wanted.nums if wanted.nums else ()
        b = offer.nums if offer.nums else ()
        return bool(a) and bool(b) and tuple(a[:2]) == tuple(b[:2])
    if wanted.dim in ("mass", "vol"):
        if offer.dim != wanted.dim:
            return False
        if (wanted.pack or 1) != (offer.pack or 1):
            return False
        return overlap(wanted.lo, wanted.hi, offer.lo, offer.hi)
    # wanted is a count
    if offer.dim == "count":
        return overlap(wanted.lo, wanted.hi, offer.lo, offer.hi)
    if offer.pack is not None:                      # 'Pepsi 24 pk' vs '24 x 355 ml'
        return wanted.lo <= offer.pack <= wanted.hi
    return False


def any_same_size(wanted: list[Size], offered: list[Size], tol: float = 0.02) -> bool:
    return any(same_size(w, o, tol) for w in wanted for o in offered)
