# -*- coding: utf-8 -*-
"""Hard-condition screening, then soft preference comparison.

Order matters: hard conditions are checked first and produce pass/fail/unknown.
"Unknown" is never collapsed into "fail" — a posting that does not state its
salary or degree requirement is `needs_info`, not rejected.
"""

from __future__ import annotations

import re
from typing import Any

from . import salary as salary_mod
from .errors import ValidationError
from .util import squash, truncate

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_UNKNOWN = "unknown"

OVERALL_ELIGIBLE = "eligible"
OVERALL_CONDITIONAL = "conditional"      # hard conditions pass but some are unknown
OVERALL_INELIGIBLE = "ineligible"
OVERALL_INSUFFICIENT = "insufficient_info"

SCHEMA_PREFS = "jfa.preferences/1"
SCHEMA_SCREEN = "jfa.screening/1"

DEFAULT_WEIGHTS = {
    "title_match": 0.35,
    "skill_overlap": 0.35,
    "salary_fit": 0.20,
    "location_fit": 0.10,
}

_DEGREE_RANK = {
    "高中": 1, "中专": 1, "大专": 2, "专科": 2, "本科": 3, "学士": 3,
    "硕士": 4, "master": 4, "mba": 4, "博士": 5, "phd": 5, "ph.d": 5,
}

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+#.\-]{1,}")


def empty_prefs() -> dict:
    return {
        "schema": SCHEMA_PREFS,
        "hard": {
            "cities": [],
            "work_modes": [],
            "min_monthly_base": None,
            "target_annual": None,
            "target_monthly": None,
            "required_degree_max": "",
            "exclude_companies": [],
            "must_have_keywords": [],
            "exclude_keywords": [],
        },
        "soft": {
            "weights": dict(DEFAULT_WEIGHTS),
            "preferred_industries": [],
            "preferred_titles": [],
            "notes": "",
        },
        "open_questions": [],
    }


def validate_prefs(prefs: dict) -> list[dict]:
    issues: list[dict] = []
    if prefs.get("schema") != SCHEMA_PREFS:
        issues.append({"code": "schema", "severity": "warn",
                       "message": f"偏好 schema 建议为 {SCHEMA_PREFS}"})
    hard = prefs.get("hard") or {}
    if not isinstance(hard, dict):
        raise ValidationError("preferences.hard 必须是对象")
    for key in ("cities", "work_modes", "exclude_companies", "must_have_keywords", "exclude_keywords"):
        if hard.get(key) is not None and not isinstance(hard[key], list):
            issues.append({"code": f"bad_type:{key}", "severity": "block",
                           "message": f"hard.{key} 必须是数组"})
    if hard.get("target_annual") is None and hard.get("target_monthly") is None:
        issues.append({"code": "no_salary_target", "severity": "warn",
                       "message": "未提供薪资目标，只能展示岗位薪资、无法比较是否达标"})
    if not hard.get("cities"):
        issues.append({"code": "no_city", "severity": "warn",
                       "message": "未提供期望城市，城市条件将记为未知而不是不符合"})
    weights = (prefs.get("soft") or {}).get("weights") or {}
    if weights and abs(sum(float(v) for v in weights.values()) - 1.0) > 0.001:
        issues.append({"code": "weights_sum", "severity": "warn",
                       "message": "soft.weights 之和不为 1，评分将按归一化处理"})
    return issues


# ---------------------------------------------------------------------------
# hard conditions
# ---------------------------------------------------------------------------


