# -*- coding: utf-8 -*-
"""Salary parsing and comparison.

Design constraints from the spec:

* fixed base pay, performance pay, commission and months-per-year are modelled
  separately — they are not one number;
* an overlap between "what the posting might pay" and "what the user wants" is
  reported as an overlap, never as "you will get your target".

All amounts are stored in CNY per month at the base-pay level, plus the annual
multiplier, so comparisons are explicit about what is guaranteed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_NUM = r"\d+(?:\.\d+)?"
_SUFFIX = r"(万|w|W|千|k|K)?"
_RANGE_RE = re.compile(
    rf"(?<!\d)({_NUM})\s*{_SUFFIX}\s*(?:-|~|～|—|–|至|到|\s)\s*({_NUM})\s*{_SUFFIX}"
)
_SINGLE_RE = re.compile(rf"(?<!\d)({_NUM})\s*{_SUFFIX}")
_MONTHS_RE = re.compile(r"(?:[·•*x×]\s*)?(\d{1,2})\s*薪")
_NEGOTIABLE = re.compile(r"面议|面谈|negotiable|competitive|待定", re.I)
_COMMISSION = re.compile(r"提成|佣金|commission", re.I)
_PERFORMANCE = re.compile(r"绩效|奖金|bonus|年终奖|分红", re.I)
_YEAR_HINT = re.compile(r"年薪|年包|年度|/年|每年|年收入|annually|per year|/yr|a year", re.I)
_MONTH_HINT = re.compile(r"月薪|/月|每月|元/月|月工资|per month|/mo", re.I)
_DAY_HINT = re.compile(r"日薪|/天|/日|每天|per day", re.I)

MULT = {"万": 10000, "w": 10000, "W": 10000, "千": 1000, "k": 1000, "K": 1000}


@dataclass
class Salary:
    """Parsed compensation, base-pay-centric."""

    kind: str = "unknown"           # unknown | negotiable | fixed | range
    currency: str = "CNY"
    period: str = "month"           # month | year | day
    min_monthly: float | None = None
    max_monthly: float | None = None
    months_per_year: int = 12
    months_conditional: bool = True
    has_commission: bool = False
    has_performance_pay: bool = False
    raw: str = ""
    notes: list[str] = field(default_factory=list)
    period_assumed: bool = False

    # ------------------------------------------------------------- derived
    def guaranteed_annual(self) -> float | None:
        """Floor of *base* pay only — commission/performance excluded."""
        if self.min_monthly is None:
            return None
        return self.min_monthly * self.months_per_year

    def possible_annual(self) -> float | None:
        if self.max_monthly is None:
            return self.guaranteed_annual()
        return self.max_monthly * self.months_per_year

    def to_dict(self) -> dict:
        data = asdict(self)
        data["guaranteed_annual"] = self.guaranteed_annual()
        data["possible_annual"] = self.possible_annual()
        return data


def parse_salary(text: str, *, annual_hint: bool = False) -> Salary:
    """Parse a posting's salary line. Never invents a number that is not there."""
    raw = (text or "").strip()
    if not raw:
        return Salary(kind="unknown", raw=raw, notes=["未提供薪资文本"])

    result = Salary(raw=raw)
    if _NEGOTIABLE.search(raw):
        result.kind = "negotiable"
        result.notes.append("岗位标注面议，无具体数字")
        _apply_extras(result, raw)
        return result

    m = _RANGE_RE.search(raw)
    if m:
        # "20-30K" states the unit once; apply it to both bounds
        suffix_lo = m.group(2) or ""
        suffix_hi = m.group(4) or ""
        if not suffix_lo and suffix_hi:
            suffix_lo = suffix_hi
        if not suffix_hi and suffix_lo:
            suffix_hi = suffix_lo
        lo = _to_number(m.group(1), suffix_lo)
        hi = _to_number(m.group(3), suffix_hi)
        result.kind = "range"
        result.min_monthly, result.max_monthly = lo, hi
    else:
        m = _SINGLE_RE.search(raw)
        if not m:
            result.notes.append("薪资文本中没有可解析的数字")
            return result
        value = _to_number(m.group(1), m.group(2))
        result.kind = "fixed"
        result.min_monthly = result.max_monthly = value
        result.currency = "CNY"

    period = _detect_period(raw, result.min_monthly or 0, annual_hint)
    result.period = period
    result.period_assumed = period == "month" and not _MONTH_HINT.search(raw)

    if period == "year":
        result.min_monthly = _div(result.min_monthly, 12)
        result.max_monthly = _div(result.max_monthly, 12)
        result.notes.append("原文为年薪，已折算为月基薪（÷12）")
    elif period == "day":
        result.min_monthly = _mul(result.min_monthly, 21.75)
        result.max_monthly = _mul(result.max_monthly, 21.75)
        result.notes.append("原文为日薪，按 21.75 个工作日/月折算")

    _apply_extras(result, raw)
    if result.min_monthly and result.max_monthly and result.min_monthly > result.max_monthly:
        result.min_monthly, result.max_monthly = result.max_monthly, result.min_monthly
        result.notes.append("上下限顺序异常，已交换")
    if result.min_monthly and result.min_monthly > 250000:
        result.notes.append("月基薪数值异常偏高，可能把年薪误当月薪，需人工确认")
    if result.period_assumed and result.min_monthly and result.min_monthly >= 100000:
        result.notes.append("未标注月/年，按常见月薪口径解析，需人工确认")
    return result


