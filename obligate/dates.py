"""Turn the way people actually say deadlines in meetings into real dates.

Handles: "today", "tomorrow", "EOD", "by Friday", "next Tuesday",
"end of the week", "end of month", "next week", "in 3 days", "in two weeks",
"Oct 30", "October 30th", "10/30", "2026-10-30".
"""
from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
         ("dec", "december")]
    )
    for m in names
}
NUMBER_WORDS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "ten": 10, "couple": 2, "few": 3}


def _next_weekday(ref: date, wd: int, force_next_week: bool = False) -> date:
    days = (wd - ref.weekday()) % 7
    if days == 0:
        days = 7
    if force_next_week and days < 7 and (ref + timedelta(days=days)).isocalendar()[1] == ref.isocalendar()[1]:
        days += 7
    return ref + timedelta(days=days)


def parse_due(text: str, ref: date) -> tuple[date | None, str | None]:
    """Return (due_date, the phrase that matched) or (None, None)."""
    t = text.lower()

    if re.search(r"\b(already (?:late|overdue|past due)|overdue|past due|was due)\b", t):
        return ref - timedelta(days=1), "already overdue"

    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3])), m[0]

    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t)
    if m:
        d = date(ref.year, MONTHS[m[1]], int(m[2]))
        if d < ref - timedelta(days=30):
            d = d.replace(year=ref.year + 1)
        return d, m[0]

    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", t)
    if m and 1 <= int(m[1]) <= 12 and 1 <= int(m[2]) <= 31:
        d = date(ref.year, int(m[1]), int(m[2]))
        if d < ref - timedelta(days=30):
            d = d.replace(year=ref.year + 1)
        return d, m[0]

    if re.search(r"\b(today|eod|end of (the )?day|tonight|this afternoon)\b", t):
        return ref, "today"
    if re.search(r"\btomorrow\b", t):
        return ref + timedelta(days=1), "tomorrow"

    m = re.search(r"\bin (\d+|" + "|".join(NUMBER_WORDS) + r") (day|days|week|weeks)\b", t)
    if m:
        n = int(m[1]) if m[1].isdigit() else NUMBER_WORDS[m[1]]
        mult = 7 if m[2].startswith("week") else 1
        return ref + timedelta(days=n * mult), m[0]

    m = re.search(r"\bnext (" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        return _next_weekday(ref, WEEKDAYS.index(m[1]), force_next_week=True), m[0]

    m = re.search(r"\b(?:by|on|before|this|until|due)?\s*(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        return _next_weekday(ref, WEEKDAYS.index(m[1])), m[0].strip()

    if re.search(r"\b(end of (the )?week|eow|this week)\b", t):
        return _next_weekday(ref, 4) if ref.weekday() != 4 else ref, "end of week"
    if re.search(r"\bnext week\b", t):
        return ref + timedelta(days=7 - ref.weekday() + 4), "next week"
    if re.search(r"\b(end of (the )?month|eom|this month)\b", t):
        nxt = (ref.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt - timedelta(days=1), "end of month"
    if re.search(r"\bnext month\b", t):
        nxt = (ref.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt + timedelta(days=14), "next month"
    if re.search(r"\b(asap|as soon as possible|right away|immediately)\b", t):
        return ref + timedelta(days=1), "asap"
    return None, None