def check_hard(job: dict, prefs: dict) -> list[dict]:
    hard = prefs.get("hard") or {}
    checks: list[dict] = []

    def add(cid: str, name: str, verdict: str, detail: str, evidence: Any = None):
        checks.append({
            "id": cid, "name": name, "verdict": verdict,
            "detail": detail, "evidence": evidence,
        })

    # ---- city
    cities = [squash(c) for c in hard.get("cities") or []]
    job_city = squash(job.get("sections", {}).get("city", "")) or squash(job.get("location", ""))
    if not cities:
        add("city", "城市", VERDICT_UNKNOWN, "用户未指定城市")
    elif not job_city:
        add("city", "城市", VERDICT_UNKNOWN, "岗位未读取到工作地点")
    else:
        hit = [c for c in cities if c and (c in job_city or job_city in c)]
        remote_ok = "远程" in (hard.get("work_modes") or []) and "远程" in job_city
        if hit or remote_ok:
            add("city", "城市", VERDICT_PASS, f"岗位地点「{job_city}」在期望城市内", job_city)
        else:
            add("city", "城市", VERDICT_FAIL, f"岗位地点「{job_city}」不在期望城市 {cities} 内", job_city)

    # ---- work mode
    modes = [squash(m) for m in hard.get("work_modes") or []]
    job_mode = squash(job.get("sections", {}).get("work_mode", "")) or "未说明"
    if not modes:
        add("work_mode", "工作方式", VERDICT_UNKNOWN, "用户未指定工作方式")
    elif job_mode == "未说明":
        add("work_mode", "工作方式", VERDICT_UNKNOWN, "岗位未说明工作方式，不按不符合处理")
    elif job_mode in modes:
        add("work_mode", "工作方式", VERDICT_PASS, f"岗位为「{job_mode}」", job_mode)
    else:
        add("work_mode", "工作方式", VERDICT_FAIL, f"岗位为「{job_mode}」，期望 {modes}", job_mode)

    # ---- salary floor (base pay only)
    min_base = hard.get("min_monthly_base")
    sal = salary_mod.from_dict(job.get("salary"))
    if not min_base:
        add("salary_floor", "薪资下限", VERDICT_UNKNOWN, "用户未指定月基薪下限")
    elif sal.kind in {"unknown", "negotiable"}:
        add("salary_floor", "薪资下限", VERDICT_UNKNOWN,
            "岗位未给出具体薪资，无法判断下限，不按不符合处理")
    else:
        guaranteed = sal.guaranteed_annual()
        need = float(min_base) * int(sal.months_per_year)
        if guaranteed is not None and guaranteed + 1e-6 >= need:
            add("salary_floor", "薪资下限", VERDICT_PASS,
                f"保证部分年化 {guaranteed:,.0f} ≥ 期望下限 {need:,.0f}",
                {"guaranteed_annual": guaranteed, "need_annual": need})
        else:
            add("salary_floor", "薪资下限", VERDICT_FAIL,
                f"保证部分年化 {guaranteed if guaranteed is None else f'{guaranteed:,.0f}'}"
                f" 低于期望下限 {need:,.0f}（按 {sal.months_per_year} 薪折算）",
                {"guaranteed_annual": guaranteed, "need_annual": need})

    # ---- degree
    max_degree = squash(hard.get("required_degree_max", "")).lower()
    if not max_degree:
        add("degree", "学历要求", VERDICT_UNKNOWN, "用户未设置学历上限条件")
    else:
        limit = _DEGREE_RANK.get(max_degree, 0)
        required, required_word = _job_degree(job)
        if not required_word:
            add("degree", "学历要求", VERDICT_UNKNOWN, "岗位未写明学历要求")
        elif required and required > limit:
            add("degree", "学历要求", VERDICT_FAIL,
                f"岗位要求「{required_word}」高于用户学历「{max_degree}」", required_word)
        else:
            add("degree", "学历要求", VERDICT_PASS,
                f"岗位要求「{required_word}」未超过用户学历", required_word)

    # ---- excluded companies
    excluded = [squash(c).lower() for c in hard.get("exclude_companies") or []]
    company = squash(job.get("company", "")).lower()
    if not excluded:
        add("exclude_company", "排除公司", VERDICT_UNKNOWN, "用户未设置排除公司")
    elif not company:
        add("exclude_company", "排除公司", VERDICT_UNKNOWN, "岗位未记录公司名")
    else:
        hit = [c for c in excluded if c and c in company]
        if hit:
            add("exclude_company", "排除公司", VERDICT_FAIL, f"公司命中排除列表：{hit}", hit)
        else:
            add("exclude_company", "排除公司", VERDICT_PASS, "公司不在排除列表", company)

    # ---- required keywords
    must = [squash(k).lower() for k in hard.get("must_have_keywords") or []]
    text = _job_text(job)
    if not must:
        add("must_keyword", "必备关键词", VERDICT_UNKNOWN, "用户未设置必备关键词")
    else:
        missing = [k for k in must if k and k not in text]
        if missing:
            add("must_keyword", "必备关键词", VERDICT_FAIL,
                f"岗位原文未出现：{missing}", missing)
        else:
            add("must_keyword", "必备关键词", VERDICT_PASS, "必备关键词全部出现", must)

    # ---- excluded keywords
    banned = [squash(k).lower() for k in hard.get("exclude_keywords") or []]
    if banned:
        hits = [k for k in banned if k and k in text]
        if hits:
            add("exclude_keyword", "排除关键词", VERDICT_FAIL, f"原文命中排除词：{hits}", hits)
        else:
            add("exclude_keyword", "排除关键词", VERDICT_PASS, "未命中排除关键词", banned)

    return checks


