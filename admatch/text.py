"""Text normalisation (spec §6): strip ®/™, unify dashes, remove accents, ignore case,
expand short forms, translate French words, unify synonyms."""
from __future__ import annotations
import re
import unicodedata

from .config import Config

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def clean(s: str | None) -> str:
    """Lower-case, no accents, no ®/™, one kind of dash and quote."""
    if not s:
        return ""
    s = str(s).replace("œ", "oe").replace("Œ", "OE")
    s = re.sub(r"[®™©]", "", s)
    s = s.replace("–", "-").replace("—", "-").replace("‑", "-").replace("−", "-")
    s = s.replace("’", "'").replace("‘", "'").replace("`", "'").replace("”", '"').replace("“", '"')
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def singular(w: str) -> str:
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("oes"):
        return w[:-2]
    if w.endswith(("ches", "shes", "xes", "sses")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


class Normalizer:
    def __init__(self, cfg: Config):
        ab = cfg.table("abbreviations.yaml").get("words", {})
        fr = cfg.table("french_english.yaml")
        self.abbrev = {clean(k): clean(v) for k, v in ab.items()}
        self.french = {clean(k): clean(v) for k, v in fr.get("words", {}).items()}
        self.phrases: list[tuple[str, str]] = []
        for k, v in fr.get("phrases", {}).items():
            self._add_phrase(k, v)
        for group in cfg.table("matching.yaml").get("synonyms", []):
            for alt in group[1:]:
                self._add_phrase(alt, group[0])
        for brand, info in cfg.table("product_knowledge.yaml").get("brands", {}).items():
            for alt in (info or {}).get("same_as", []) or []:
                self._add_phrase(alt, brand)
        for brand, info in cfg.table("store_brands.yaml").get("brands", {}).items():
            for alt in info.get("aliases", []):
                if clean(alt) not in self.abbrev:
                    self._add_phrase(alt, brand)
        uniq = {}
        for a, b in self.phrases:
            uniq.setdefault(tuple(a.split()), b.split())
        self.phrases = sorted(((list(a), b) for a, b in uniq.items()), key=lambda p: -len(p[0]))

    def _basic(self, s: str) -> list[str]:
        s = clean(s).replace("!", "i" if "s!gnal" in clean(s) else "")
        out = []
        for i, t in enumerate(_TOKEN.findall(s)):
            t = t.replace("'", "")
            out.append(t)
        return out

    def _add_phrase(self, src: str, dst: str) -> None:
        a = " ".join(singular(t) for t in self._basic(src))
        b = " ".join(singular(t) for t in self._basic(dst))
        if a and b:                       # the full name maps to itself so it is never expanded twice
            self.phrases.append((a, b))
            self.phrases.append((b, b))

    def tokens(self, s: str | None) -> list[str]:
        raw = self._basic(s or "")
        out: list[str] = []
        for i, t in enumerate(raw):
            if t in self.abbrev and not (t == "pc" and i > 0 and raw[i - 1].isdigit()):
                out.extend(self.abbrev[t].replace("'", "").split())
            elif t in self.french:
                out.extend(self.french[t].split())
            else:
                out.append(t)
        toks = [singular(t) for t in out]
        res: list[str] = []
        i = 0
        while i < len(toks):                # longest phrase first, replaced text is not scanned again
            for a, b in self.phrases:
                if toks[i:i + len(a)] == a:
                    res.extend(b)
                    i += len(a)
                    break
            else:
                res.append(toks[i])
                i += 1
        return res

    def phrase(self, s: str) -> tuple[str, ...]:
        return tuple(self.tokens(s))
