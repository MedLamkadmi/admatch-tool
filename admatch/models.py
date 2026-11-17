from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import date


@dataclass
class Offer:
    """One flyer offer, exactly as extracted. Missing values stay None and are flagged."""
    uid: str
    source: str
    store: str
    flyer_id: str | None
    title: str | None
    description: str | None
    price_text: str | None
    image_url: str | None
    page: int | None            # page as shown in the Excel file
    page_raw: str | None        # page exactly as the source gave it
    valid_from: date | None
    valid_to: date | None
    category: str | None = None
    item_no: str | None = None
    source_url: str | None = None
    pdf_path: str | None = None
    pdf_status: str | None = None
    flags: list[str] = field(default_factory=list)

    def flag(self, text: str) -> None:
        if text not in self.flags:
            self.flags.append(text)

    def to_row(self) -> dict:
        d = asdict(self)
        d["flags"] = "; ".join(self.flags)
        return d