def _job_degree(job: dict) -> tuple[int, str]:
    chunks = list(job.get("sections", {}).get("education_experience") or [])
    chunks += list(job.get("sections", {}).get("must_have") or [])
    for chunk in chunks:
        low = squash(chunk).lower()
        for word, rank in sorted(_DEGREE_RANK.items(), key=lambda kv: -len(kv[0])):
            if word in low:
                return rank, word
    return 0, ""


def _job_text(job: dict) -> str:
    sections = job.get("sections") or {}
    parts: list[str] = [job.get("title", ""), job.get("company", "")]
    for key in ("responsibilities", "must_have", "nice_to_have", "education_experience"):
        parts.extend(sections.get(key) or [])
    if sections.get("salary_raw"):
        parts.append(sections["salary_raw"])
    return squash("\n".join(parts)).lower()


# ---------------------------------------------------------------------------
# soft preferences
# ---------------------------------------------------------------------------


def score_soft(job: dict, prefs: dict, *, facts_terms: set[str] | None = None) -> dict:
    soft = prefs.get("soft") or {}
    weights = dict(DEFAULT_WEIGHTS)
    weights.update({k: float(v) for k, v in (soft.get("weights") or {}).items()})
    total_w = sum(weights.values()) or 1.0

    components: dict[str, dict] = {}

    preferred_titles = soft.get("preferred_titles") or []
    title = squash(job.get("title", ""))
    if preferred_titles:
        best = max((_overlap(title, t) for t in preferred_titles), default=0.0)
        components["title_match"] = {
            "score": round(best, 3),
            "detail": f"职位名与期望方向重叠度 {best:.0%}",
        }
    else:
        components["title_match"] = {"score": 0.5, "detail": "未设置期望职位方向，取中性分"}

    if facts_terms:
        text = _job_text(job)
        job_terms = {t for t in _TOKEN_RE.findall(text) if len(t) >= 2}
        shared = job_terms & facts_terms
        score = min(len(shared) / max(len(job_terms), 1) * 2.5, 1.0)
        components["skill_overlap"] = {
            "score": round(score, 3),
            "detail": f"岗位术语与个人事实重叠 {len(shared)} 个：{truncate('、'.join(sorted(shared)) or '无', 60)}",
            "shared_terms": sorted(shared)[:20],
        }
    elif soft.get("preferred_industries"):
        text = _job_text(job)
        hit = [i for i in soft["preferred_industries"] if squash(i).lower() in text]
        components["skill_overlap"] = {
            "score": 1.0 if hit else 0.0,
            "detail": f"命中偏好行业 {hit}" if hit else "未命中偏好行业",
        }
    else:
        components["skill_overlap"] = {"score": 0.5, "detail": "缺少可比较的技能/行业信息"}

    target = salary_mod.merge_user_target(prefs.get("hard") or {})
    sal = salary_mod.from_dict(job.get("salary"))
    cmp = salary_mod.compare(target, sal)
    if target is None or sal.kind in {"unknown", "negotiable"}:
        components["salary_fit"] = {"score": 0.5, "detail": cmp["note"]}
    elif cmp["verdict"] == "meets_target_guaranteed":
        components["salary_fit"] = {"score": 1.0, "detail": cmp["note"]}
    elif cmp["verdict"] == "overlap_only":
        components["salary_fit"] = {"score": 0.55, "detail": cmp["note"]}
    else:
        components["salary_fit"] = {"score": 0.15, "detail": cmp["note"]}

    cities = [squash(c) for c in (prefs.get("hard") or {}).get("cities") or []]
    job_city = squash(job.get("sections", {}).get("city", "")) or squash(job.get("location", ""))
    if not cities or not job_city:
        components["location_fit"] = {"score": 0.5, "detail": "城市信息不足"}
    else:
        components["location_fit"] = {
            "score": 1.0 if any(c in job_city for c in cities) else 0.0,
            "detail": f"岗位地点 {job_city}",
        }

    weighted = sum(components[k]["score"] * weights.get(k, 0) for k in components) / total_w
    return {
        "score": round(weighted, 3),
        "components": components,
        "weights": weights,
        "note": "软偏好只用于排序比较，不改变硬条件结论。",
    }


