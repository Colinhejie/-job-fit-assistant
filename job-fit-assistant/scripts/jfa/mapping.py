# -*- coding: utf-8 -*-
"""Requirement -> personal-evidence mapping.

The host agent authors the mapping (it understands the posting); this module
enforces that every claim points at real facts and that a "direct match" is not
declared on top of an unconfirmed or absent fact.
"""

from __future__ import annotations

import re
from typing import Any

from . import facts as F
from .errors import ValidationError
from .util import now_iso, squash, truncate

SCHEMA_MAP = "jfa.requirement_map/1"

STATUS_DIRECT = "direct"
STATUS_TRANSFERABLE = "transferable"
STATUS_MISSING = "missing_evidence"
STATUS_MISMATCH = "clear_mismatch"
STATUS_VALUES = (STATUS_DIRECT, STATUS_TRANSFERABLE, STATUS_MISSING, STATUS_MISMATCH)

IMPORTANCE_MUST = "must"
IMPORTANCE_NICE = "nice"
IMPORTANCE_VALUES = (IMPORTANCE_MUST, IMPORTANCE_NICE)

STATUS_LABELS = {
    STATUS_DIRECT: "直接匹配",
    STATUS_TRANSFERABLE: "可迁移经验",
    STATUS_MISSING: "缺少证据",
    STATUS_MISMATCH: "明确不符合",
}

CREDIT = {
    STATUS_DIRECT: 1.0,
    STATUS_TRANSFERABLE: 0.6,
    STATUS_MISSING: 0.0,
    STATUS_MISMATCH: 0.0,
}

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+#.\-]{1,}")


def empty_map(job_id: str = "") -> dict:
    return {
        "schema": SCHEMA_MAP,
        "job_id": job_id,
        "generated_at": now_iso(),
        "requirements": [],
        "coverage": {},
        "gaps": [],
        "open_questions": [],
    }


def build_skeleton(job: dict, archive: dict | None = None) -> dict:
    """Seed requirements from the posting so the agent edits rather than invents.

    Every seeded requirement starts as `missing_evidence` — the honest default
    until the agent maps a fact onto it.
    """
    sections = job.get("sections") or {}
    mapping = empty_map(job.get("job_id", ""))
    n = 0
    for imp, key in ((IMPORTANCE_MUST, "must_have"), (IMPORTANCE_NICE, "nice_to_have")):
        for item in sections.get(key) or []:
            if not squash(item):
                continue
            n += 1
            mapping["requirements"].append({
                "requirement_id": f"R-{n:03d}",
                "requirement": squash(item),
                "importance": imp,
                "fact_ids": [],
                "status": STATUS_MISSING,
                "explanation": "",
                "source_section": key,
            })
    for item in sections.get("responsibilities") or []:
        if not squash(item):
            continue
        n += 1
        mapping["requirements"].append({
            "requirement_id": f"R-{n:03d}",
            "requirement": squash(item),
            "importance": IMPORTANCE_NICE,
            "fact_ids": [],
            "status": STATUS_MISSING,
            "explanation": "",
            "source_section": "responsibilities",
        })
    mapping["coverage"] = compute_coverage(mapping)
    return mapping


