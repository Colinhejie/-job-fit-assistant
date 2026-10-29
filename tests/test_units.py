# -*- coding: utf-8 -*-
"""Unit tests for the local core. No pytest dependency on purpose.

    python tests/test_units.py [-v]

Each test is a function named `test_*`; a plain assert failure is a test failure.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "job-fit-assistant" / "scripts"))

from jfa import blocks as B  # noqa: E402
from jfa import facts as F  # noqa: E402
from jfa import jobs as J  # noqa: E402
from jfa import mapping as M  # noqa: E402
from jfa import rewrite as RW  # noqa: E402
from jfa import salary as S  # noqa: E402
from jfa import screen  # noqa: E402
from jfa import util  # noqa: E402
from jfa.dates import (  # noqa: E402
    extract_date_span,
    month_span,
    parse_month,
    parse_range,
)
from jfa.errors import OcrUnavailable, SafetyViolation, ValidationError  # noqa: E402
from jfa.extract.ocr import base as ocr_base  # noqa: E402
from jfa.extract.ocr import review as review_mod  # noqa: E402

VERBOSE = "-v" in sys.argv


# ---------------------------------------------------------------------------
# salary
# ---------------------------------------------------------------------------


def test_salary_range_with_single_suffix():
    s = S.parse_salary("20-30K·13薪")
    assert s.kind == "range", s.kind
    assert s.min_monthly == 20000, s.min_monthly
    assert s.max_monthly == 30000, s.max_monthly
    assert s.months_per_year == 13
    assert s.months_conditional is True


def test_salary_annual_converted_to_monthly():
    s = S.parse_salary("年薪 36-48 万")
    assert s.period == "year"
    assert abs(s.min_monthly - 30000) < 1, s.min_monthly
    assert abs(s.max_monthly - 40000) < 1, s.max_monthly


def test_salary_negotiable():
    s = S.parse_salary("薪资面议")
    assert s.kind == "negotiable"
    assert s.guaranteed_annual() is None


def test_salary_commission_not_guaranteed():
    s = S.parse_salary("底薪 8000-12000 元/月 + 提成")
    assert s.has_commission is True
    assert s.guaranteed_annual() == 8000 * 12
    assert any("提成" in note for note in s.notes)


def test_salary_day_rate():
    s = S.parse_salary("400 元/天")
    assert s.period == "day"
    assert abs(s.min_monthly - 400 * 21.75) < 1


def test_salary_compare_overlap_only():
    s = S.parse_salary("20-30K")
    result = S.compare(360000, s)          # 30 万保证, 36 万可能
    assert result["verdict"] == "overlap_only", result
    assert "不等于你能拿到" in result["note"]


def test_salary_compare_meets_guaranteed():
    s = S.parse_salary("30-40K")
    result = S.compare(300000, s)
    assert result["verdict"] == "meets_target_guaranteed", result


def test_salary_compare_below():
    s = S.parse_salary("10-12K")
    result = S.compare(400000, s)
    assert result["verdict"] == "below_target", result


def test_salary_unknown_no_data():
    s = S.parse_salary("")
    assert s.kind == "unknown"
    result = S.compare(300000, s)
    assert result["verdict"] == "no_data"
    assert "不能据此判断是否达标" in result["note"]


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def test_parse_month_formats():
    assert parse_month("2021.03").iso() == "2021-03"
    assert parse_month("2021-03").iso() == "2021-03"
    assert parse_month("2021年3月").iso() == "2021-03"
    assert parse_month("Mar 2021").iso() == "2021-03"
    assert parse_month("2019").iso() == "2019"


def test_parse_range_ongoing():
    start, end, ongoing = parse_range("2021.03 - 至今")
    assert start.iso() == "2021-03"
    assert end is None
    assert ongoing is True


def test_parse_range_closed():
    start, end, ongoing = parse_range("2019.07 - 2021.02")
    assert start.iso() == "2019-07"
    assert end.iso() == "2021-02"
    assert ongoing is False


def test_extract_date_span_does_not_eat_separator():
    # regression: the day-of-month group used to swallow " - 20"
    assert extract_date_span("云帆大学 本科 2015.09 - 2019.06") == "2015.09 - 2019.06"
    assert extract_date_span("2021年3月15日") == "2021年3月15日"


def test_month_span():
    start = parse_month("2021.03")
    end = parse_month("2023.06")
    # inclusive of both endpoints, matching how Chinese resumes count tenure
    assert month_span(start, end, False) == 28, month_span(start, end, False)


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------


def test_block_flags_garbled():
    builder = B.BlockBuilder("s1")
    block = builder.add("张三\ufffd\ufffd", method=B.M_OCR, confidence=0.9, page=1)
    assert "garbled_glyph" in block.flags
    assert block.check_status == B.CHECK_REVIEW


def test_block_flags_low_confidence():
    builder = B.BlockBuilder("s1")
    block = builder.add("13800001111", method=B.M_OCR, confidence=0.5, page=1)
    assert "low_confidence" in block.flags
    assert block.check_status == B.CHECK_REVIEW


def test_block_flags_impossible_date():
    builder = B.BlockBuilder("s1")
    block = builder.add("2022.13 - 2023.01", method=B.M_OCR, confidence=0.99, page=1)
    assert any(f.startswith("odd_date") for f in block.flags), block.flags


def test_block_docx_has_no_page():
    builder = B.BlockBuilder("s1")
    block = builder.add("工作经历", method=B.M_DOCX_PARAGRAPH)
    assert block.page is None
    assert "missing_page_ref" not in block.flags


def test_block_docx_textbox_needs_review():
    builder = B.BlockBuilder("s1")
    block = builder.add("证书：软件设计师", method=B.M_DOCX_TEXTBOX)
    assert block.check_status == B.CHECK_REVIEW


def test_reading_order_conflict_detected():
    builder = B.BlockBuilder("s1")
    first = builder.add("上", method=B.M_PDF_TEXT, page=1, locator={"bbox": [0, 100, 50, 120]})
    second = builder.add("下", method=B.M_PDF_TEXT, page=1, locator={"bbox": [0, 10, 50, 30]})
    conflicts = B.detect_reading_order_conflicts([first, second])
    assert conflicts and conflicts[0]["page"] == 1


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------


def _archive_with(facts: list[dict]) -> dict:
    return {
        "schema": F.SCHEMA_FACTS,
        "person": {"display_name": "张三", "name_fact_id": facts[0]["fact_id"] if facts else ""},
        "facts": facts,
        "tenure": {},
    }


def _fact(fid: str, kind: str = F.KIND_SKILL, content: str = "Python",
          status: str = F.STATUS_SOURCE_EXPLICIT, **structured) -> dict:
    return {
        "fact_id": fid, "kind": kind, "content": content, "label": content,
        "structured": structured, "source": {"block_ids": [], "file": "", "page": None,
                                             "locator": {}, "method": ""},
        "confirm_status": status, "verification_basis": "self_attested",
        "provenance": "resume_extract", "needs_review": False,
        "version": 1, "created_at": "", "updated_at": "", "revisions": [],
    }


def test_facts_validate_duplicate_id():
    archive = _archive_with([_fact("F-0001"), _fact("F-0001", content="Redis")])
    issues = F.validate(archive)
    assert any(i["code"] == "duplicate_id" for i in issues)


def test_facts_validate_bad_status():
    archive = _archive_with([_fact("F-0001", status="verified")])
    issues = F.validate(archive)
    assert any(i["code"] == "bad_status" and i["severity"] == "block" for i in issues)


def test_facts_validate_verification_basis():
    bad = _fact("F-0001")
    bad["verification_basis"] = "third_party_verified"
    issues = F.validate(_archive_with([bad]))
    assert any(i["code"] == "bad_basis" for i in issues)


def test_tenure_union_for_concurrent_jobs():
    archive = _archive_with([
        _fact("F-0001", F.KIND_EMPLOYMENT, "A", company="A", start="2020-01", end="2022-01"),
        _fact("F-0002", F.KIND_EMPLOYMENT, "B", company="B", start="2021-01", end="2023-01"),
    ])
    tenure = F.tenure_summary(archive)
    # inclusive months: 2020-01..2023-01 = 37, naive sum = 25 + 25 = 50
    assert tenure["total_months"] == 37, tenure
    assert tenure["raw_sum_months"] == 50, tenure
    assert tenure["concurrent_detected"] is True
    assert tenure["total_months"] < tenure["raw_sum_months"]


def test_tenure_ignores_unparseable():
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, "A", company="A")])
    tenure = F.tenure_summary(archive)
    assert tenure["total_months"] == 0
    assert "没有可计算任职区间" in tenure["note"]


def test_revise_fact_keeps_history():
    archive = _archive_with([_fact("F-0001")])
    F.revise_fact(archive, "F-0001", content="Python（熟练）", confirm_status="user_confirmed")
    fact = archive["facts"][0]
    assert fact["version"] == 2
    assert fact["revisions"][0]["before"]["content"] == "Python"
    assert fact["revisions"][0]["after"]["content"] == "Python（熟练）"


def test_dedupe_employment_keeps_one():
    archive = _archive_with([
        _fact("F-0001", F.KIND_EMPLOYMENT, "A 后端", company="A", title="后端", start="2020-01"),
        _fact("F-0002", F.KIND_EMPLOYMENT, "A 后端（PDF）", company="A", title="后端", start="2020-01"),
    ])
    F.dedupe(archive)
    assert len(archive["facts"]) == 1
    assert archive["deduped"][0]["duplicate_of"] == "F-0001"


def test_add_user_fact_defaults_confirmed():
    archive = _archive_with([])
    fact = F.add_user_fact(archive, kind=F.KIND_SKILL, content="Kubernetes")
    assert fact["confirm_status"] == F.STATUS_USER_CONFIRMED
    assert fact["provenance"] == "user_statement"


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------


def _mapping(rows: list[dict]) -> dict:
    return {"schema": M.SCHEMA_MAP, "requirements": rows}


def _req(rid="R-001", status=M.STATUS_DIRECT, importance=M.IMPORTANCE_MUST,
         fact_ids=None, explanation="") -> dict:
    return {"requirement_id": rid, "requirement": "熟悉 Python 后端开发",
            "importance": importance, "status": status,
            "fact_ids": fact_ids or [], "explanation": explanation}


def test_mapping_direct_without_evidence_blocks():
    archive = _archive_with([_fact("F-0001")])
    issues = M.validate_map(_mapping([_req(fact_ids=[])]), archive)
    assert any(i["code"] == "direct_without_evidence" for i in issues)


def test_mapping_direct_with_unconfirmed_fact_blocks():
    archive = _archive_with([_fact("F-0001", status=F.STATUS_UNCONFIRMED)])
    issues = M.validate_map(_mapping([_req(fact_ids=["F-0001"])]), archive)
    assert any(i["code"] == "evidence_unconfirmed" for i in issues)


def test_mapping_missing_with_evidence_blocks():
    archive = _archive_with([_fact("F-0001")])
    issues = M.validate_map(
        _mapping([_req(status=M.STATUS_MISSING, fact_ids=["F-0001"])]), archive)
    assert any(i["code"] == "missing_but_has_evidence" for i in issues)


def test_mapping_transferable_needs_explanation():
    archive = _archive_with([_fact("F-0001", content="Python 订单系统")])
    issues = M.validate_map(
        _mapping([_req(status=M.STATUS_TRANSFERABLE, fact_ids=["F-0001"])]), archive)
    assert any(i["code"] == "transferable_without_explanation" for i in issues)


def test_mapping_unknown_fact_blocks():
    archive = _archive_with([_fact("F-0001")])
    issues = M.validate_map(_mapping([_req(fact_ids=["F-9999"])]), archive)
    assert any(i["code"] == "unknown_fact" for i in issues)


def test_coverage_weights_must_higher():
    mapping = _mapping([
        _req("R-001", M.STATUS_DIRECT, M.IMPORTANCE_MUST, ["F-0001"]),
        _req("R-002", M.STATUS_MISSING, M.IMPORTANCE_NICE),
    ])
    coverage = M.compute_coverage(mapping)
    assert coverage["must_ratio"] == 1.0
    assert coverage["nice_ratio"] == 0.0
    assert coverage["score"] == 0.7, coverage


def test_high_value_questions_only_must_gaps():
    mapping = _mapping([
        _req("R-001", M.STATUS_MISSING, M.IMPORTANCE_MUST),
        _req("R-002", M.STATUS_MISSING, M.IMPORTANCE_NICE),
        _req("R-003", M.STATUS_MISSING, M.IMPORTANCE_MUST),
        _req("R-004", M.STATUS_MISSING, M.IMPORTANCE_MUST),
    ])
    questions = M.high_value_questions(mapping, {}, limit=3)
    assert len(questions) == 3
    assert all(q["can_skip"] is True for q in questions)


# ---------------------------------------------------------------------------
# rewrite guards
# ---------------------------------------------------------------------------


def _plan_for(fact_id: str, after: str, before: str = "协助团队完成后端接口开发") -> dict:
    plan = RW.empty_plan("job-test")
    plan["target_title"]["text"] = "后端开发工程师"
    plan["target_title"]["fact_ids"] = [fact_id]
    plan["target_title"]["verification_status"] = "verified"
    plan["experiences"] = [{
        "fact_id": fact_id, "order": 1, "omit": False, "omit_reason": "",
        "bullets": [{
            "before": before, "after": after,
            "change_reason": "test", "requirement_ids": [],
            "fact_ids": [fact_id], "verification_status": "verified",
        }],
    }]
    return plan


def _codes(report: dict) -> set[str]:
    return {i["code"] for i in report["blocking"]} | {i["code"] for i in report["warnings"]}


def test_guard_verb_escalation():
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, "协助团队完成后端接口开发")])
    report = RW.validate_plan(_plan_for("F-0001", "主导后端接口开发"), archive)
    assert "verb_escalation" in _codes(report)
    assert report["ok"] is False


def test_guard_invented_number():
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, "协助团队完成后端接口开发")])
    report = RW.validate_plan(_plan_for("F-0001", "完成后端接口开发，效率提升 300%"), archive)
    assert "invented_number" in _codes(report)


def test_guard_number_from_fact_is_allowed():
    content = "协助团队完成后端接口开发，响应时间从 800ms 降到 300ms"
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, content)])
    plan = _plan_for("F-0001", "配合团队完成后端接口开发，响应时间从 800ms 降到 300ms",
                     before=content)
    report = RW.validate_plan(plan, archive)
    assert "invented_number" not in _codes(report), report["blocking"]


def test_guard_team_to_individual():
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, "协助团队完成后端接口开发")])
    report = RW.validate_plan(_plan_for("F-0001", "独立完成后端接口开发"), archive)
    assert "team_to_individual" in _codes(report)


def test_guard_skill_level_unbacked():
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, "使用 Python 完成后端接口开发")])
    report = RW.validate_plan(_plan_for("F-0001", "熟练使用 Python 完成后端接口开发"), archive)
    assert "skill_level_unbacked" in _codes(report)


def test_guard_hedge_dropped():
    """删掉「协助」但保留同一动作，等于把协助写成本人主导。"""
    content = "协助搭建门店经营日报，维护 27 张固定报表"
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, content)])
    report = RW.validate_plan(
        _plan_for("F-0001", "搭建门店经营日报，维护 27 张固定报表", before=content), archive)
    assert "hedge_dropped" in _codes(report), report["warnings"]


def test_guard_hedge_kept_is_fine():
    content = "协助搭建门店经营日报，维护 27 张固定报表"
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, content)])
    report = RW.validate_plan(
        _plan_for("F-0001", "协助搭建门店经营日报，维护 27 张固定报表", before=content), archive)
    assert "hedge_dropped" not in _codes(report), report["warnings"]


def test_guard_unsupported_chinese_term():
    """事实里没有的中文领域词（如"专题分析"）应作为警告暴露出来。"""
    content = "协助搭建门店经营日报，维护 27 张固定报表"
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, content)])
    plan = RW.empty_plan("job-x")
    plan["target_title"].update({"text": "数据分析师", "fact_ids": ["F-0001"],
                                 "verification_status": "verified"})
    plan["summary"] = {"before": "", "after": "做指标口径与专题分析，维护固定报表",
                       "change_reason": "t", "requirement_ids": [],
                       "fact_ids": ["F-0001"], "verification_status": "verified"}
    plan["experiences"] = []
    report = RW.validate_plan(plan, archive)
    assert "unsupported_term" in _codes(report), report["warnings"]


def test_guard_grounded_summary_has_no_unsupported_term():
    content = "协助搭建门店经营日报，维护 27 张固定报表"
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, content)])
    plan = RW.empty_plan("job-x")
    plan["target_title"].update({"text": "数据分析师", "fact_ids": ["F-0001"],
                                 "verification_status": "verified"})
    plan["summary"] = {"before": "", "after": "协助搭建门店经营日报，维护 27 张固定报表",
                       "change_reason": "t", "requirement_ids": [],
                       "fact_ids": ["F-0001"], "verification_status": "verified"}
    plan["experiences"] = []
    report = RW.validate_plan(plan, archive)
    assert "unsupported_term" not in _codes(report), report["warnings"]


def test_guard_unsupported_term_in_rewritten_bullet():
    """before 非空时，新增的无依据中文词同样要告警（技能后缀就踩过这个坑）。"""
    content = "协助搭建门店经营日报，维护 27 张固定报表"
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, content)])
    plan = _plan_for("F-0001",
                     "协助搭建门店经营日报，维护 27 张固定报表（在经营报表与专题分析中使用）",
                     before=content)
    report = RW.validate_plan(plan, archive)
    assert "unsupported_term" in _codes(report), report["warnings"]


def test_guard_company_swap():
    archive = _archive_with([
        _fact("F-0001", F.KIND_EMPLOYMENT, "A 公司后端", company="A 公司", title="后端"),
        _fact("F-0002", F.KIND_EMPLOYMENT, "B 公司前端", company="B 公司", title="前端"),
    ])
    report = RW.validate_plan(
        _plan_for("F-0001", "在 B 公司负责后端接口开发"), archive)
    assert "identity_company_swap" in _codes(report)


def test_guard_degree_invented():
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, "协助团队完成后端接口开发")])
    report = RW.validate_plan(_plan_for("F-0001", "硕士学历，协助团队完成后端接口开发"), archive)
    assert "identity_degree_invented" in _codes(report)


def test_guard_no_fact_basis():
    archive = _archive_with([_fact("F-0001")])
    plan = _plan_for("F-0001", "协助团队完成后端接口开发")
    # an experience bullet inherits the experience's fact; a summary does not
    plan["summary"] = {"before": "", "after": "一名靠谱的后端工程师",
                       "change_reason": "test", "requirement_ids": [],
                       "fact_ids": [], "verification_status": "verified"}
    report = RW.validate_plan(plan, archive)
    assert "no_fact_basis" in _codes(report), report["blocking"]


def test_guard_unconfirmed_as_fact():
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, "协助开发", status=F.STATUS_UNCONFIRMED)])
    report = RW.validate_plan(_plan_for("F-0001", "协助团队完成后端接口开发"), archive)
    assert "unconfirmed_as_fact" in _codes(report)


def test_pending_items_not_written_into_resume():
    archive = _archive_with([_fact("F-0001", F.KIND_EMPLOYMENT, "协助开发", status=F.STATUS_UNCONFIRMED)])
    plan = _plan_for("F-0001", "协助团队完成后端接口开发")
    plan["experiences"][0]["bullets"][0]["verification_status"] = "pending_confirmation"
    doc = RW.build_resume_doc(archive, plan)
    assert doc["experiences"][0]["bullets"] == []
    assert len(doc["pending_items"]) == 1
    text = RW.render_text(doc)
    assert "协助团队" not in text


def test_build_resume_takes_identity_from_facts():
    archive = _archive_with([
        _fact("F-0001", F.KIND_IDENTITY, "张三"),
        _fact("F-0002", F.KIND_EMPLOYMENT, "星河数据 | 后端开发工程师 | 2021.03 - 2023.06",
              company="星河数据", title="后端开发工程师", start="2021-03", end="2023-06"),
    ])
    archive["person"]["name_fact_id"] = "F-0001"
    plan = _plan_for("F-0002", "协助团队完成后端接口开发")
    doc = RW.build_resume_doc(archive, plan)
    assert doc["name"] == "张三"
    assert doc["experiences"][0]["company"] == "星河数据"
    assert doc["experiences"][0]["title"] == "后端开发工程师"
    assert "2021-03" in doc["experiences"][0]["period"]


def test_traceability_flags_unknown_fact():
    archive = _archive_with([_fact("F-0001", F.KIND_IDENTITY, "张三")])
    doc = {"schema": RW.SCHEMA_DOC, "name": "张三", "name_fact_id": "F-0404",
           "experiences": [], "contacts": []}
    issues = RW.check_traceability(doc, archive)
    assert any(i["code"] == "untraceable_fact" for i in issues)


# ---------------------------------------------------------------------------
# jobs & screening
# ---------------------------------------------------------------------------


def test_company_normalization():
    assert J.normalize_company("示例科技有限公司") == "示例"
    assert J.normalize_company("示例科技（中国）有限公司") == "示例"


def test_job_sections_split():
    text = ("岗位职责\n1. 负责 A\n任职要求\n1. 本科以上\n2. 3 年经验\n加分项\n有 B 经验\n"
            "薪资 20-30K·13薪\n工作地点：上海")
    parsed = J.parse_sections(text)
    assert parsed["responsibilities"], parsed
    assert parsed["must_have"], parsed
    assert parsed["nice_to_have"], parsed
    assert parsed["city"] == "上海", parsed["city"]
    assert "20-30K" in parsed["salary_raw"], parsed["salary_raw"]


def test_identity_requires_header_not_body():
    """公司名只出现在正文深处（如"相似职位"）不能算身份核对通过。"""
    text = "数据分析师\n某招聘平台\n" + "\n".join(f"其他内容 {i}" for i in range(14)) + \
           "\n杭州云枢数据有限公司 正在招聘"
    result = J.check_identity("杭州云枢数据有限公司", "数据分析师", text)
    assert result["company_in_text"] is True          # 全文能找到
    assert result["company_in_header"] is False       # 但不在页头
    assert result["verified"] is False


def test_identity_header_pass():
    text = "数据分析师\n杭州云枢数据有限公司 · 杭州 · 18-25K\n岗位职责\n负责报表"
    result = J.check_identity("杭州云枢数据有限公司", "数据分析师", text, location="杭州")
    assert result["verified"] is True
    assert result["location_in_text"] is True


def test_identity_title_missing_from_header_fails():
    # 页头（前 12 行）只有公司，职位名出现在页面更靠后的"相似职位"区块
    filler = "\n".join(f"岗位职责第 {i} 条" for i in range(12))
    text = f"杭州云枢数据有限公司\n{filler}\n数据分析师（相似职位）"
    result = J.check_identity("杭州云枢数据有限公司", "数据分析师", text)
    assert result["title_in_header"] is False, result
    assert result["title_in_text"] is True
    assert result["verified"] is False


def test_identity_status_blocks_export_until_confirmed(tmp_path=None):
    job = {"job_id": "job-x", "identity": {"status": "pending_review"}}
    allowed, why = J.export_allowed(job)
    assert allowed is False and "待核对" in why

    job["identity"]["status"] = "user_confirmed"
    allowed, why = J.export_allowed(job)
    assert allowed is True and "未经系统核对" in why

    job["identity"]["status"] = "verified"
    assert J.export_allowed(job)[0] is True


def test_header_match_is_auxiliary_evidence_only():
    """页头匹配只是辅助证据：它不改写 identity 的语义，也不产生 human_authorised。"""
    text = "数据分析师\n杭州云枢数据有限公司 · 杭州\n岗位职责\n负责报表"
    result = J.check_identity("杭州云枢数据有限公司", "数据分析师", text)
    assert result["verified"] is True
    assert "human_authorised" not in result


def test_job_completeness_marks_partial():
    parsed = J.parse_sections("岗位职责\n负责 A")
    completeness = J.assess_completeness(parsed, J.SCOPE_PARTIAL)
    assert completeness["is_complete"] is False
    assert "read_scope_not_full" in completeness["missing"]


def test_screen_unknown_not_fail():
    job = {
        "job_id": "job-1", "company": "A", "title": "后端",
        "location": "", "sections": {"city": "", "work_mode": "未说明"},
        "salary": S.parse_salary("").to_dict(),
        "completeness": {"is_complete": False, "missing": ["salary_raw"]},
    }
    prefs = screen.empty_prefs()
    prefs["hard"].update({"cities": ["上海"], "work_modes": ["坐班"]})
    result = screen.screen_job(job, prefs)
    assert result["overall"] in {"conditional", "insufficient_info"}, result
    assert result["hard_failures"] == []


def test_screen_city_mismatch_fails():
    job = {
        "job_id": "job-1", "company": "A", "title": "后端", "location": "北京",
        "sections": {"city": "北京", "work_mode": "坐班"},
        "salary": S.parse_salary("20-30K").to_dict(),
        "completeness": {"is_complete": True, "missing": []},
    }
    prefs = screen.empty_prefs()
    prefs["hard"].update({"cities": ["上海"]})
    result = screen.screen_job(job, prefs)
    assert result["overall"] == "ineligible"
    assert "city" in result["hard_failures"]


# ---------------------------------------------------------------------------
# util & safety
# ---------------------------------------------------------------------------


def test_safe_component_strips_traversal():
    assert "/" not in util.safe_component("../../etc/passwd")
    assert ".." not in util.safe_component("..")
    assert util.safe_component("") == "untitled"


def test_safe_component_windows_reserved():
    assert util.safe_component("CON").startswith("_")
    assert util.safe_component("com1") != "com1"


def test_safe_component_keeps_chinese():
    assert "星河" in util.safe_component("星河数据科技有限公司")


def test_ensure_within_rejects_escape(tmp_path=None):
    base = ROOT / ".unit-base"
    base.mkdir(exist_ok=True)
    try:
        util.ensure_within(base, base / "ok.txt")
        try:
            util.ensure_within(base, base / ".." / "escape.txt")
        except SafetyViolation:
            pass
        else:
            raise AssertionError("应当拒绝越界路径")
    finally:
        base.rmdir()


def test_unique_path_never_overwrites():
    directory = ROOT / ".unit-out"
    directory.mkdir(exist_ok=True)
    try:
        first = util.unique_path(directory, "a", ".txt")
        first.write_text("x", encoding="utf-8")
        second = util.unique_path(directory, "a", ".txt")
        assert second != first
        assert second.name.startswith("a-")
    finally:
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()


def test_compare_key_and_loose_key():
    assert util.compare_key("20-30K · 13 薪") == util.compare_key("20-30K·13薪")
    assert util.loose_key("星河数据科技有限公司 | 后端") == util.loose_key("星河数据科技有限公司 后端")


# ---------------------------------------------------------------------------
# OCR interface & review
# ---------------------------------------------------------------------------


def test_ocr_select_raises_when_nothing_installed():
    try:
        provider = ocr_base.select("auto")
    except OcrUnavailable as exc:
        assert "OCR" in exc.message
        assert exc.hint
    else:
        # if a backend really is installed, it must at least describe itself
        assert provider.describe()["available"] is True


def test_fixture_provider_requires_opt_in():
    import os
    from jfa.extract.ocr.fixture import FixtureProvider

    previous = os.environ.pop("JFA_ALLOW_TEST_PROVIDER", None)
    try:
        assert FixtureProvider.available() is False
    finally:
        if previous is not None:
            os.environ["JFA_ALLOW_TEST_PROVIDER"] = previous


def test_review_highlight_categories():
    text = "张三 13800001111 星河数据科技有限公司 云帆大学 本科 2021.03 处理工单 60 余个"
    spans = review_mod.highlight(text)
    categories = {s["category"] for s in spans}
    assert {"contact", "company", "school", "degree", "date", "number"} <= categories, categories


def test_review_correction_preserves_original():
    builder = B.BlockBuilder("s1")
    block = builder.add("2022.13 某段文字", method=B.M_OCR, confidence=0.5, page=1)
    queue = review_mod.build_queue([block], source_id="s1", source_file="a.png")
    item = queue["items"][0]
    review_mod.apply_decision(queue, item["item_id"], action="correct",
                              corrected_text="2022.12 某段文字", note="月份更正")
    assert item["revisions"][0]["before"] == "2022.13 某段文字"
    blocks, log = review_mod.apply_corrections_to_blocks([block], queue)
    assert blocks[0].text == "2022.12 某段文字"
    assert "2022.13" in blocks[0].notes
    assert log[0]["action"] == "corrected"


def test_review_reject_removes_block():
    builder = B.BlockBuilder("s1")
    block = builder.add("页脚水印", method=B.M_OCR, confidence=0.9, page=1)
    queue = review_mod.build_queue([block], source_id="s1", source_file="a.png")
    item = queue["items"][0]
    review_mod.apply_decision(queue, item["item_id"], action="reject", note="水印")
    blocks, _ = review_mod.apply_corrections_to_blocks([block], queue)
    assert blocks[0].text == ""


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_schema_self_check():
    from jfa import schema as schema_mod

    checks = schema_mod.self_check()
    assert all(c["ok"] for c in checks), checks
    assert len(checks) == len(schema_mod.KNOWN)


def test_schema_rejects_bad_preferences():
    from jfa import schema as schema_mod

    bad = {"schema": "jfa.preferences/1", "hard": {"cities": "上海"}}
    problems = schema_mod.validate(bad, schema_mod.load("preferences"))
    assert any("cities" in p["path"] for p in problems), problems


def test_schema_accepts_fixture_prefs():
    from jfa import schema as schema_mod

    path = ROOT / "tests" / "fixtures" / "prefs.json"
    if not path.exists():
        return
    assert schema_mod.validate_file("preferences", path) == []


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

FIXTURE_JOB = ""


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures.append((name, str(exc) or "assert failed"))
            print(f"[FAIL] {name} — {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"[ERROR] {name} — {type(exc).__name__}: {exc}")
            if VERBOSE:
                traceback.print_exc()
        else:
            if VERBOSE:
                print(f"[PASS] {name}")
    print()
    print(f"单元测试 {len(tests)} 项：通过 {len(tests) - len(failures)}，失败 {len(failures)}")
    if failures:
        for name, detail in failures:
            print(f"  ✗ {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
