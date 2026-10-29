# -*- coding: utf-8 -*-
"""Change-plan validation and resume document assembly.

Two jobs, in this order:

1. `validate_plan` — refuses inflation. Escalated verbs, invented numbers,
   team results presented as personal, skill-level upgrades, swapped companies
   and unconfirmed facts used as facts are all hard blocks.
2. `build_resume_doc` — assembles the document *from the fact archive*, taking
   only prose from the plan. Company, title, dates, degree and name are copied
   from confirmed facts and cannot be supplied by the rewrite at all.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from . import facts as F
from .errors import ValidationError
from .mapping import STATUS_LABELS
from .util import now_iso, squash, truncate

SCHEMA_PLAN = "jfa.change_plan/1"
SCHEMA_DOC = "jfa.resume_doc/1"

VS_VERIFIED = "verified"
VS_PENDING = "pending_confirmation"
VS_VALUES = (VS_VERIFIED, VS_PENDING)

#: escalation ladder — (weaker verb, stronger verb). Fires only when the
#: stronger verb has *no* basis in the referenced facts.
VERB_LADDER: list[tuple[str, str]] = [
    ("协助", "主导"), ("协助", "负责"), ("协助", "统筹"), ("协助", "独立"),
    ("参与", "主导"), ("参与", "负责"), ("参与", "统筹"),
    ("配合", "主导"), ("支持", "主导"), ("跟进", "主导"),
    ("负责", "主导"), ("执行", "主导"), ("承担", "主导"),
    ("assist", "led"), ("assisted", "led"), ("helped", "led"),
    ("participated", "led"), ("supported", "led"), ("contributed", "led"),
    ("member", "owner"), ("involved", "owned"),
]

#: skill-level ladder — same rule: no basis in facts, no upgrade.
LEVEL_LADDER: list[tuple[str, str]] = [
    ("学习过", "熟练"), ("学习过", "精通"), ("学习过", "掌握"),
    ("了解", "熟练"), ("了解", "精通"), ("了解", "掌握"),
    ("熟悉", "精通"), ("接触过", "熟练"), ("接触过", "精通"),
    ("会用", "精通"), ("beginner", "proficient"), ("exposure", "expert"),
    ("familiar", "expert"), ("studied", "proficient"), ("aware", "expert"),
]

TEAM_WORDS = ("团队", "小组", "协作", "合作", "共同", "参与", "配合", "跨部门", "team", "cross-functional")
SOLO_WORDS = ("独立", "个人主导", "独自", "全程负责", "我主导", "我负责", "solo", "independently", "single-handedly")

ABSOLUTE_WORDS = ("所有", "全部", "唯一", "首个", "零失误", "百分之百", "100%", "全网第一", "行业第一",
                  "always", "never", "only", "all of")

STRONG_LEVEL_WORDS = ("精通", "熟练", "掌握", "proficient", "expert", "advanced", "mastery")
WEAK_LEVEL_WORDS = ("熟悉", "了解", "familiar", "aware")

#: 弱化词一旦在改写里消失，就等于把"协助"抬成了"本人负责"。
#: 这是最容易漏的一种注水：动词没升级，但限定词被删掉了。
HEDGE_WORDS = ("协助", "参与", "配合", "支持", "帮助", "辅助", "assist", "assisted",
               "supported", "helped", "participated")
ACTION_WORDS = ("搭建", "开发", "设计", "重构", "上线", "负责", "主导", "完成", "推进",
                "落地", "搭建", "建立", "优化")

DEGREE_WORDS = ("博士", "硕士", "本科", "学士", "大专", "专科", "MBA", "EMBA",
                "Ph.D", "PhD", "Master", "Bachelor")

NUM_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(%|万|亿|千|k|K|个月|年|人|次|倍|w|W)?")
LATIN_TERM = re.compile(r"\b([A-Za-z][A-Za-z0-9+#.\-]{2,})\b")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def empty_plan(job_id: str = "") -> dict:
    return {
        "schema": SCHEMA_PLAN,
        "job_id": job_id,
        "generated_at": now_iso(),
        "target_title": {"text": "", "requirement_ids": [], "fact_ids": [], "change_reason": ""},
        "summary": {"before": "", "after": "", "change_reason": "",
                    "requirement_ids": [], "fact_ids": [], "verification_status": VS_PENDING},
        "skills": [],
        "experiences": [],
        "compressions": [],
        "open_questions": [],
        "notes": [],
    }


def _numbers(text: str) -> set[str]:
    return {m.group(1) for m in NUM_RE.finditer(text or "")}


def _fact_text(fact: dict) -> str:
    structured = fact.get("structured") or {}
    flat = " ".join(f"{k} {v}" for k, v in structured.items() if isinstance(v, (str, int, float)))
    return f"{fact.get('content', '')} {fact.get('label', '')} {flat}"


def _fact_terms(facts: list[dict]) -> set[str]:
    text = " ".join(_fact_text(f) for f in facts)
    return {t.lower() for t in LATIN_TERM.findall(text)}


_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")


def _cjk_ngrams(text: str, n: int = 3) -> set[str]:
    """Character n-grams over CJK runs; used to spot invented Chinese wording."""
    grams: set[str] = set()
    for run in _CJK_RUN.findall(text or ""):
        for i in range(len(run) - n + 1):
            grams.add(run[i:i + n])
    return grams


def flatten_changes(plan: dict) -> list[dict]:
    """One flat list for the before/after diff report."""
    out: list[dict] = []
    n = 0

    def add(change: dict, section: str, location: str) -> None:
        nonlocal n
        n += 1
        out.append({
            "change_id": f"C-{n:03d}",
            "section": section,
            "location": location,
            "before": change.get("before", ""),
            "after": change.get("after", ""),
            "change_reason": change.get("change_reason", ""),
            "requirement_ids": change.get("requirement_ids") or [],
            "fact_ids": _local_fact_ids(change),
            "verification_status": change.get("verification_status", VS_PENDING),
        })

    tt = plan.get("target_title") or {}
    if squash(tt.get("text", "")):
        add({"before": "", "after": tt["text"], **{k: tt.get(k) for k in
             ("change_reason", "requirement_ids", "fact_ids")},
             "verification_status": tt.get("verification_status", VS_VERIFIED)},
            "求职方向", "header")

    summary = plan.get("summary") or {}
    if squash(summary.get("after", "")):
        add(summary, "个人简介", "summary")

    for i, skill in enumerate(plan.get("skills") or []):
        add(skill, "技能", f"skills[{i}]")
    for exp in plan.get("experiences") or []:
        for j, bullet in enumerate(exp.get("bullets") or []):
            merged = dict(bullet)
            merged.setdefault("fact_ids", [])
            if exp.get("fact_id") and exp["fact_id"] not in merged["fact_ids"]:
                merged["fact_ids"] = [exp["fact_id"], *merged["fact_ids"]]
            add(merged, "工作经历", f"{exp.get('fact_id','?')}#{j}")
    return out


def _local_fact_ids(change: dict) -> list[str]:
    ids = list(change.get("fact_ids") or [])
    single = change.get("fact_id")
    if single and single not in ids:
        ids.insert(0, single)
    return ids


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate_plan(
    plan: dict,
    archive: dict,
    *,
    job: dict | None = None,
    mapping: dict | None = None,
) -> dict:
    """Return `{issues, blocking, stats}`. Never raises; callers decide."""
    issues: list[dict] = []

    def add(code: str, severity: str, message: str, **extra):
        issues.append({"code": code, "severity": severity, "message": message, **extra})

    index = F.fact_index(archive)
    usable = {fid for fid, f in index.items() if f.get("confirm_status") in F.USABLE}
    companies = {
        squash((f.get("structured") or {}).get("company", ""))
        for f in archive.get("facts", []) if f.get("kind") == F.KIND_EMPLOYMENT
    }
    companies.discard("")
    req_ids = set()
    if mapping:
        req_ids = {r["requirement_id"] for r in mapping.get("requirements") or []}

    if plan.get("schema") != SCHEMA_PLAN:
        add("schema", "warn", f"change plan schema 建议为 {SCHEMA_PLAN}")

    changes = flatten_changes(plan)
    if not changes:
        add("empty_plan", "block", "修改方案为空：没有任何可导出的改动。")
        return _finish(issues, changes)

    context = " ".join([
        (job or {}).get("title", ""),
        (job or {}).get("company", ""),
        (plan.get("target_title") or {}).get("text", ""),
    ])
    for change in changes:
        _check_change(
            change, index=index, usable=usable, companies=companies,
            req_ids=req_ids, issues=issues, add=add, extra_basis=context,
        )

    # whole-plan structural rules -----------------------------------------
    tt = plan.get("target_title") or {}
    if not squash(tt.get("text", "")):
        add("no_target_title", "warn", "未设置求职方向：简历抬头将留空。")
    if not squash((plan.get("summary") or {}).get("after", "")):
        add("no_summary", "warn", "没有个人简介：定制感会明显下降。")

    seen_emp: set[str] = set()
    for exp in plan.get("experiences") or []:
        fid = exp.get("fact_id", "")
        if fid and fid in seen_emp:
            add("duplicate_experience", "block", f"经历 {fid} 在方案中出现多次", fact_id=fid)
        seen_emp.add(fid)
        if fid and fid not in index:
            add("unknown_experience_fact", "block", f"方案引用了不存在的工作经历 {fid}", fact_id=fid)
        elif fid and index.get(fid, {}).get("kind") != F.KIND_EMPLOYMENT:
            add("not_employment_fact", "block",
                f"{fid} 不是工作经历事实，不能作为经历段落", fact_id=fid)
        if exp.get("omit") and not squash(exp.get("omit_reason", "")):
            add("omit_without_reason", "warn",
                f"删除了经历 {fid} 但没有写明理由（压缩无关内容需要可追溯）", fact_id=fid)

    for change in changes:
        for rid in change.get("requirement_ids") or []:
            if not rid:
                continue
            if rid not in req_ids and mapping:
                add("unknown_requirement", "block",
                    f"{change['change_id']} 引用了映射表中不存在的要求 {rid}",
                    change_id=change["change_id"], requirement_id=rid)

    return _finish(issues, changes)


def _finish(issues: list[dict], changes: list[dict]) -> dict:
    blocking = [i for i in issues if i.get("severity") == "block"]
    warnings = [i for i in issues if i.get("severity") == "warn"]
    pending = [c for c in changes if c.get("verification_status") == VS_PENDING]
    return {
        "ok": not blocking,
        "issues": issues,
        "blocking": blocking,
        "warnings": warnings,
        "stats": {
            "changes": len(changes),
            "blocking": len(blocking),
            "warnings": len(warnings),
            "pending": len(pending),
            "verified": len(changes) - len(pending),
        },
    }


def _check_change(change, *, index, usable, companies, req_ids, issues, add,
                  extra_basis: str = "") -> None:
    cid = change["change_id"]
    after = change.get("after", "") or ""
    before = change.get("before", "") or ""
    fact_ids = change.get("fact_ids") or []

    if not squash(after):
        add("empty_after", "block", f"{cid} 的改写结果为空", change_id=cid)
        return
    if not fact_ids:
        add("no_fact_basis", "block",
            f"{cid}（{change['section']}）没有引用任何个人事实，无法核验",
            change_id=cid)
        return

    unknown = [f for f in fact_ids if f not in index]
    if unknown:
        add("unknown_fact", "block", f"{cid} 引用了不存在的事实 {unknown}",
            change_id=cid, fact_ids=unknown)
        return

    facts = [index[f] for f in fact_ids]
    unusable = [f["fact_id"] for f in facts if f["fact_id"] not in usable]
    status = change.get("verification_status", VS_PENDING)
    if status not in VS_VALUES:
        add("bad_verification", "block",
            f"{cid} verification_status 必须是 {list(VS_VALUES)}", change_id=cid)
        status = VS_PENDING
    if unusable and status == VS_VERIFIED:
        add("unconfirmed_as_fact", "block",
            f"{cid} 标记为已核验，但引用了未确认事实 {unusable}；"
            "请先让用户确认这些事实，或把状态改为 pending_confirmation",
            change_id=cid, fact_ids=unusable)

    if not squash(change.get("change_reason", "")):
        add("no_reason", "warn", f"{cid} 没有修改理由", change_id=cid)

    # ---- invented numbers
    fact_nums: set[str] = set()
    for f in facts:
        fact_nums |= _numbers(_fact_text(f))
    before_nums = _numbers(before)
    new_nums = _numbers(after) - fact_nums - before_nums
    if new_nums:
        add("invented_number", "block",
            f"{cid} 出现了事实与原文中都没有的数字 {sorted(new_nums)}："
            "数字必须来自个人事实，不能为了好看补一个",
            change_id=cid, numbers=sorted(new_nums))

    # ---- verb escalation
    basis_text = " ".join([_fact_text(f) for f in facts] + [before]).lower()
    after_low = after.lower()
    for weak, strong in VERB_LADDER:
        if weak in basis_text and strong.lower() in after_low and strong.lower() not in basis_text:
            add("verb_escalation", "block",
                f"{cid} 把「{weak}」升级成了「{strong}」："
                "个人事实里没有这个职责强度，不能靠改词提高",
                change_id=cid, weak=weak, strong=strong)

    # ---- hedge dropped: "协助搭建 X" -> "搭建 X" --------------------------
    # The verb did not escalate, the qualifier was simply deleted. Compare the
    # rewrite with what it is based on (its own `before`, or the cited facts).
    hedge_source = before if before else " ".join(_fact_text(f) for f in facts)
    if (any(h in hedge_source for h in HEDGE_WORDS)
            and not any(h in after for h in HEDGE_WORDS)
            and any(a in after and a in hedge_source for a in ACTION_WORDS)):
        add("hedge_dropped", "warn",
            f"{cid} 依据里写的是「协助/参与」，改写中没有保留这个限定词却保留了同一动作；"
            "读起来会变成本人主导，请人工确认措辞",
            change_id=cid)

    # ---- unsupported Chinese wording in newly composed prose --------------
    # `keyword_without_fact` only looks at Latin tech terms. A summary can invent
    # a plausible-sounding Chinese phrase ("专题分析") that appears nowhere in the
    # facts. We cannot prove which phrase is "too generic", so this is a warning
    # carrying the offending n-grams for a human to judge — not a pass.
    # 依据 = 所引事实 + 本条的 before + 岗位上下文（职位名/公司名）
    basis_grams = _cjk_ngrams(" ".join(_fact_text(f) for f in facts), 3)
    basis_grams |= _cjk_ngrams(before, 3)
    basis_grams |= _cjk_ngrams(extra_basis, 3)
    unknown = sorted(_cjk_ngrams(after, 3) - basis_grams)[:8]
    if len(unknown) >= 3:
        add("unsupported_term", "warn",
            f"{cid} 出现依据（事实/原文）里没有的中文表述（3 字片段）：{'、'.join(unknown)}；"
            "若其中有具体领域名词，需要补事实依据再写",
            change_id=cid, terms=unknown)

    # ---- skill level escalation
    for weak, strong in LEVEL_LADDER:
        if weak in basis_text and strong in after and strong not in basis_text:
            add("skill_inflation", "block",
                f"{cid} 把技能水平从「{weak}」提高到「{strong}」，档案中没有依据",
                change_id=cid, weak=weak, strong=strong)

    # ---- level claim with nothing in the archive to back it
    #      ("熟练" is a claim about the person, not a writing choice)
    for word in STRONG_LEVEL_WORDS:
        if word in after and word not in basis_text:
            add("skill_level_unbacked", "block",
                f"{cid} 写了「{word}」，但所引事实与原文都没有给出这个熟练度；"
                "要么在档案里补一条真实水平，要么删掉这个判断",
                change_id=cid, word=word)
    for word in WEAK_LEVEL_WORDS:
        if word in after and word not in basis_text:
            add("skill_level_unbacked", "warn",
                f"{cid} 写了「{word}」，档案中没有对应熟练度描述，建议确认",
                change_id=cid, word=word)

    # ---- team result -> personal result
    basis_has_team = any(w in basis_text for w in TEAM_WORDS)
    after_solo = [w for w in SOLO_WORDS if w in after_low]
    if basis_has_team and after_solo and not any(w in basis_text for w in SOLO_WORDS):
        add("team_to_individual", "block",
            f"{cid} 原文是团队/协作语境，改写后变成 {after_solo}，"
            "把团队成果写成了个人成果",
            change_id=cid, solo_words=after_solo)

    # ---- absolute claims
    for word in ABSOLUTE_WORDS:
        if word in after and word not in basis_text:
            add("absolute_claim", "warn",
                f"{cid} 出现绝对化表述「{word}」，事实中没有对应证据",
                change_id=cid, word=word)

    # ---- identity drift: company swap
    for company in companies:
        if company and company in after and not any(company in _fact_text(f) for f in facts):
            add("identity_company_swap", "block",
                f"{cid} 提到了不属于所引事实的公司「{company}」", change_id=cid)
            break

    # ---- identity drift: degree
    fact_degrees = {d for f in facts for d in DEGREE_WORDS if d in _fact_text(f)}
    after_degrees = {d for d in DEGREE_WORDS if d in after}
    invented = after_degrees - fact_degrees
    if invented and not any(d in before for d in invented):
        add("identity_degree_invented", "block",
            f"{cid} 出现了档案中没有的学历表述 {sorted(invented)}", change_id=cid)

    # ---- keyword stuffing: latin tech terms not grounded in facts
    fact_terms = _fact_terms(facts)
    after_terms = {t.lower() for t in LATIN_TERM.findall(after)}
    before_terms = {t.lower() for t in LATIN_TERM.findall(before)}
    ungrounded = after_terms - fact_terms - before_terms
    if ungrounded:
        add("keyword_without_fact", "warn",
            f"{cid} 新增了事实中没有的技术名词 {sorted(ungrounded)}："
            "使用岗位术语必须有人个事实依据",
            change_id=cid, terms=sorted(ungrounded))

    if change.get("requirement_ids") and req_ids:
        missing = [r for r in change["requirement_ids"] if r not in req_ids]
        if missing:
            add("unknown_requirement", "block",
                f"{cid} 引用了不存在的岗位要求 {missing}", change_id=cid)


def require_valid(plan: dict, archive: dict, *, job: dict | None = None,
                  mapping: dict | None = None) -> dict:
    report = validate_plan(plan, archive, job=job, mapping=mapping)
    if report["blocking"]:
        raise ValidationError(
            f"修改方案未通过事实校验：{len(report['blocking'])} 处阻断问题。",
            hint="按 issues 修正措辞或补事实；不要通过删除校验来过关。",
            detail={"blocking": report["blocking"][:20],
                    "total_blocking": len(report["blocking"])},
        )
    return report


# ---------------------------------------------------------------------------
# assemble the resume document from facts
# ---------------------------------------------------------------------------


def build_resume_doc(
    archive: dict,
    plan: dict,
    *,
    job: dict | None = None,
    include_pending: bool = False,
) -> dict:
    """Facts supply identity and history; the plan supplies only prose."""
    index = F.fact_index(archive)
    person = archive.get("person") or {}
    name_fact = index.get(person.get("name_fact_id", ""), {})
    name = person.get("display_name") or name_fact.get("content", "")

    contacts: list[dict] = []
    for fact in archive.get("facts", []):
        if fact.get("kind") != F.KIND_CONTACT:
            continue
        if fact.get("confirm_status") not in F.USABLE:
            continue
        st = fact.get("structured") or {}
        contacts.append({
            "type": st.get("type", "other"),
            "text": fact.get("content", ""),
            "fact_id": fact["fact_id"],
        })

    pending: list[dict] = []

    def take(change: dict, section: str) -> dict | None:
        status = change.get("verification_status", VS_PENDING)
        row = {
            "text": change.get("after", ""),
            "fact_ids": _local_fact_ids(change),
            "verification_status": status,
            "change_id": change.get("change_id", ""),
        }
        if status == VS_PENDING and not include_pending:
            pending.append({**row, "section": section,
                            "reason": "引用的事实尚未由用户确认"})
            return None
        return row

    doc: dict[str, Any] = {
        "schema": SCHEMA_DOC,
        "job_id": plan.get("job_id", ""),
        "company": (job or {}).get("company", ""),
        "job_title": (job or {}).get("title", ""),
        "name": name,
        "name_fact_id": person.get("name_fact_id", ""),
        "contacts": contacts,
        "target_title": squash((plan.get("target_title") or {}).get("text", "")),
        "summary": None,
        "skills": [],
        "experiences": [],
        "education": [],
        "achievements": [],
        "pending_items": pending,
        "generated_at": now_iso(),
    }

    summary = plan.get("summary") or {}
    if squash(summary.get("after", "")):
        doc["summary"] = take(summary, "个人简介")

    for skill in plan.get("skills") or []:
        row = take(skill, "技能")
        if row:
            row["source_label"] = (index.get(row["fact_ids"][0], {}) or {}).get("label", "") \
                if row["fact_ids"] else ""
            doc["skills"].append(row)

    experiences = [e for e in (plan.get("experiences") or []) if not e.get("omit")]
    experiences.sort(key=lambda e: (e.get("order") is None, e.get("order") or 0))
    for exp in experiences:
        fact = index.get(exp.get("fact_id", ""))
        if not fact or fact.get("kind") != F.KIND_EMPLOYMENT:
            continue
        st = fact.get("structured") or {}
        bullets = []
        for bullet in exp.get("bullets") or []:
            merged = dict(bullet)
            merged.setdefault("fact_ids", [])
            if fact["fact_id"] not in merged["fact_ids"]:
                merged["fact_ids"] = [fact["fact_id"], *merged["fact_ids"]]
            row = take(merged, "工作经历")
            if row:
                row["requirement_ids"] = merged.get("requirement_ids") or []
                bullets.append(row)
        doc["experiences"].append({
            # identity fields come from the fact archive, never from the plan
            "fact_id": fact["fact_id"],
            "company": st.get("company", ""),
            "title": st.get("title", ""),
            "period": _period(st),
            "location": st.get("location", ""),
            "bullets": bullets,
        })

    for fact in archive.get("facts", []):
        if fact.get("kind") == F.KIND_EDUCATION and fact.get("confirm_status") in F.USABLE:
            st = fact.get("structured") or {}
            doc["education"].append({
                "fact_id": fact["fact_id"],
                "school": st.get("school", ""),
                "degree": st.get("degree", ""),
                "major": st.get("major", ""),
                "date_raw": st.get("date_raw", ""),
                "text": fact.get("content", ""),
            })
        elif fact.get("kind") == F.KIND_ACHIEVEMENT and fact.get("confirm_status") in F.USABLE:
            doc["achievements"].append({"fact_id": fact["fact_id"],
                                        "text": fact.get("content", "")})

    doc["pending_items"] = pending
    doc["stats"] = {
        "experiences": len(doc["experiences"]),
        "bullets": sum(len(e["bullets"]) for e in doc["experiences"]),
        "skills": len(doc["skills"]),
        "pending": len(pending),
    }
    return doc


def _period(st: dict) -> str:
    start = st.get("start", "")
    if not start:
        return st.get("date_raw", "")
    end = "至今" if st.get("ongoing") else (st.get("end") or "")
    return f"{start} – {end}" if end else start


def render_text(doc: dict, *, include_pending: bool = False) -> str:
    """Plain-text rendering, used for TXT export and PDF/DOCX comparison."""
    lines: list[str] = []
    header = [doc.get("name", "")]
    if doc.get("target_title"):
        header.append(doc["target_title"])
    contact_bits = [c["text"] for c in doc.get("contacts") or []]
    if contact_bits:
        header.append(" | ".join(contact_bits))
    lines.extend([h for h in header if h])
    lines.append("")

    if doc.get("summary"):
        lines.append("个人简介")
        lines.append(doc["summary"]["text"])
        lines.append("")

    if doc.get("skills"):
        lines.append("核心技能")
        for skill in doc["skills"]:
            lines.append(f"· {skill['text']}")
        lines.append("")

    if doc.get("experiences"):
        lines.append("工作经历")
        for exp in doc["experiences"]:
            head = " | ".join(x for x in (exp.get("company"), exp.get("title"), exp.get("period")) if x)
            if head:
                lines.append(head)
            for bullet in exp["bullets"]:
                marker = "【待确认】" if bullet["verification_status"] == VS_PENDING else ""
                lines.append(f"· {marker}{bullet['text']}")
            lines.append("")

    if doc.get("education"):
        lines.append("教育背景")
        for edu in doc["education"]:
            head = " | ".join(x for x in (edu.get("school"), edu.get("degree"),
                                          edu.get("major"), edu.get("date_raw")) if x)
            lines.append(head or edu.get("text", ""))
        lines.append("")

    if doc.get("achievements"):
        lines.append("证书与荣誉")
        for item in doc["achievements"]:
            lines.append(f"· {item['text']}")
        lines.append("")

    if include_pending and doc.get("pending_items"):
        lines.append("待确认内容（未写入正式简历）")
        for item in doc["pending_items"]:
            lines.append(f"· [{item['section']}] {item['text']}")
    return "\n".join(lines).strip() + "\n"


def doc_fact_ids(doc: dict) -> set[str]:
    ids: set[str] = set()
    if doc.get("name_fact_id"):
        ids.add(doc["name_fact_id"])
    for c in doc.get("contacts") or []:
        ids.add(c["fact_id"])
    if doc.get("summary"):
        ids.update(doc["summary"].get("fact_ids") or [])
    for s in doc.get("skills") or []:
        ids.update(s.get("fact_ids") or [])
    for e in doc.get("experiences") or []:
        ids.add(e["fact_id"])
        for b in e["bullets"]:
            ids.update(b.get("fact_ids") or [])
    for edu in doc.get("education") or []:
        ids.add(edu["fact_id"])
    return {i for i in ids if i}


def check_traceability(doc: dict, archive: dict) -> list[dict]:
    """Every published string must trace to a usable fact."""
    index = F.fact_index(archive)
    issues: list[dict] = []
    for fid in sorted(doc_fact_ids(doc)):
        fact = index.get(fid)
        if not fact:
            issues.append({"code": "untraceable_fact", "severity": "block",
                           "message": f"简历引用了档案中不存在的事实 {fid}", "fact_id": fid})
        elif fact.get("confirm_status") not in F.USABLE:
            issues.append({"code": "unconfirmed_in_doc", "severity": "block",
                           "message": f"简历包含未确认事实 {fid}",
                           "fact_id": fid})
    if not doc.get("name"):
        issues.append({"code": "no_name", "severity": "warn", "message": "简历没有姓名"})
    if not doc.get("experiences"):
        issues.append({"code": "no_experience", "severity": "warn",
                       "message": "简历没有工作经历段落"})
    return issues


def readiness(doc: dict, plan_report: dict, mapping_report: dict | None,
              job: dict | None) -> dict:
    """A blunt answer to 'is this a fully tailored resume?'."""
    reasons: list[str] = []
    if doc.get("stats", {}).get("pending"):
        reasons.append(f"{doc['stats']['pending']} 条待确认内容未写入")
    if job and not (job.get("completeness") or {}).get("is_complete", True):
        reasons.append("职位详情不完整")
    if mapping_report:
        cov = mapping_report.get("coverage") or {}
        if (cov.get("must_ratio") or 0) < 0.5:
            reasons.append("必备条件覆盖率低于 50%")
    return {
        "fully_tailored": not reasons,
        "reasons": reasons,
        "statement": "已完成全面定制" if not reasons
        else "仅生成保守草稿：" + "；".join(reasons),
    }
