"""Local OCR for flyer images (Tesseract, runs on this computer, no API, no LLM).

Reads (a) the plain text of an image and (b) the prices printed in it, including the flyer
style "big dollars + small cents" (spec §7 rule 8): the big and small parts are found by
their size and position and combined into one price. The big part is never read alone."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

try:
    import pytesseract
    from scipy import ndimage
    _OK = True
except Exception:  # pragma: no cover
    _OK = False

UNIT_AFTER = re.compile(r"^\s*(g|kg|ml|l|lb|lbs|oz|ct|pk|un|%|'s|’s|x)\b", re.I)


@dataclass
class ImagePrice:
    value: float
    kind: str = "price"            # price | member | multi_total
    qty: int | None = None         # for multi-buy
    per: str | None = None         # 'lb', '100g', 'kg' when the image says so
    how: str = "text"              # text | big+small


@dataclass
class ImageRead:
    ok: bool = False
    text: str = ""
    prices: list[ImagePrice] = field(default_factory=list)
    error: str | None = None

    def values(self) -> list[float]:
        return sorted({p.value for p in self.prices})


def ocr_available() -> bool:
    if not _OK:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _digits(img: Image.Image) -> str:
    """Read a tight crop that should contain only digits. Tried at a normal text size
    with three page modes; the first non-empty answer wins."""
    s = 48 / max(img.height, 1)
    img = img.resize((max(1, int(img.width * s)), 48), Image.LANCZOS)
    img = ImageOps.expand(img, border=24, fill=255)
    for psm in (7, 8, 10):
        txt = pytesseract.image_to_string(img, config=f"--psm {psm} -c tessedit_char_whitelist=0123456789")
        txt = re.sub(r"\D", "", txt)
        if txt:
            return txt
    return ""


def _big_small_prices(gray: np.ndarray, masked: list | None = None) -> list[ImagePrice]:
    """Find groups of big digits with 2 small digits to their upper right -> dollars.cents."""
    found: list[ImagePrice] = []
    H, W = gray.shape
    thr = (int(gray.min()) + int(gray.max())) / 2
    for ink in (gray < thr, gray > thr):                  # dark-on-light and light-on-dark
        if ink.mean() > 0.5:
            continue
        lab, n = ndimage.label(ink)
        if n == 0 or n > 4000:
            continue
        boxes = []
        for sl in ndimage.find_objects(lab):
            h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
            if h >= max(12, 0.04 * H) and w >= 3 and h / max(w, 1) < 8 and w / h < 1.6:
                boxes.append((sl[1].start, sl[0].start, sl[1].stop, sl[0].stop))
        boxes.sort()
        used: set = set()
        for i, b in enumerate(sorted(boxes, key=lambda b: -(b[3] - b[1]))):
            if b in used:
                continue
            bh = b[3] - b[1]
            # big run: similar height, same line, side by side
            run = [b]
            grown = True
            while grown:
                grown = False
                for c in boxes:
                    if c in run or c in used:
                        continue
                    ch = c[3] - c[1]
                    if abs(ch - bh) <= 0.15 * bh and abs(c[1] - b[1]) <= 0.15 * bh:
                        left, right = min(r[0] for r in run), max(r[2] for r in run)
                        if -0.1 * bh <= c[0] - right <= 0.45 * bh or -0.1 * bh <= left - c[2] <= 0.45 * bh:
                            run.append(c); grown = True
            run.sort()
            right, top = max(r[2] for r in run), min(r[1] for r in run)
            # small run: 30-70% of the big height, starts just right of the big run, top-aligned
            small = [c for c in boxes if c not in run and 0.3 * bh <= (c[3] - c[1]) <= 0.7 * bh
                     and -0.05 * bh <= c[0] - right <= 1.3 * bh and abs(c[1] - top) <= 0.3 * bh]
            small.sort()
            small = small[:2]
            if len(small) != 2 or small[1][0] - small[0][2] > 0.4 * bh:
                continue
            crop = lambda bs: Image.fromarray(np.where(ink, 0, 255).astype(np.uint8)).crop(
                (min(x[0] for x in bs) - 2, min(x[1] for x in bs) - 2, max(x[2] for x in bs) + 2, max(x[3] for x in bs) + 2))
            d, c = _digits(crop(run)), _digits(crop(small))
            if d and len(d) <= 4 and len(c) == 2:
                found.append(ImagePrice(float(f"{int(d)}.{c}"), how="big+small"))
                used.update(run); used.update(small)
                if masked is not None:
                    masked.extend(run + small)
    return found


PRICE_TXT = re.compile(r"(\$\s*\d+(?:[.,]\d{2})?|\b\d+[.,]\d{2}\b)")


def _text_prices(text: str) -> list[ImagePrice]:
    out: list[ImagePrice] = []
    flat = " ".join(text.split())
    for m in re.finditer(r"\b(\d+)\s*(?:for|/)\s*\$\s*(\d+(?:\.\d{2})?)", flat, re.I):
        out.append(ImagePrice(float(m.group(2)), kind="multi_total", qty=int(m.group(1))))
    for m in PRICE_TXT.finditer(flat):
        before, after = flat[max(0, m.start() - 24):m.start()].lower(), flat[m.end():m.end() + 12]
        if re.search(r"(save|off|was|reg\.?|regular)\s*$", before) or UNIT_AFTER.match(after):
            continue
        if re.search(r"\d+\s*(for|/)\s*$", before):
            continue
        val = float(m.group(1).replace("$", "").replace(",", ".").strip())
        kind = "member" if re.search(r"member", flat[max(0, m.start() - 40):m.end() + 40], re.I) else "price"
        per = None
        pm = re.match(r"\s*/\s*(lb|kg|100\s*g)", after, re.I)
        if pm:
            per = pm.group(1).lower().replace(" ", "")
        out.append(ImagePrice(val, kind=kind, per=per))
    return out


def read_image(img: Image.Image | str) -> ImageRead:
    """OCR one image. Never raises: a failed read comes back with ok=False."""
    if not ocr_available():
        return ImageRead(ok=False, error="OCR engine (Tesseract) not installed")
    try:
        if not isinstance(img, Image.Image):
            img = Image.open(img)
        img = img.convert("RGB")
        if max(img.size) < 900:                       # small cut-outs read better enlarged
            s = 900 / max(img.size)
            img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
        gray = np.asarray(ImageOps.grayscale(img))
        boxes: list = []
        prices = _big_small_prices(gray, boxes)
        if boxes:   # hide the big price glyphs so the normal-size text reads cleanly
            arr = np.asarray(img).copy()
            bg = np.median(arr.reshape(-1, 3), axis=0).astype(np.uint8)
            for x0, y0, x1, y1 in boxes:
                arr[max(0, y0 - 3):y1 + 3, max(0, x0 - 3):x1 + 3] = bg
            img = Image.fromarray(arr)
        text = pytesseract.image_to_string(img, config="--psm 11")
        big_vals = {p.value for p in prices}
        member_near = bool(re.search(r"member", text, re.I))
        for p in _text_prices(text):
            if p.value in big_vals:
                continue
            # A bare "$7" read from the plain text next to a big+small price is the big
            # part read alone (rule 8: never use it).
            if prices and p.kind == "price" and float(p.value).is_integer() and any(q.how == "big+small" for q in prices):
                continue
            prices.append(p)
        if member_near and len({p.value for p in prices}) == 1:
            for p in prices:
                p.kind = "member"
        return ImageRead(ok=True, text=text.strip(), prices=prices)
    except Exception as e:  # unreadable image: reported, never a crash
        return ImageRead(ok=False, error=f"{type(e).__name__}: {e}")
