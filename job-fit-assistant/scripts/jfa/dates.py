# -*- coding: utf-8 -*-
"""Date parsing shared by resume structure parsing, facts and job screening.

Never guesses. `parse_month` returns what the string actually states; an
unparseable range stays unstructured and is surfaced for confirmation instead of
being coerced into a plausible date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ISO = re.compile(r"(?<!\d)((?:19|20)\d{2})[-./年](\d{1,2})(?:[-./月](\d{1,2})\s*日?)?")
_YEAR_ONLY = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_EN = re.compile(
    r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\.?\s+((?:19|20)\d{2})\b"
)
_PRESENT = re.compile(r"(至今|现在|目前|今|present|now|current|today)", re.I)

_FULL_RANGE = re.compile(
    r"(?P<start>[^\s~～至—–\-]{4,20})\s*(?:~|～|-|—|–|至|到)\s*(?P<end>至今|现在|目前|present|now|[^\s,，;；]{4,20})",
    re.I,
)


@dataclass(frozen=True)
class YearMonth:
    year: int
    month: int | None = None

    def months_index(self) -> int:
        return self.year * 12 + (self.month or 1) - 1

    def iso(self) -> str:
        return f"{self.year:04d}-{self.month:02d}" if self.month else f"{self.year:04d}"

    def __str__(self) -> str:  # pragma: no cover
        return self.iso()


def parse_month(text: str) -> YearMonth | None:
    """First date-like token in `text`, or None. Month may be absent."""
    if not text:
        return None
    m = _ISO.search(text)
    if m:
        month = int(m.group(2))
        if 1 <= month <= 12:
            return YearMonth(int(m.group(1)), month)
    m = _EN.search(text)
    if m:
        return YearMonth(int(m.group(2)), _MONTH_NAMES[m.group(1).lower().rstrip(".")])
    m = _YEAR_ONLY.search(text)
    if m:
        return YearMonth(int(m.group(1)), None)
    return None


def is_present(text: str) -> bool:
    return bool(_PRESENT.search(text or ""))


def parse_range(text: str) -> tuple[YearMonth | None, YearMonth | None, bool]:
    """Return (start, end, ongoing). `end` is None when ongoing or unparseable."""
    if not text:
        return None, None, False
    m = _FULL_RANGE.search(text)
    if not m:
        single = parse_month(text)
        return single, None, is_present(text)
    start = parse_month(m.group("start"))
    raw_end = m.group("end")
    ongoing = is_present(raw_end)
    end = None if ongoing else parse_month(raw_end)
    return start, end, ongoing


def month_span(
    start: YearMonth | None,
    end: YearMonth | None,
    ongoing: bool,
    *,
    today: YearMonth | None = None,
) -> int:
    """Inclusive month count; 0 when the range is not usable.

    Inclusive (Mar 2021 – Jun 2023 = 28 months) because that is how Chinese
    resumes state tenure; the same rule is used by the tenure union so the
    number the user sees never depends on which code path computed it.
    """
    if start is None:
        return 0
    if ongoing:
        if today is None:
            from .util import now

            n = now()
            today = YearMonth(n.year, n.month)
        end = today
    if end is None:
        return 0
    delta = end.months_index() - start.months_index()
    return max(delta, 0) + 1


def year_month_now() -> YearMonth:
    from .util import now

    n = now()
    return YearMonth(n.year, n.month)


#: one date: 2021.03 / 2021-03 / 2021/03 / 2021年3月 / 2021.03.15 / 2021年3月15日
_DATE_ONE = r"(?:19|20)\d{2}\s*[./年\-]\s*\d{1,2}(?:[./月\-]\s*\d{1,2}\s*日?)?"

#: a date range, or a single date, including "至今"
_DATE_SPAN = re.compile(
    rf"{_DATE_ONE}\s*(?:~|～|-|—|–|至|到)\s*"
    rf"(?:{_DATE_ONE}|至今|现在|目前|present|now)"
    rf"|{_DATE_ONE}"
    rf"|(?:19|20)\d{{2}}\s*(?:-|–|~|至)\s*(?:至今|现在|目前|present|now)",
    re.I,
)


def extract_date_span(text: str) -> str:
    """The date substring itself, not the whole line it sits on."""
    m = _DATE_SPAN.search(text or "")
    return m.group(0).strip() if m else ""


def format_span(months: int) -> str:
    if months <= 0:
        return "0 个月"
    years, rem = divmod(months, 12)
    if years and rem:
        return f"{years} 年 {rem} 个月"
    if years:
        return f"{years} 年"
    return f"{rem} 个月"
