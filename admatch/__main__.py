"""Command line:  python -m admatch --list LIST.xlsx --raddar RADDAR.xlsx [--run-date 2026-11-10]"""
from __future__ import annotations
import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .config import Config
from .excel import write_excel
from .pipeline import Run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="admatch", description="Weekly ad-match file. No LLM: rules, fuzzy matching, local OCR.")
    ap.add_argument("--list", required=True, help="the fixed list file (.xlsx or .csv)")
    ap.add_argument("--raddar", required=True, help="this week's Raddar export (.xlsx, .csv or .json)")
    ap.add_argument("--run-date", help="YYYY-MM-DD. Default: today. The tool takes the flyer week that starts on the next Thursday.")
    ap.add_argument("--out", default="output", help="output folder")
    ap.add_argument("--config", help="config folder (default: the config folder next to the code)")
    ap.add_argument("--no-images", action="store_true", help="do not download images (pictures and image prices are skipped)")
    a = ap.parse_args(argv)

    run_date = datetime.strptime(a.run_date, "%Y-%m-%d").date() if a.run_date else date.today()
    cfg = Config(a.config)
    from .week import target_week
    week = target_week(run_date, cfg.settings["week"])
    out = Path(a.out) / f"week_{week.number}_{week.start:%Y-%m-%d}"
    run = Run(cfg, out, use_images=False if a.no_images else None)
    result = run.run(a.list, a.raddar, run_date)
    xlsx = write_excel(result, out / f"ad_match_week_{week.number}.xlsx")

    print("\n".join(result.report.lines()))
    counts: dict = {}
    for r in result.rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"List items: {len(result.rows)}  " + "  ".join(f"{k}: {v}" for k, v in counts.items()))
    for w in result.warnings:
        if w not in result.report.warnings:
            print(w)
    print(f"Excel file: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
