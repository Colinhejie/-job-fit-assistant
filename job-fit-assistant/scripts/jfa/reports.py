# -*- coding: utf-8 -*-
"""Markdown deliverables: change diff, match & gap report, fact checklist,
greeting draft.

The greeting is authored by the agent (or generated here as a conservative
fallback from confirmed facts only). It is a *draft*: nothing is ever sent by
this tool.
"""

from __future__ import annotations

from typing import Any

from . import facts as F
from . import mapping as M
from . import salary as salary_mod
from .rewrite import VS_PENDING, flatten_changes
from .util import now_iso, squash, truncate

STATUS_BADGE = {
    "verified": "已核验",
    "pending_confirmation": "待确认",
}


def render_change_diff(
    plan: dict,
    *,
    job: dict | None = None,
    mapping: dict | None = None,
) -> str:
    changes = flatten_changes(plan)
    req_titles = {r["requirement_id"]: r["requirement"]
                  for r in (mapping or {}).get("requirements") or []}
    head = [
        "# 简历修改对照",
        "",
        f"- 目标岗位：{(job or {}).get('company', '')} · {(job or {}).get('title', '')}",
        f"- 生成时间：{now_iso()}",
        f"- 改动条数：{len(changes)}",
        "",
        "每条改动都可以追溯到事实编号（fact_ids）与岗位要求（requirement_ids）。"
        "没有事实依据的措辞会在导出前被拦下。",
        "",
        "| # | 板块 | 修改前 | 修改后 | 修改理由 | 对应要求 | 事实 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = []
    for change in changes:
        reqs = "、".join(f"{r}" for r in change.get("requirement_ids") or []) or "—"
        facts = "、".join(change.get("fact_ids") or []) or "—"
        rows.append(
            f"| {change['change_id']} | {change['section']} "
            f"| {_cell(change.get('before', ''), 160)} "
            f"| {_cell(change.get('after', ''), 220)} "
            f"| {_cell(change.get('change_reason', ''), 120)} "
            f"| {reqs} | {facts} "
            f"| {STATUS_BADGE.get(change.get('verification_status'), change.get('verification_status'))} |"
        )

    tail: list[str] = []
    omitted = [e for e in plan.get("experiences") or [] if e.get("omit")]
    if omitted:
        tail += ["", "## 已删除/压缩的内容", ""]
        for exp in omitted:
            tail.append(f"- `{exp.get('fact_id')}`：{exp.get('omit_reason') or '未注明理由'}")
    if req_titles:
        tail += ["", "## 岗位要求对照", ""]
        for rid, title in req_titles.items():
            tail.append(f"- `{rid}` {truncate(title, 100)}")
    open_q = plan.get("open_questions") or []
    if open_q:
        tail += ["", "## 待你补充的问题", ""]
        for q in open_q:
            tail.append(f"- {q}")
    return "\n".join(head + rows + tail) + "\n"


def render_match_report(
    mapping: dict,
    *,
    job: dict | None = None,
    screening: dict | None = None,
    archive: dict | None = None,
) -> str:
    coverage = mapping.get("coverage") or M.compute_coverage(mapping)
    job = job or {}
    lines = [
        "# 岗位匹配与缺口报告",
        "",
        f"- 公司：{job.get('company', '')}",
        f"- 职位：{job.get('title', '')}",
        f"- 地点：{(job.get('sections') or {}).get('city') or job.get('location', '')}",
        f"- 来源：{(job.get('source') or {}).get('url') or '用户粘贴/文件'}",
        f"- 读取时间：{(job.get('source') or {}).get('read_at', '')}",
        f"- 读取范围：{(job.get('source') or {}).get('read_scope', '')}",
        "",
        "## 覆盖度",
        "",
        f"- 要求总数：{coverage.get('total')}（必备 {coverage.get('must_total')} / 加分 {coverage.get('nice_total')}）",
        f"- 必备条件覆盖率：{_pct(coverage.get('must_ratio'))}",
        f"- 加分项覆盖率：{_pct(coverage.get('nice_ratio'))}",
        f"- 综合匹配分：{coverage.get('score')}",
        f"- 计分口径：{coverage.get('score_note')}",
        "",
        "| 要求 | 重要性 | 结论 | 依据事实 | 解释 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for req in mapping.get("requirements") or []:
        lines.append(
            f"| {_cell(req.get('requirement', ''), 120)} "
            f"| {'必备' if req.get('importance') == M.IMPORTANCE_MUST else '加分'} "
            f"| {M.STATUS_LABELS.get(req.get('status'), req.get('status'))} "
            f"| {'、'.join(req.get('fact_ids') or []) or '—'} "
            f"| {_cell(req.get('explanation', ''), 120)} |"
        )

    gaps = M.gaps(mapping)
    lines += ["", "## 缺口（按重要性排序）", ""]
    if gaps:
        for gap in gaps:
            lines.append(
                f"- **{gap['requirement_id']}**（{gap['status_label']}）"
                f"{truncate(gap['requirement'], 100)}"
            )
    else:
        lines.append("- 没有未覆盖的要求。")

    completeness = job.get("completeness") or {}
    if not completeness.get("is_complete", True):
        lines += ["", "## 信息完整性警示", "",
                  f"- 缺失栏目：{'、'.join(completeness.get('missing') or [])}",
                  "- 结论：本次不能声称「已完成全面定制」，缺口见上。"]
    notes = (screening or {}).get("caveats") or []
    if notes:
        lines += ["", "## 注意事项", ""]
        for note in notes:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def render_fact_check(
    archive: dict,
    *,
    doc: dict | None = None,
    plan_report: dict | None = None,
    ocr_review: list[dict] | None = None,
) -> str:
    facts = archive.get("facts") or []
    by_status: dict[str, list[dict]] = {}
    for fact in facts:
        by_status.setdefault(fact.get("confirm_status", "?"), []).append(fact)

    lines = [
        "# 事实检查与待确认清单",
        "",
        f"- 事实总数：{len(facts)}",
        f"- 原始材料明确提供：{len(by_status.get(F.STATUS_SOURCE_EXPLICIT, []))}",
        f"- 用户补充并确认：{len(by_status.get(F.STATUS_USER_CONFIRMED, []))}",
        f"- 未确认：{len(by_status.get(F.STATUS_UNCONFIRMED, []))}",
        f"- 存在冲突：{len(by_status.get(F.STATUS_CONFLICT, []))}",
        "",
        "> 「已确认」只表示你本人确认过，不代表任何第三方核实。",
        "",
    ]

    tenure = archive.get("tenure") or {}
    if tenure:
        lines += ["## 工作年限", "",
                  f"- 合并后总年限：{tenure.get('total_text', '—')}"
                  f"（{tenure.get('total_months', 0)} 个月）",
                  f"- 直接相加：{tenure.get('raw_sum_text', '—')}"
                  f"（{tenure.get('raw_sum_months', 0)} 个月）",
                  f"- 说明：{tenure.get('note', '')}"]
        if tenure.get("concurrent_detected"):
            lines.append("- ⚠ 检测到并行任职，已按区间合并，未简单累加。")
        lines.append("")

    unconfirmed = by_status.get(F.STATUS_UNCONFIRMED, [])
    if unconfirmed:
        lines += ["## 待确认事实", "", "| 编号 | 类型 | 内容 | 缺失原因 |", "| --- | --- | --- | --- |"]
        for fact in unconfirmed[:50]:
            reason = "机器拆分/推断，需人工确认" if fact.get("needs_review") else "尚未确认"
            lines.append(
                f"| {fact['fact_id']} | {fact.get('kind')} | {_cell(fact.get('content', ''), 100)} | {reason} |"
            )
        lines.append("")

    conflicts = by_status.get(F.STATUS_CONFLICT, [])
    if conflicts:
        lines += ["## 冲突事实", ""]
        for fact in conflicts:
            lines.append(f"- {fact['fact_id']}：{truncate(fact.get('content', ''), 120)}"
                         f"　冲突对象：{'、'.join(fact.get('conflicts_with') or [])}")
        lines.append("")

    if doc and doc.get("pending_items"):
        lines += ["## 未写入简历的待确认内容", "",
                  "以下内容有改写方案，但依据的事实未经你确认，因此**没有**写进正式简历：", ""]
        for item in doc["pending_items"]:
            lines.append(f"- [{item.get('section')}] {truncate(item.get('text', ''), 120)}")
        lines.append("")

    if plan_report:
        stats = plan_report.get("stats") or {}
        lines += ["## 改写校验结果", "",
                  f"- 改动 {stats.get('changes', 0)} 条：已核验 {stats.get('verified', 0)}，"
                  f"待确认 {stats.get('pending', 0)}",
                  f"- 阻断问题：{stats.get('blocking', 0)}；警告：{stats.get('warnings', 0)}"]
        warnings = plan_report.get("warnings") or []
        if warnings:
            lines += ["", "警告明细：", ""]
            for issue in warnings[:30]:
                lines.append(f"- [{issue['code']}] {issue['message']}")
        lines.append("")

    if ocr_review:
        lines += ["## OCR 核对记录", ""]
        for row in ocr_review[:30]:
            lines.append(f"- {row.get('block_id')}：{row.get('action')}"
                         + (f"（{row.get('reason')}）" if row.get("reason") else ""))
        lines.append("")

    return "\n".join(lines) + "\n"


def draft_greeting(
    job: dict,
    mapping: dict | None,
    archive: dict,
    *,
    plan: dict | None = None,
) -> dict:
    """Conservative greeting draft built only from confirmed facts.

    This is a fallback for hosts that cannot author prose themselves. It never
    adds a claim that is not in the fact archive, and it is never sent.
    """
    authored = ((plan or {}).get("greeting") or {})
    if squash(authored.get("text", "")):
        return {
            "text": authored["text"],
            "source": "agent_authored",
            "verification_status": authored.get("verification_status", VS_PENDING),
            "fact_ids": authored.get("fact_ids") or [],
            "requirement_ids": authored.get("requirement_ids") or [],
            "note": "草稿由宿主 agent 撰写；本工具只做事实校验，不发送任何消息。",
        }

    index = F.fact_index(archive)
    name = (archive.get("person") or {}).get("display_name", "")
    title = job.get("title", "")
    company = job.get("company", "")

    strongest: dict | None = None
    for req in (mapping or {}).get("requirements") or []:
        if req.get("status") != M.STATUS_DIRECT:
            continue
        for fid in req.get("fact_ids") or []:
            fact = index.get(fid)
            if fact and fact.get("confirm_status") in F.USABLE:
                strongest = {"fact": fact, "requirement": req}
                break
        if strongest:
            break

    parts: list[str] = []
    parts.append(f"您好，我是{name}。" if name else "您好，")
    if title and company:
        parts.append(f"看到贵司在招{title}，想应聘。")
    elif title:
        parts.append(f"看到{title}这个岗位，想应聘。")

    if strongest:
        fact = strongest["fact"]
        evidence = _evidence_line(fact)
        if evidence:
            parts.append(evidence)
            fact_ids = [fact["fact_id"]]
            req_ids = [strongest["requirement"]["requirement_id"]]
        else:
            fact_ids, req_ids = [], []
    else:
        fact_ids, req_ids = [], []

    gaps = M.gaps(mapping or {}) if mapping else []
    if gaps:
        parts.append("岗位有一部分要求我目前还没有直接经验，如果方便，想先确认这部分是否是硬性条件。")
    else:
        parts.append("如果方便，想进一步了解岗位的团队情况和具体职责，谢谢。")

    return {
        "text": "".join(parts),
        "source": "generated_from_facts",
        "verification_status": "verified" if fact_ids else VS_PENDING,
        "fact_ids": fact_ids,
        "requirement_ids": req_ids,
        "note": (
            "草稿仅由已确认事实生成，未包含任何未经确认的经历或数字。"
            "按当前版本设计，本工具不会自动发送、不会投递、不会修改在线简历。"
        ),
    }


def _evidence_line(fact: dict) -> str:
    """One factual sentence about a fact, with no adjectives added."""
    st = fact.get("structured") or {}
    if fact.get("kind") == F.KIND_EMPLOYMENT:
        company = st.get("company", "")
        title = st.get("title", "")
        if company and title:
            return f"我此前在{company}担任{title}。"
        return ""
    if fact.get("kind") == F.KIND_SKILL:
        text = squash(fact.get("content", ""))
        return f"我会用{text}。" if text else ""
    if fact.get("kind") == F.KIND_EDUCATION:
        school = st.get("school", "")
        degree = st.get("degree", "")
        if school or degree:
            return f"我是{school}{degree}背景。".replace("背景。", "。")
    return ""


def render_greeting(draft: dict, job: dict, *, max_chars: int = 200) -> str:
    text = draft.get("text", "")
    warning = ""
    if len(text) > max_chars:
        warning = f"\n> 提示：草稿 {len(text)} 字，招聘平台打招呼通常不宜超过 {max_chars} 字，建议精简。\n"
    return "\n".join([
        "# 打招呼草稿（不会自动发送）",
        "",
        f"- 目标：{job.get('company', '')} · {job.get('title', '')}",
        f"- 生成方式：{draft.get('source')}",
        f"- 事实依据：{'、'.join(draft.get('fact_ids') or []) or '无'}",
        f"- 对应要求：{'、'.join(draft.get('requirement_ids') or []) or '无'}",
        "",
        "```text",
        text,
        "```",
        warning,
        f"> {draft.get('note', '')}",
        "",
        "发送前请你自己检查一遍：称呼、公司名、岗位名是否与页面一致。",
        "",
    ])


def _cell(text: str, limit: int) -> str:
    value = squash(text or "").replace("|", "\\|")
    value = value.replace("\n", " ")
    return truncate(value, limit)


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.0%}"