def validate_map(mapping: dict, archive: dict) -> list[dict]:
    """Returns issues. `severity: block` means the mapping cannot be used."""
    issues: list[dict] = []

    def add(code, severity, message, **extra):
        issues.append({"code": code, "severity": severity, "message": message, **extra})

    if mapping.get("schema") != SCHEMA_MAP:
        add("schema", "warn", f"mapping schema 建议为 {SCHEMA_MAP}")
    reqs = mapping.get("requirements")
    if not isinstance(reqs, list) or not reqs:
        add("empty", "block", "映射表为空：没有可展示的岗位要求与证据对应关系。")
        return issues

    index = F.fact_index(archive)
    usable = {fid for fid, f in index.items() if f.get("confirm_status") in F.USABLE}
    seen: set[str] = set()

    for req in reqs:
        rid = req.get("requirement_id", "")
        if not rid:
            add("missing_id", "block", "存在缺少 requirement_id 的要求")
            continue
        if rid in seen:
            add("duplicate_id", "block", f"requirement_id 重复：{rid}", requirement_id=rid)
        seen.add(rid)
        if not squash(req.get("requirement", "")):
            add("empty_requirement", "block", f"{rid} 要求文本为空", requirement_id=rid)
        if req.get("importance") not in IMPORTANCE_VALUES:
            add("bad_importance", "block",
                f"{rid} importance 必须是 {list(IMPORTANCE_VALUES)}", requirement_id=rid)
        status = req.get("status")
        if status not in STATUS_VALUES:
            add("bad_status", "block",
                f"{rid} status 必须是 {list(STATUS_VALUES)}", requirement_id=rid)
            continue
        fact_ids = req.get("fact_ids") or []
        if not isinstance(fact_ids, list):
            add("bad_fact_ids", "block", f"{rid} fact_ids 必须是数组", requirement_id=rid)
            fact_ids = []

        unknown = [f for f in fact_ids if f not in index]
        if unknown:
            add("unknown_fact", "block",
                f"{rid} 引用了不存在的事实：{unknown}", requirement_id=rid, fact_ids=unknown)
        unusable = [f for f in fact_ids if f in index and f not in usable]
        if unusable:
            add("unconfirmed_evidence", "warn",
                f"{rid} 引用了未确认/冲突事实 {unusable}；只能作为待确认线索，不能当直接匹配",
                requirement_id=rid, fact_ids=unusable)

        usable_refs = [f for f in fact_ids if f in usable]

        if status == STATUS_DIRECT:
            if not fact_ids:
                add("direct_without_evidence", "block",
                    f"{rid} 标记为直接匹配，但没有引用任何事实", requirement_id=rid)
            elif not usable_refs:
                add("evidence_unconfirmed", "block",
                    f"{rid} 标记为直接匹配，但引用的事实 {unusable} 都未经用户确认；"
                    "先确认事实，或降级为「缺少证据」", requirement_id=rid, fact_ids=unusable)
            elif not _lexical_support(req.get("requirement", ""), usable_refs, index):
                add("direct_without_lexical_support", "warn",
                    f"{rid} 标记为直接匹配，但要求措辞与所引事实几乎没有共同术语，请复核",
                    requirement_id=rid)
        elif status == STATUS_TRANSFERABLE:
            if not fact_ids:
                add("transferable_without_evidence", "block",
                    f"{rid} 标记为可迁移经验，但没有引用任何事实", requirement_id=rid)
            elif not usable_refs:
                add("evidence_unconfirmed", "block",
                    f"{rid} 标记为可迁移经验，但引用的事实 {unusable} 未经确认",
                    requirement_id=rid, fact_ids=unusable)
            if not squash(req.get("explanation", "")):
                add("transferable_without_explanation", "block",
                    f"{rid} 标记为可迁移经验，必须写明迁移逻辑（解释为空）", requirement_id=rid)
        elif status == STATUS_MISSING:
            if usable_refs:
                add("missing_but_has_evidence", "block",
                    f"{rid} 标记为缺少证据，却引用了已确认事实 {usable_refs}；"
                    "请改为直接匹配/可迁移，或移除这些事实引用",
                    requirement_id=rid)
        elif status == STATUS_MISMATCH:
            if not fact_ids:
                add("mismatch_without_evidence", "block",
                    f"{rid} 标记为明确不符合，但没有引用证明不符合的事实", requirement_id=rid)
            if not squash(req.get("explanation", "")):
                add("mismatch_without_explanation", "warn",
                    f"{rid} 标记为明确不符合但未解释原因", requirement_id=rid)

    must_ids = {r.get("requirement_id") for r in reqs if r.get("importance") == IMPORTANCE_MUST}
    missing_must = [r["requirement_id"] for r in reqs
                    if r.get("importance") == IMPORTANCE_MUST and r.get("status") == STATUS_MISSING]
    if must_ids and len(missing_must) == len(must_ids):
        add("no_must_coverage", "warn",
            "所有必备条件都缺少证据：该岗位很可能不是合适的目标，先向用户确认",
            requirement_ids=missing_must)
    return issues


def _lexical_support(requirement: str, fact_ids: list[str], index: dict) -> bool:
    req_terms = {t.lower() for t in _TOKEN_RE.findall(requirement)}
    if not req_terms:
        return True
    for fid in fact_ids:
        fact = index.get(fid) or {}
        text = squash(fact.get("content", "")) + " " + squash(
            str(fact.get("structured", "")))
        terms = {t.lower() for t in _TOKEN_RE.findall(text)}
        if req_terms & terms:
            return True
    return False