def _apply_extras(result: Salary, raw: str) -> None:
    m = _MONTHS_RE.search(raw)
    if m:
        n = int(m.group(1))
        if 12 <= n <= 24:
            result.months_per_year = n
            result.months_conditional = n > 12
            if n > 12:
                result.notes.append(f"第 13 个月起的 {n - 12} 薪通常与绩效挂钩，不保证到手")
    if _COMMISSION.search(raw):
        result.has_commission = True
        result.notes.append("含提成/佣金，比例与达成条件未知，不计入保证收入")
    if _PERFORMANCE.search(raw):
        result.has_performance_pay = True
        result.notes.append("含绩效/奖金，发放条件未知，不计入保证收入")


def _detect_period(raw: str, value: float, annual_hint: bool) -> str:
    if _YEAR_HINT.search(raw):
        return "year"
    if _MONTH_HINT.search(raw):
        return "month"
    if _DAY_HINT.search(raw):
        return "day"
    if annual_hint:
        return "year"
    # `30-50万` without a period marker: 万 + two-digit numbers is almost always
    # annual in Chinese postings, but say so instead of silently assuming.
    if "万" in raw and value <= 300:
        return "year"
    return "month"


def _to_number(num: str, suffix: str | None) -> float:
    value = float(num)
    if suffix:
        value *= MULT.get(suffix, 1)
    return value


def _div(value: float | None, n: float) -> float | None:
    return None if value is None else round(value / n, 2)


def _mul(value: float | None, n: float) -> float | None:
    return None if value is None else round(value * n, 2)


# ---------------------------------------------------------------------------
# comparison against the user's target
# ---------------------------------------------------------------------------

VERDICT_LABELS = {
    "unknown": "薪资信息不足",
    "no_data": "岗位未给薪资",
    "below_target": "上限低于目标",
    "overlap_only": "仅区间相交",
    "meets_target_guaranteed": "保证部分已达标",
    "negotiable": "面议，无法比较",
}

WARNING_OVERLAP = (
    "薪资区间与期望区间相交，只说明该岗位在理论上有谈到的可能，"
    "不等于你能拿到目标数字：实际报价取决于面试评级、岗位预算与议价，"
    "提成/绩效/13 薪以上通常附带条件。"
)