def _overlap(a: str, b: str) -> float:
    ta = {t.lower() for t in _TOKEN_RE.findall(a)}
    tb = {t.lower() for t in _TOKEN_RE.findall(b)}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


def screen_job(job: dict, prefs: dict, *, facts_terms: set[str] | None = None) -> dict:
    checks = check_hard(job, prefs)
    fails = [c for c in checks if c["verdict"] == VERDICT_FAIL]
    unknowns = [c for c in checks if c["verdict"] == VERDICT_UNKNOWN]
    if fails:
        overall = OVERALL_INELIGIBLE
    elif unknowns:
        overall = OVERALL_CONDITIONAL if _has_decided(checks) else OVERALL_INSUFFICIENT
    else:
        overall = OVERALL_ELIGIBLE

    soft = score_soft(job, prefs, facts_terms=facts_terms)
    target = salary_mod.merge_user_target(prefs.get("hard") or {})
    salary_cmp = salary_mod.compare(target, salary_mod.from_dict(job.get("salary")))
    completeness = job.get("completeness") or {}

    return {
        "job_id": job.get("job_id", ""),
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "location": job.get("sections", {}).get("city") or job.get("location", ""),
        "overall": overall,
        "hard_checks": checks,
        "hard_failures": [c["id"] for c in fails],
        "hard_unknowns": [c["id"] for c in unknowns],
        "soft": soft,
        "salary": salary_cmp,
        "completeness": completeness,
        "caveats": _caveats(job, salary_cmp, completeness),
    }


def _has_decided(checks: list[dict]) -> bool:
    return any(c["verdict"] in {VERDICT_PASS, VERDICT_FAIL} for c in checks)


def _caveats(job: dict, salary_cmp: dict, completeness: dict) -> list[str]:
    notes: list[str] = []
    if salary_cmp["verdict"] == "overlap_only":
        notes.append(salary_mod.WARNING_OVERLAP)
    if not completeness.get("is_complete", True):
        notes.append("职位详情不完整（" + "、".join(completeness.get("missing", [])) + "），"
                     "不能声称已完成全面定制。")
    identity_status = (job.get("identity") or {}).get("status", "")
    if identity_status == "pending_review":
        notes.append("职位身份待核对：页头未匹配到公司/职位，结论仅供参考，导出会被阻止。")
    elif identity_status == "user_confirmed":
        notes.append("职位身份由用户确认，未经系统核对。")
    if job.get("possible_duplicates"):
        notes.append(f"与已记录岗位可能重复：{job['possible_duplicates']}")
    return notes


def screen_all(
    jobs: list[dict],
    prefs: dict,
    *,
    facts_terms: set[str] | None = None,
    limit: int | None = None,
) -> dict:
    results = [screen_job(j, prefs, facts_terms=facts_terms) for j in jobs]
    results.sort(key=lambda r: (
        {OVERALL_ELIGIBLE: 0, OVERALL_CONDITIONAL: 1, OVERALL_INSUFFICIENT: 2, OVERALL_INELIGIBLE: 3}[r["overall"]],
        -r["soft"]["score"],
    ))
    if limit:
        results = results[:limit]
    return {
        "schema": SCHEMA_SCREEN,
        "generated_at": _now(),
        "counts": _counts(results),
        "results": results,
        "policy": {
            "unknown_is_fail": False,
            "note": "未知信息不按不符合处理；硬条件先于软偏好；薪资区间相交不等于确定能获得目标薪资。",
        },
    }


def _counts(results: list[dict]) -> dict:
    counts = {OVERALL_ELIGIBLE: 0, OVERALL_CONDITIONAL: 0,
              OVERALL_INSUFFICIENT: 0, OVERALL_INELIGIBLE: 0}
    for r in results:
        counts[r["overall"]] = counts.get(r["overall"], 0) + 1
    return counts


def _now() -> str:
    from .util import now_iso

    return now_iso()