def compute_coverage(mapping: dict) -> dict:
    reqs = mapping.get("requirements") or []
    total = len(reqs)
    must = [r for r in reqs if r.get("importance") == IMPORTANCE_MUST]
    nice = [r for r in reqs if r.get("importance") == IMPORTANCE_NICE]

    def ratio(items):
        if not items:
            return None
        return round(sum(CREDIT.get(r.get("status"), 0.0) for r in items) / len(items), 3)

    must_ratio = ratio(must)
    nice_ratio = ratio(nice)
    if must_ratio is None and nice_ratio is None:
        score = 0.0
    elif must_ratio is None:
        score = nice_ratio or 0.0
    elif nice_ratio is None:
        score = must_ratio
    else:
        score = round(0.7 * must_ratio + 0.3 * nice_ratio, 3)

    by_status = {s: 0 for s in STATUS_VALUES}
    for r in reqs:
        if r.get("status") in by_status:
            by_status[r["status"]] += 1
    return {
        "total": total,
        "must_total": len(must),
        "nice_total": len(nice),
        "by_status": by_status,
        "must_ratio": must_ratio,
        "nice_ratio": nice_ratio,
        "score": score,
        "score_note": "must 权重 0.7 / nice 权重 0.3；直接匹配 1.0，可迁移 0.6，其余 0。",
    }


def gaps(mapping: dict) -> list[dict]:
    """Requirements with no evidence, must-have first."""
    out = []
    for req in mapping.get("requirements") or []:
        if req.get("status") in {STATUS_MISSING, STATUS_MISMATCH}:
            out.append({
                "requirement_id": req.get("requirement_id"),
                "requirement": truncate(req.get("requirement", ""), 120),
                "importance": req.get("importance"),
                "status": req.get("status"),
                "status_label": STATUS_LABELS.get(req.get("status"), ""),
                "explanation": req.get("explanation", ""),
            })
    out.sort(key=lambda g: 0 if g["importance"] == IMPORTANCE_MUST else 1)
    return out


def high_value_questions(mapping: dict, archive: dict, *, limit: int = 3) -> list[dict]:
    """Few, high-value questions — only where a gap could be closed by a fact.

    The spec asks for a small number of high-value questions, not an intake form:
    we only ask about must-have gaps, and only when the user plausibly has the
    experience but it is missing from the archive.
    """
    questions = []
    for gap in gaps(mapping):
        if len(questions) >= limit:
            break
        if gap["importance"] != IMPORTANCE_MUST:
            continue
        questions.append({
            "requirement_id": gap["requirement_id"],
            "question": _question_for(gap, archive),
            "why": "这是必备条件且档案中没有证据；补上一条真实经历就能显著提升匹配度。",
            "can_skip": True,
            "if_skipped": "按保守草稿处理，不把该要求写成已满足。",
        })
    return questions


def _question_for(gap: dict, archive: dict) -> str:
    req = gap["requirement"]
    return (
        f"岗位要求「{truncate(req, 60)}」，档案里没有对应证据。"
        "你是否有真实做过相关工作的经历？如果有，请说明：做过什么、你本人负责哪部分、"
        "用了什么方法、结果如何（有具体数字就写数字，没有就写清范围）。"
        "如果你没有，也可以直接说没有，我会按缺口如实标注。"
    )


def merge_agent_mapping(base: dict, agent_mapping: dict) -> dict:
    """Apply an agent-authored mapping onto the skeleton, keeping stable ids."""
    by_id = {r["requirement_id"]: r for r in base.get("requirements", [])}
    merged = dict(base)
    merged["requirements"] = []
    for req in agent_mapping.get("requirements") or []:
        rid = req.get("requirement_id")
        if rid and rid in by_id:
            row = dict(by_id[rid])
            row.update({k: v for k, v in req.items() if v is not None})
        else:
            row = {
                "requirement_id": rid or "",
                "requirement": req.get("requirement", ""),
                "importance": req.get("importance", IMPORTANCE_NICE),
                "fact_ids": req.get("fact_ids") or [],
                "status": req.get("status", STATUS_MISSING),
                "explanation": req.get("explanation", ""),
                "source_section": "agent_added",
            }
        merged["requirements"].append(row)
    merged["coverage"] = compute_coverage(merged)
    merged["gaps"] = gaps(merged)
    merged["open_questions"] = agent_mapping.get("open_questions") or high_value_questions(merged, {})
    merged["generated_at"] = now_iso()
    return merged


def require_valid(mapping: dict, archive: dict) -> dict:
    issues = validate_map(mapping, archive)
    blocking = [i for i in issues if i.get("severity") == "block"]
    if blocking:
        raise ValidationError(
            "映射表未通过校验，共 "
            f"{len(blocking)} 处阻断问题（如：直接匹配缺少已确认证据）。",
            hint="逐条修正后重新提交；不要通过降低标准让它通过。",
            detail={"issues": blocking[:20], "total": len(blocking)},
        )
    return {"issues": issues, "coverage": compute_coverage(mapping), "gaps": gaps(mapping)}