def compare(target_annual: float | None, salary: Salary) -> dict:
    """Compare a target *annual* figure with a parsed salary."""
    if salary.kind in {"unknown", "negotiable"}:
        verdict = "negotiable" if salary.kind == "negotiable" else "no_data"
        return {
            "verdict": verdict,
            "verdict_label": VERDICT_LABELS[verdict],
            "target_annual": target_annual,
            "guaranteed_annual": salary.guaranteed_annual(),
            "possible_annual": salary.possible_annual(),
            "overlap": None,
            "note": "岗位薪资信息不足，不能据此判断是否达标；也不按不符合处理。",
        }
    if target_annual is None:
        return {
            "verdict": "unknown",
            "verdict_label": VERDICT_LABELS["unknown"],
            "target_annual": None,
            "guaranteed_annual": salary.guaranteed_annual(),
            "possible_annual": salary.possible_annual(),
            "overlap": None,
            "note": "用户未给出薪资目标，仅展示岗位薪资构成。",
        }

    low = salary.guaranteed_annual()
    high = salary.possible_annual()
    if low is None or high is None:
        return {
            "verdict": "unknown",
            "verdict_label": VERDICT_LABELS["unknown"],
            "target_annual": target_annual,
            "guaranteed_annual": None,
            "possible_annual": None,
            "overlap": None,
            "note": "薪资数字不完整，无法比较。",
        }

    if high < target_annual:
        verdict = "below_target"
        note = f"即便按上限 {high:,.0f} 元/年 计算也低于目标 {target_annual:,.0f} 元/年。"
    elif low >= target_annual:
        verdict = "meets_target_guaranteed"
        note = (
            f"按保证部分下限 {low:,.0f} 元/年 已覆盖目标 {target_annual:,.0f} 元/年。"
            + ("（未计入提成/绩效）" if salary.has_commission or salary.has_performance_pay else "")
        )
    else:
        verdict = "overlap_only"
        note = WARNING_OVERLAP

    overlap_low = max(low, target_annual)
    overlap_high = min(high, target_annual)
    overlap = {
        "annual_low": overlap_low,
        "annual_high": overlap_high,
        "monthly_low": round(overlap_low / 12, 2),
        "monthly_high": round(overlap_high / 12, 2),
        "months_per_year": salary.months_per_year,
    }
    return {
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS[verdict],
        "target_annual": target_annual,
        "guaranteed_annual": low,
        "possible_annual": high,
        "overlap": overlap,
        "note": note,
    }


def format_amount(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 10000:
        wan = value / 10000
        text = f"{wan:.1f}".rstrip("0").rstrip(".")
        return f"{text} 万"
    return f"{value:,.0f}"


def render_salary(salary: Salary) -> str:
    """One-line human summary used in reports."""
    if salary.kind == "unknown":
        return "未提供"
    if salary.kind == "negotiable":
        return "面议"
    if salary.min_monthly == salary.max_monthly:
        base = f"月基薪 {format_amount(salary.min_monthly)}"
    else:
        base = f"月基薪 {format_amount(salary.min_monthly)}–{format_amount(salary.max_monthly)}"
    extra = []
    if salary.months_per_year != 12:
        extra.append(f"{salary.months_per_year} 薪")
    if salary.has_commission:
        extra.append("含提成")
    if salary.has_performance_pay:
        extra.append("含绩效")
    tail = f"（{'、'.join(extra)}）" if extra else ""
    return base + tail


def from_dict(data: dict | None) -> Salary:
    if not data:
        return Salary()
    allowed = {f for f in Salary.__dataclass_fields__}  # type: ignore[attr-defined]
    return Salary(**{k: v for k, v in data.items() if k in allowed})


def merge_user_target(prefs: dict) -> float | None:
    """Target annual, derived from whatever the user actually stated."""
    if not prefs:
        return None
    if prefs.get("target_annual"):
        return float(prefs["target_annual"])
    if prefs.get("target_monthly"):
        months = int(prefs.get("target_months_per_year") or 12)
        return float(prefs["target_monthly"]) * months
    return None
