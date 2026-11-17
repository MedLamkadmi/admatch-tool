"""Week rule (spec §3): ONLY the coming flyer week, filtered by flyer START date."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass(frozen=True)
class FlyerWeek:
    start: date
    end: date

    @property
    def number(self) -> int:
        """Team week number = ISO week of the start day (Thu Nov 12 2026 -> week 46)."""
        return self.start.isocalendar()[1]

    def contains(self, d: date | None) -> bool:
        return d is not None and self.start <= d <= self.end

    def __str__(self) -> str:
        return f"week {self.number} ({self.start:%a %b %d} - {self.end:%a %b %d %Y})"


def target_week(run_date: date, week_cfg: dict) -> FlyerWeek:
    """The coming flyer week for a given run date. Never a past week."""
    start_wd = WEEKDAYS.index(week_cfg["start_weekday"].lower())
    ahead = (start_wd - run_date.weekday()) % 7
    if ahead == 0 and week_cfg.get("run_on_start_day_takes", "same_day") != "same_day":
        ahead = 7
    start = run_date + timedelta(days=ahead)
    return FlyerWeek(start, start + timedelta(days=int(week_cfg["length_days"]) - 1))
