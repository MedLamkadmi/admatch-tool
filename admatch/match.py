"""Matching (spec §6). Rules + fuzzy spelling tolerance only. No LLM, no model call.

Identity first, price second: an offer is a match only when the product words, the brand
rule and the size all agree. Anything close-but-not-sure goes to the review list and is
never used as a price."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .config import Config
from .models import Offer
from .sizes import Size, find_sizes, any_same_size
from .text import Normalizer, clean


@dataclass
class Branch:
    """One product of an 'A or B' text, with its own brand(s), words and size(s)."""
    brands: set = field(default_factory=set)       # normalised brand names
    words: list = field(default_factory=list)      # product words (no brand, no variant words)
    all_tokens: list = field(default_factory=list)
    sizes: list = field(default_factory=list)
    context: set = field(default_factory=set)      # words shared from the other parts of the text
    size_link_unclear: bool = False
    text: str = ""
    offer_tokens: list = field(default_factory=list)   # every word of the whole text
    words_from_brand: bool = False                 # the text named only a brand; words = what the brand means
    from_slash: bool = False
    size_options: bool = False                     # sizes joined by 'or' after the names: options for every product


@dataclass
class ListItem:
    row: int
    raw: str
    branches: list = field(default_factory=list)
    grade: str | None = None
    flags: list = field(default_factory=list)


@dataclass
class Match:
    offer: Offer
    branch: Branch
    flags: list = field(default_factory=list)
    usable: bool = True


@dataclass
class NearMiss:
    offer: Offer
    reason: str


class Matcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.m = cfg.table("matching.yaml")
        self.norm = Normalizer(cfg)
        self.tol = float(self.m.get("size_tolerance", 0.02))
        self.fuzzy = int(self.m.get("fuzzy_word_score", 90))
        self.variants = self._phrases(self.m.get("variants", []))
        self.modifiers = {t for p in self.m.get("modifiers", []) for t in self.norm.tokens(p)}
        self.grades = sorted((self.norm.phrase(g) for g in self.m.get("grades", [])), key=len, reverse=True)
        self.remove = [re.compile(p) for p in self.m.get("remove_patterns", [])]
        # brands
        self.brands: dict = {}                      # token tuple -> canonical name
        self.store_brands: set = set()
        sb = cfg.table("store_brands.yaml")
        for name, info in sb.get("brands", {}).items():
            canon = " ".join(self.norm.tokens(name))
            self.store_brands.add(canon)
            self.brands[self.norm.phrase(name)] = canon
        self.store_brand_prefixes = {" ".join(self.norm.tokens(n)) for n, i in sb.get("brands", {}).items() if i.get("prefix")}
        path = cfg.dir / "brands.txt"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    p = self.norm.phrase(line)
                    if p:
                        self.brands.setdefault(p, " ".join(p))
        # what a brand means
        self.knowledge: dict = {}
        for brand, info in cfg.table("product_knowledge.yaml").get("brands", {}).items():
            p = self.norm.phrase(brand)
            canon = " ".join(p)
            self.brands.setdefault(p, canon)
            info = info or {}
            self.knowledge[canon] = {
                "product": self.norm.tokens(info.get("product") or ""),
                "also_ok": {t for a in (info.get("also_ok") or []) for t in self.norm.tokens(a)},
            }
        self.max_brand_len = max((len(k) for k in self.brands), default=1)

    def _know(self, brand: str) -> dict | None:
        """What a brand means. 'danone oikos' also gets what 'oikos' means."""
        if brand in self.knowledge:
            return self.knowledge[brand]
        padded = f" {brand} "
        for key, info in self.knowledge.items():
            if f" {key} " in padded:
                return info
        return None

    def _brand_ok(self, list_brands: set, offer_brands: set) -> bool:
        """Brand rule (Mohamed, 16 Nov 2026), both sides: a maker brand needs the same maker brand;
        a store brand accepts any store brand."""
        if self._brand_hit(list_brands, offer_brands):
            return True
        return bool(list_brands & self.store_brands) and bool(offer_brands & self.store_brands)

    @staticmethod
    def _or_joined(text: str, spans: list) -> bool:
        """True when the sizes are joined only by 'or' / '=' ('18 pk or 12 pk'), not by commas."""
        for (_, (_, b1)), (_, (a2, _)) in zip(spans, spans[1:]):
            if re.sub(r"\bor\b|=|\s", "", text[b1:a2]):
                return False
        return True

    @staticmethod
    def _brand_hit(a: set, b: set) -> bool:
        for x in a:
            for y in b:
                if x == y or f" {x} " in f" {y} " or f" {y} " in f" {x} ":
                    return True
        return False

    def _phrases(self, items) -> list:
        return sorted({self.norm.phrase(i) for i in items if self.norm.phrase(i)}, key=len, reverse=True)

    # ---- reading a text into branches ------------------------------------------------
    def _strip_noise(self, s: str) -> str:
        for rx in self.remove:
            s = rx.sub(" ", s)
        return " ".join(s.split())

    def _find_brands(self, toks: list) -> tuple[set, list]:
        """-> (brands found, tokens that are not part of a brand name)."""
        brands, rest, i = set(), [], 0
        while i < len(toks):
            hit = None
            for n in range(min(self.max_brand_len, len(toks) - i), 0, -1):
                key = tuple(toks[i:i + n])
                if key in self.brands:
                    hit = (n, self.brands[key])
                    break
            if hit is None and toks[i] in self.store_brand_prefixes:
                hit = (1, toks[i])                     # any name starting with "Compliments"
            if hit:
                brands.add(hit[1])
                i += hit[0]
            else:
                rest.append(toks[i])
                i += 1
        return brands, rest

    def _drop(self, toks: list, phrases: list) -> list:
        out, i = [], 0
        while i < len(toks):
            for p in phrases:
                if tuple(toks[i:i + len(p)]) == p:
                    i += len(p)
                    break
            else:
                out.append(toks[i])
                i += 1
        return out

    def read(self, names: str | None, extra_sizes_text: str | None = None, is_list: bool = False) -> list[Branch]:
        """Split a product text into OR-branches. Each branch keeps its own size."""
        t = self._strip_noise(clean(names))
        spans = find_sizes(t)
        # put a marker where each size stands so a size stays with its own product
        marked, last, sizes = "", 0, []
        for k, (size, (a, b)) in enumerate(spans):
            marked += t[last:a] + f" zzsize{k}zz "
            sizes.append(size)
            last = b
        marked += t[last:]
        marked = re.sub(r"(?<=[a-z])\s*/\s*(?=[a-z])", " or zzslashzz ", marked)   # Bread/Sandwich/Texas
        parts = [p for p in re.split(r"\s+or\s+|,|;|\s+\|\s+", marked) if p.strip()]
        final = []
        for p in parts:                             # 'wings 505 g and strips 550 g'
            sub = re.split(r"\s+and\s+", p)
            final.extend(sub if len(sub) > 1 and all("zzsize" in s for s in sub) else [p])
        branches: list[Branch] = []
        for p in final:
            own = [sizes[int(k)] for k in re.findall(r"zzsize(\d+)zz", p)]
            slash = "zzslashzz" in p
            text = re.sub(r"zzsize\d+zz|zzslashzz", " ", p)
            toks = self.norm.tokens(text)
            brands, rest = self._find_brands(toks)
            words = [w for w in self._drop(rest, self.variants) if not w.isdigit() and len(w) > 1]
            branches.append(Branch(brands=brands, words=words, all_tokens=toks, sizes=own,
                                   text=" ".join(text.split()), from_slash=slash))
        # a part with no product word (brand only, or variant only) belongs to the next product
        merged: list[Branch] = []
        carry: Branch | None = None
        for b in branches:
            if carry is not None:
                b.brands |= carry.brands
                b.all_tokens = carry.all_tokens + b.all_tokens
                b.sizes = carry.sizes + b.sizes
                b.text = (carry.text + " or " + b.text).strip()
                carry = None
            if not b.words:
                carry = b
            else:
                merged.append(b)
        if carry is not None:
            if merged:
                merged[-1].brands |= carry.brands
                merged[-1].all_tokens += carry.all_tokens
                merged[-1].sizes += carry.sizes
            else:
                merged.append(carry)
        branches = merged
        # brand carries forward: 'UNICO beans, tomatoes or tuna'
        for i in range(1, len(branches)):
            if not branches[i].brands:
                branches[i].brands = set(branches[i - 1].brands)
        # shared words: 'chicken strips, burgers or nuggets' / 'red, russet or yellow potatoes'
        if len(branches) > 1:
            first, lastb = branches[0], branches[-1]
            for i, b in enumerate(branches):
                ctx = set()
                if i > 0:   # 'Bread/Sandwich/Texas' = kinds of the same thing: the product word is shared
                    ctx |= set(first.words[:-1]) if (is_list and not b.from_slash) else set(first.words)
                if i < len(branches) - 1 and not is_list:
                    ctx |= set(lastb.words)
                b.context = ctx - set(b.words)
        every = [t for b in branches for t in b.all_tokens]
        for b in branches:
            b.offer_tokens = every
        # sizes
        joined = True                               # are the trailing sizes joined only by 'or'?
        extra = []
        if extra_sizes_text:
            et = self._strip_noise(clean(extra_sizes_text))
            espans = find_sizes(et)
            extra = [s for s, _ in espans]
            joined = self._or_joined(et, espans)
        if not is_list and len(branches) > 1 and branches[-1].sizes and not any(b.sizes for b in branches[:-1]):
            extra = branches[-1].sizes + extra      # 'A or B or C 400 g, 320 g, 454 g': sizes listed after the names
            joined = joined and self._or_joined(t, spans)
            branches[-1].sizes = []
        if is_list:
            for i, b in enumerate(branches):        # a size written once applies to the products before it
                if not b.sizes:
                    nxt = next((x.sizes for x in branches[i + 1:] if x.sizes), None)
                    prv = next((x.sizes for x in reversed(branches[:i]) if x.sizes), None)
                    b.sizes = list(nxt or prv or [])
        else:
            need = [b for b in branches if not b.sizes]
            if need and extra:
                if len(extra) == 1:
                    for b in need:
                        b.sizes = list(extra)
                elif joined:                        # '18 pk or 12 pk': every product is checked against every size
                    for b in need:
                        b.sizes = list(extra)
                        b.size_options = True
                        b.size_link_unclear = len(extra) != len(need) and len(branches) > 1
                elif len(extra) == len(need):       # '540 mL, 796 mL or 198 g': first size -> first product
                    for b, s in zip(need, extra):
                        b.sizes = [s]
                else:                               # cannot tell which size belongs to which product
                    for b in need:
                        b.sizes = list(extra)
                        b.size_link_unclear = len(branches) > 1
        return branches

    def read_list_item(self, row: int, raw: str) -> ListItem:
        item = ListItem(row=row, raw=raw)
        item.branches = [b for b in self.read(raw, is_list=True) if b.words or b.brands]
        toks = self.norm.tokens(raw)
        if "egg" in toks:
            for g in self.grades:
                if any(tuple(toks[i:i + len(g)]) == g for i in range(len(toks))):
                    item.grade = " ".join(g)
                    break
        for b in item.branches:
            if item.grade:
                b.words = [w for w in b.words if w not in item.grade.split()]
                b.context = {w for w in b.context if w not in item.grade.split()}
            # a brand that stands for a product: add the product when the text does not say it
            for br in b.brands:
                know = self._know(br)
                if know and know["product"] and not b.words:
                    b.words = list(know["product"])
                    b.words_from_brand = True
            if b.context and b.words:
                b.words = b.words + [w for w in b.context if w not in b.words]
        if not any(b.sizes for b in item.branches):
            item.flags.append("size unclear")
        return item

    def read_offer(self, offer: Offer) -> list[Branch]:
        title, desc = offer.title or "", offer.description or ""
        names = title[: len(title) - len(desc)] if desc and title.endswith(desc) else title
        if not names.strip():
            names = title or desc           # title missing: try the description
        return self.read(names, extra_sizes_text=desc if names != desc else None)

    # ---- comparing -------------------------------------------------------------------
    def _has(self, word: str, pool: set) -> bool:
        if word in pool:
            return True
        if len(word) >= 5:
            return any(len(t) >= 5 and fuzz.ratio(word, t) >= self.fuzzy for t in pool)
        return False

    def compare(self, item: ListItem, ib: Branch, ob: Branch, home: bool, per_weight: bool = False) -> tuple[str, list]:
        """-> ('match' | 'near' | 'no', notes)."""
        implied, also_ok = set(), set()
        for br in ob.brands:
            know = self._know(br)
            if know:
                implied |= set(know["product"])
                also_ok |= know["also_ok"]
        for br in ib.brands:
            know = self._know(br)
            if know:
                also_ok |= know["also_ok"] | set(know["product"])
        own = set(ob.words)
        pool = own | implied
        want = list(ib.words)
        brand_hit = self._brand_hit(ib.brands, ob.brands)

        if want and ib.words_from_brand and brand_hit:
            want = []                           # the flyer names the very brand the list names
        if want:
            missing = [w for w in want if not self._has(w, pool | ob.context)]
            if not any(self._has(w, pool) for w in want):
                return "no", []
            if missing:
                if len(missing) == 1 and (len(want) >= 3 or (len(want) == 2 and brand_hit)):
                    return "near", [f"list word not in flyer text: '{missing[0]}'"]
                return "no", []
        else:                                   # list names only a brand we know nothing about
            if not brand_hit:
                return "no", []
        # 'Ziploc Mega Packs': no real product word, only a describing word -> the brand IS the identity
        if want and all(w in self.modifiers for w in want) and not brand_hit:
            return "no", []
        if item.grade:
            g = tuple(item.grade.split())
            toks = ob.offer_tokens or ob.all_tokens
            if not any(tuple(toks[i:i + len(g)]) == g for i in range(len(toks))):
                return "near", [f"grade '{item.grade}' not stated in flyer"]
        extras = [w for w in ob.words if not self._has(w, set(want)) and w not in self.modifiers
                  and w not in also_ok and w not in implied]
        notes = []
        size_ok = True
        if ib.sizes and ob.sizes:
            size_ok = any_same_size(ib.sizes, ob.sizes, self.tol)
        else:
            notes.append("size unclear")
        if extras:
            return ("near", [f"flyer has extra product word(s): {', '.join(extras)}"]) if size_ok else ("no", [])
        # A per-lb / per-100 g offer with no pack size is not a fixed pack: it cannot be the
        # same size as a list item that names a pack size (weights are never converted to prices).
        if per_weight and not ob.sizes and ib.sizes and not all(s.each for s in ib.sizes):
            return "near", ["flyer price is per weight with no pack size; list names a pack size"]
        if not size_ok:
            return "near", [f"size differs: list {', '.join(map(str, ib.sizes))} / flyer {', '.join(map(str, ob.sizes))}"]
        if ib.brands and not self._brand_ok(ib.brands, ob.brands):
            if not ob.brands:
                return "near", ["brand not stated in flyer"]
            return "near", [f"different brand: list {', '.join(sorted(ib.brands))} / flyer {', '.join(sorted(ob.brands))}"]
        if ob.size_link_unclear:
            notes.append("OR offer - size link unclear")
        return "match", notes

    def find(self, item: ListItem, offers: list[tuple[Offer, list[Branch]]], home: bool,
             per_weight: set | None = None) -> tuple[list[Match], list[NearMiss]]:
        """per_weight = uids of offers priced only per lb / per 100 g."""
        matches, near = [], []
        per_weight = per_weight or set()
        for offer, branches in offers:
            best = None
            for ob in branches:
                for ib in item.branches:
                    verdict, notes = self.compare(item, ib, ob, home, offer.uid in per_weight)
                    if verdict == "match":
                        cand = Match(offer=offer, branch=ob, flags=list(notes),
                                     usable=not (ob.size_link_unclear and not ob.size_options))
                        if best is None or (cand.usable and not best.usable) or (len(cand.flags) < len(best.flags) and cand.usable >= best.usable):
                            best = cand
                    elif verdict == "near" and notes and best is None:
                        near.append(NearMiss(offer, notes[0]))
            if best is not None:
                matches.append(best)
                near = [n for n in near if n.offer is not offer]
        seen, uniq = set(), []
        for n in near:                              # one line per offer
            if n.offer.uid not in seen:
                seen.add(n.offer.uid)
                uniq.append(n)
        return matches, uniq
