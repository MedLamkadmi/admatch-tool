"""Excel output (spec §9): one file, 12 columns in a fixed order, pictures embedded in the
file (never links), every list item present, rows in master-list order."""
from __future__ import annotations
import io
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side as XLSide
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from PIL import Image as PILImage

from .pipeline import RunResult, Row, Side

COLUMNS = [
    ("List item", 34), ("Status", 20), ("Price difference", 12), ("Competitor store", 14),
    ("Competitor price", 27), ("Competitor product (as written in flyer)", 46), ("Competitor image", 19),
    ("Competitor flyer page", 11), ("Superstore price", 27), ("Superstore product (as written in flyer)", 46),
    ("Superstore image", 19), ("Superstore flyer page", 11),
]
STATUS_FILL = {"HIGH": "F8CBAD", "NOT IN SUPERSTORE": "FFE699", "NOT IN COMPETITOR": "DDEBF7",
               "EQUAL": "E2EFDA", "LOW": "E2EFDA", "EMPTY": "EDEDED"}
IMG_W, IMG_H = 120, 96          # picture box in pixels
ROW_PT = 78                     # row height in points (about 104 px)
THIN = XLSide(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _money_format(note: str) -> str:
    """Number stays a number; the wording (2 for $7.00, from, /lb) is shown beside it."""
    if not note:
        return '"$"#,##0.00'
    safe = note.replace('"', "'").replace("\\", "/").replace(";", ",")[:90]   # ';' would split the format
    return f'"$"#,##0.00"  ({safe})"'


def _picture(path: Path) -> tuple[XLImage, int, int]:
    """Picture stored INSIDE the workbook, scaled to the picture box."""
    with PILImage.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((IMG_W * 2, IMG_H * 2))        # 2x for sharpness, small file
        w, h = im.size
        buf = io.BytesIO()
        im.save(buf, "PNG")
    buf.seek(0)
    scale = min(IMG_W / w, IMG_H / h)
    img = XLImage(buf)
    img.width, img.height = int(w * scale), int(h * scale)
    return img, img.width, img.height


def _place(ws, img: XLImage, w: int, h: int, row: int, col: int) -> None:
    """Anchor the picture inside one cell so it moves and hides with its row."""
    pad = 4
    a = AnchorMarker(col=col - 1, colOff=pixels_to_EMU(pad), row=row - 1, rowOff=pixels_to_EMU(pad))
    b = AnchorMarker(col=col - 1, colOff=pixels_to_EMU(pad + w), row=row - 1, rowOff=pixels_to_EMU(pad + h))
    img.anchor = TwoCellAnchor(editAs="twoCell", _from=a, to=b)
    ws.add_image(img)


def _side_cells(ws, r: int, first_col: int, side: Side, with_store: bool) -> None:
    c = first_col
    if with_store:
        ws.cell(r, c, side.offer.store if side.offer else None)
        c += 1
    price_cell = ws.cell(r, c, float(side.value) if side.value is not None else None)
    if side.value is not None:
        price_cell.number_format = _money_format(side.price.note())
    ws.cell(r, c + 1, side.offer.title if side.offer else None)
    img_cell = ws.cell(r, c + 2)
    if side.offer is not None:
        if side.image is not None:
            try:
                img, w, h = _picture(side.image)
                _place(ws, img, w, h, r, c + 2)
            except Exception:
                img_cell.value = "no image (file unreadable)"
        else:
            img_cell.value = side.image_note or "no image"
    ws.cell(r, c + 3, side.offer.page if side.offer else None)
    if side.flags and side.offer is not None:
        price_cell.fill = PatternFill("solid", start_color="FFFF00")
        price_cell.comment = Comment("Flags: " + "; ".join(dict.fromkeys(side.flags)), "Ad-Match tool", width=320, height=110)


def write_excel(result: RunResult, path: Path | str) -> Path:
    path = Path(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Ad Match"
    r = 1
    for w in result.warnings:                       # the file must never look complete when it is not
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(COLUMNS))
        cell = ws.cell(r, 1, w)
        cell.font = Font(bold=True, color="FFFFFF", size=12)
        cell.fill = PatternFill("solid", start_color="C00000")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = 34
        r += 1
    header_row = r
    for i, (name, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(header_row, i, name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", start_color="1F3864")
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[header_row].height = 36
    ws.freeze_panes = ws.cell(header_row + 1, 2)

    for row in result.rows:
        r += 1
        ws.row_dimensions[r].height = ROW_PT
        ws.cell(r, 1, row.item).font = Font(bold=True)
        st = ws.cell(r, 2, row.status)
        st.fill = PatternFill("solid", start_color=STATUS_FILL[row.status])
        st.font = Font(bold=True)
        d = ws.cell(r, 3, float(row.diff) if row.diff is not None else None)
        d.number_format = '"$"#,##0.00;[Red]-"$"#,##0.00'
        _side_cells(ws, r, 4, row.comp, with_store=True)
        _side_cells(ws, r, 9, row.home, with_store=False)
        if row.flags:
            ws.cell(r, 1).comment = Comment("Flags: " + "; ".join(row.flags), "Ad-Match tool", width=260, height=70)
        for c in range(1, len(COLUMNS) + 1):
            cell = ws.cell(r, c)
            cell.border = BORDER
            if c in (5, 9):         # a number with wording beside it: shrink instead of showing ####
                cell.alignment = Alignment(shrink_to_fit=True, vertical="center", horizontal="left")
            else:
                cell.alignment = Alignment(wrap_text=True, vertical="center",
                                           horizontal="center" if c in (2, 3, 4, 7, 8, 11, 12) else "left")
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(COLUMNS))}{r}"

    _flags_sheet(wb, result)
    _review_sheet(wb, result)
    _info_sheet(wb, result)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _head(ws, names: list, widths: list) -> None:
    for i, (n, w) in enumerate(zip(names, widths), start=1):
        c = ws.cell(1, i, n)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color="1F3864")
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def _flags_sheet(wb, result: RunResult) -> None:
    ws = wb.create_sheet("Flags")
    _head(ws, ["List item", "Status", "Side", "Store", "Flag", "Flyer text", "Price text in flyer"], [36, 20, 13, 14, 60, 60, 30])
    for row in result.rows:
        for f in row.flags:
            ws.append([row.item, row.status, "List", None, f, None, None])
        for name, side in (("Competitor", row.comp), ("Superstore", row.home)):
            if side.offer is None:
                continue
            for f in dict.fromkeys(side.flags):
                ws.append([row.item, row.status, name, side.offer.store, f, side.offer.title, side.offer.price_text])
            if side.image is None and side.image_note:
                ws.append([row.item, row.status, name, side.offer.store, side.image_note, side.offer.title, side.offer.price_text])


def _review_sheet(wb, result: RunResult) -> None:
    ws = wb.create_sheet("Review")
    _head(ws, ["List item", "Side", "Store", "Flyer text", "Price", "Why it is listed here"], [36, 13, 14, 70, 22, 70])
    for row in result.rows:
        for side, store, text, price, reason in row.review:
            ws.append([row.item, side, store, text, float(price.comparison) if price.comparison is not None else None, reason])
            if price.comparison is not None:
                ws.cell(ws.max_row, 5).number_format = _money_format(price.note())


def _info_sheet(wb, result: RunResult) -> None:
    ws = wb.create_sheet("Run info")
    ws.column_dimensions["A"].width = 120
    for line in result.report.lines():
        ws.append([line])
        if line.startswith("!!!"):
            ws.cell(ws.max_row, 1).font = Font(bold=True, color="C00000")
    for w in result.warnings:
        if w not in result.report.warnings:
            ws.append([w])
            ws.cell(ws.max_row, 1).font = Font(bold=True, color="C00000")
    counts: dict = {}
    for row in result.rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    ws.append([])
    ws.append([f"List items: {len(result.rows)}   " + "   ".join(f"{k}: {v}" for k, v in counts.items())])
