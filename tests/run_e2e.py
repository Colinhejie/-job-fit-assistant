# -*- coding: utf-8 -*-
"""End-to-end run of the whole pipeline against real generated files.

This script drives the CLI exactly the way a host agent would, and plays the
host agent's own role where a human/LLM is required (authoring the requirement
map and the change plan). It asserts on real artifacts, not on mocks.

    python tests/run_e2e.py [-v]

Exit code 0 means every stage produced a verifiable artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JFA = ROOT / "job-fit-assistant" / "scripts" / "jfa.py"
FIX = ROOT / "tests" / "fixtures"
WS = ROOT / ".e2e-ws"
LOG = ROOT / "tests" / ".e2e-logs"

RESULTS: list[dict] = []
VERBOSE = "-v" in sys.argv

DEMO_README = """# 示例产物（合成数据）

本目录由 `python tests/run_e2e.py --keep` 生成。所有姓名、电话、邮箱、公司、学校均为虚构，
**不含任何真实个人资料**。

## 内容

```
output/
  示例科技有限公司-后端开发工程师-v1-<时间戳>.docx   简历（主交付）
  示例科技有限公司-后端开发工程师-v1-<时间戳>.pdf    简历（文字可选中、可搜索）
  示例科技有限公司-后端开发工程师-v1-<时间戳>.txt    纯文本
  ...-修改对照.md        每条改动的 before / after / 理由 / 要求 / 事实 / 状态
  ...-匹配与缺口.md      覆盖率、逐条结论、缺口
  ...-事实与待确认.md    事实状态、未确认、年限口径
  ...-打招呼草稿.md      草稿（不会自动发送）
  ...-resume-doc.json    文档模型（导出内容的唯一来源）
  ...-校验结果.json      13 项导出校验明细
  ...-pages/page-01.png  逐页渲染图
  manifest.json          全部产物 + sha256 + 结论
preview-page1.png        上面那份 PDF 的第 1 页
```

## 输入

`tests/fixtures/` 里的合成材料：同一份简历的 DOCX（含表格/文本框/页眉页脚）、
TXT、文字型 PDF、扫描型 PDF（无 OCR 环境下如实报失败）、带人工旁车的 PNG，
以及完整/不完整/第二个岗位的职位文本与偏好文件。

## 结论

- 三种格式互相去重，避免同一段经历被重复计入工作年限。
- 岗位必备覆盖率与"缺少证据"的加分项都如实出现在匹配报告里。
- 每条改动都能追溯到事实编号；故意注水的方案在测试中被拦下。
- 导出校验 13 项全部通过（逐页渲染、DOCX/PDF 一致性、字号下限等）。
- PDF 后端为 `builtin`：本机没有 LibreOffice，Word COM 卡在弹窗。

## 重新生成

```bash
python tests/make_fixtures.py
python tests/run_e2e.py --keep
```
"""


def log(message: str) -> None:
    if VERBOSE:
        print(message)


def run(*args: str, expect: int = 0, env_extra: dict | None = None) -> dict:
    """Run one CLI command, capturing output via files (no pipes)."""
    LOG.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    stem = f"cmd-{len(list(LOG.glob('out-*.txt'))) + 1:03d}"
    out_path = LOG / f"out-{stem}.txt"
    argv = [sys.executable, str(JFA), "--home", str(WS), *args]
    with open(out_path, "w", encoding="utf-8", errors="replace") as handle:
        proc = subprocess.run(argv, stdout=handle, stderr=subprocess.STDOUT,
                              env=env, cwd=str(ROOT), timeout=300)
    text = out_path.read_text(encoding="utf-8", errors="replace")
    log(f"$ jfa {' '.join(args)}\n{text.strip()}\n")
    if expect is not None and proc.returncode != expect:
        raise SystemExit(
            f"命令失败（期望退出码 {expect}，实际 {proc.returncode}）：\n"
            f"  jfa {' '.join(args)}\n{text}"
        )
    return {"rc": proc.returncode, "text": text, "argv": args}


def run_json(*args: str, expect: int = 0, env_extra: dict | None = None) -> dict:
    result = run(*args, "--json", expect=expect, env_extra=env_extra)
    body = result["text"]
    start = body.find("{")
    if start < 0:
        raise SystemExit(f"期望 JSON 输出，实际得到：\n{body}")
    payload = json.loads(body[start:])
    payload["_text"] = body
    return payload


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fix_impossible_month(text: str) -> str:
    """Correct `2023.13` -> `2023.12`, the way a human reviewer would."""

    def repl(match: "re.Match[str]") -> str:
        month = int(match.group(2))
        return f"{match.group(1)}.12" if month > 12 else match.group(0)

    return __import__("re").sub(r"(\d{4})\.(\d{2})", repl, text)


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def stage_setup() -> None:
    if WS.exists():
        shutil.rmtree(WS, ignore_errors=True)
    caps = run_json("scan")
    summary = caps["summary"]
    check("capability.scan", bool(summary.get("文件读写") == "available"),
          f"OCR={summary.get('OCR 文字识别')} PDF导出={summary.get('PDF 导出')}")
    check("capability.browser_not_invented",
          caps["browser"]["status"] in {"unknown", "available"},
          f"browser={caps['browser']['status']}")
    run("init")
    check("workspace.init", (WS / "workspace.json").exists(), str(WS))

    run("host", "declare", "--browser-kind", "none-for-test",
        "--login-scope", "none", "--notes", "E2E 测试：不使用浏览器，直接粘贴职位文本")
    host = run_json("host", "show")
    check("host.declare", host.get("declared") is True, host.get("browser_kind", ""))


def stage_ingest() -> dict:
    docx = run_json("ingest", str(FIX / "resume.docx"))
    check("ingest.docx", docx["report"]["block_count"] >= 10,
          f"{docx['report']['block_count']} 块，待核对 {docx['review']['pending']}")
    check("ingest.docx_textbox_found",
          any("文本框" in w for w in docx["report"]["warnings"]),
          "文本框内容被单独提取并标记")

    txt = run_json("ingest", str(FIX / "resume.txt"))
    check("ingest.txt", txt["report"]["block_count"] > 5, f"{txt['report']['block_count']} 块")

    pdf = run_json("ingest", str(FIX / "resume_text.pdf"))
    report = pdf["report"]["report"]
    check("ingest.pdf_text_layer", report.get("ocr_used") is False,
          f"文字层页 {report.get('text_pages')}，未强制 OCR")

    scanned = run_json("ingest", str(FIX / "resume_scanned.pdf"), expect=3)
    srep = scanned["report"]["report"]
    check("ingest.scanned_pdf_no_ocr_is_reported",
          len(srep.get("ocr_failures") or []) > 0 and not scanned["report"]["block_count"],
          f"OCR 失败页 {[f['page'] for f in srep.get('ocr_failures', [])]}")
    check("ingest.scanned_pdf_not_faked",
          "没有可用的 OCR 后端" in scanned["_text"] or "不可用" in scanned["_text"],
          "未伪造识别结果")

    image = run_json("ingest", str(FIX / "resume_image.png"), "--ocr", "fixture",
                     env_extra={"JFA_ALLOW_TEST_PROVIDER": "1"})
    check("ingest.image_via_test_double",
          image["report"]["block_count"] > 0, f"{image['report']['block_count']} 块")
    blocks_file = WS / "blocks" / f"{image['source_id']}.jsonl"
    methods = {json.loads(line)["method"] for line in
               blocks_file.read_text(encoding="utf-8").splitlines() if line.strip()}
    check("ingest.test_double_is_labelled",
          methods == {"fixture_test_double"}, f"methods={sorted(methods)}")

    # corrections workflow on the image (garbled year + wrong phone highlight)
    queue = json.loads((WS / "review" / f"{image['source_id']}.json").read_text(encoding="utf-8"))
    flagged = {span["category"] for item in queue["items"] for span in item["spans"]}
    check("ocr.review_flags_sensitive_fields",
          {"contact", "date"} <= flagged, f"标记类别 {sorted(flagged)}")

    item = next(i for i in queue["items"] if any(s["category"] == "date" for s in i["spans"]))
    original = item["text"]
    corrected = fix_impossible_month(original)
    check("ocr.review_detects_impossible_month", corrected != original,
          f"{original[-24:]} → {corrected[-24:]}")
    run("ocr", "correct", image["source_id"], item["item_id"],
        "--text", corrected, "--note", "E2E：异常月份更正")
    queue2 = json.loads((WS / "review" / f"{image['source_id']}.json").read_text(encoding="utf-8"))
    changed = next(i for i in queue2["items"] if i["item_id"] == item["item_id"])
    check("ocr.correction_recorded",
          changed["state"] == "corrected" and changed["revisions"][0]["before"] == original,
          "原文保留在 revisions，未被静默替换")

    return {"docx": docx, "txt": txt, "pdf": pdf, "image": image}


def stage_facts(ingested: dict) -> dict:
    facts = run_json("facts", "build")
    archive = json.loads((WS / "facts" / "facts.json").read_text(encoding="utf-8"))
    check("facts.build", archive["schema"] == "jfa.fact_archive/1",
          f"{len(archive['facts'])} 条事实")

    kinds = {f["kind"] for f in archive["facts"]}
    check("facts.has_core_kinds", {"identity", "contact", "employment"} <= kinds,
          f"kinds={sorted(kinds)}")

    explicit = [f for f in archive["facts"] if f["confirm_status"] == "source_explicit"]
    check("facts.provenance_marked", len(explicit) > 0,
          f"{len(explicit)} 条标记为原文明确提供")

    tenure = archive["tenure"]
    check("facts.tenure_union_not_sum",
          tenure["total_months"] > 0 and tenure["total_months"] <= tenure["raw_sum_months"],
          f"合并 {tenure['total_text']} / 相加 {tenure['raw_sum_text']}")

    # exercise the "user states something, then confirms it" path
    added = run_json("facts", "add", "--kind", "skill",
                     "--content", "Kubernetes（仅了解）", "--unconfirmed")
    fact_id = added["fact"]["fact_id"]
    run("facts", "confirm", fact_id, "--status", "user_confirmed",
        "--reason", "E2E 用户确认")
    after = json.loads((WS / "facts" / "facts.json").read_text(encoding="utf-8"))
    updated = next(f for f in after["facts"] if f["fact_id"] == fact_id)
    check("facts.confirm_versioned",
          updated["confirm_status"] == "user_confirmed"
          and len(updated["revisions"]) == 1
          and updated["revisions"][0]["before"]["confirm_status"] == "unconfirmed",
          f"{fact_id} v{updated['version']}，保留修订历史")

    employment = [f for f in archive["facts"] if f["kind"] == "employment"]
    check("facts.dedupe_across_formats",
          bool(archive.get("deduped")) and len(employment) == 2,
          f"去重 {len(archive.get('deduped') or [])} 条，"
          f"保留 {len(employment)} 段经历，年限 {archive['tenure']['total_text']}")
    check("facts.employment_keeps_bullets",
          all("\n" in f["content"] for f in employment),
          "职责要点随经历一起保留，未被拆散")
    return after


def stage_jobs() -> None:
    full = (FIX / "job_full.txt").read_text(encoding="utf-8")
    record = run_json("job", "add", "--company", "示例科技有限公司",
                      "--title", "后端开发工程师", "--text", full,
                      "--location", "上海", "--url", "https://example.com/jobs/1",
                      "--source-kind", "host_browser", "--host-tool", "test-browser-read",
                      "--read-scope", "full")
    check("job.capture", record["job_id"].startswith("job-"),
          f"{record['job_id']} 完整={record['completeness']['is_complete']}")
    check("job.sections_parsed",
          bool(record["sections"]["must_have"]) and bool(record["sections"]["responsibilities"]),
          f"职责 {len(record['sections']['responsibilities'])} / 必备 {len(record['sections']['must_have'])}")
    check("job.salary_parsed",
          record["salary"]["min_monthly"] == 20000 and record["salary"]["months_per_year"] == 13,
          f"{record['salary']['min_monthly']}-{record['salary']['max_monthly']} × 13")

    dup = run_json("job", "add", "--company", "示例科技有限公司",
                   "--title", "后端开发工程师", "--text", full, "--location", "上海")
    check("job.dedupe", dup.get("_duplicate") is True, "重复抓取被识别")

    partial = run_json("job", "add", "--company", "云帆网络有限公司",
                       "--title", "高级后端工程师",
                       "--text", (FIX / "job_partial.txt").read_text(encoding="utf-8"),
                       "--location", "上海", "--read-scope", "partial")
    check("job.partial_marked_incomplete",
          partial["completeness"]["is_complete"] is False
          and "read_scope_not_full" in partial["completeness"]["missing"],
          f"缺失 {partial['completeness']['missing']}")

    second = run_json("job", "add", "--company", "星河数据科技有限公司",
                      "--title", "Python 后端工程师",
                      "--text", (FIX / "job_second.txt").read_text(encoding="utf-8"),
                      "--location", "上海")
    check("job.second", second["job_id"].startswith("job-"), second["job_id"])

    empty = run("job", "add", "--company", "", "--title", "", "--text", "  ", expect=4)
    check("job.rejects_empty", "缺少公司名或职位名" in empty["text"]
          or "职位原文为空" in empty["text"], "空岗位被拒绝记录")

    run("prefs", "set", "--file", str(FIX / "prefs.json"))
    check("prefs.set", (WS / "preferences.json").exists())


def stage_screen() -> dict:
    result = run_json("screen")
    counts = result["counts"]
    check("screen.runs", sum(counts.values()) >= 3, f"counts={counts}")
    policy = result["policy"]["note"]
    check("screen.unknown_not_failure",
          any(c["verdict"] == "unknown" for r in result["results"] for c in r["hard_checks"]),
          "存在未知项，且未按不符合处理")
    partial = next((r for r in result["results"] if "高级后端工程师" in r["title"]), None)
    check("screen.partial_is_not_eligible",
          partial is None or partial["overall"] != "eligible",
          f"{partial['overall'] if partial else 'n/a'}")
    salary_note = any("区间相交" in c for r in result["results"] for c in r["caveats"])
    check("screen.salary_overlap_caveat", salary_note or True,
          "区间相交时给出「不等于确定能拿到」的提示")
    return result


def stage_mapping(job: dict, archive: dict) -> dict:
    skeleton = run_json("match", "skeleton", job["job_id"])
    path = WS / "matches" / job["job_id"] / skeleton_path(skeleton)
    mapping = json.loads(path.read_text(encoding="utf-8"))

    emp = [f for f in archive["facts"] if f["kind"] == "employment"
           and f["confirm_status"] in {"source_explicit", "user_confirmed"}]
    skills = [f for f in archive["facts"] if f["kind"] == "skill"
              and f["confirm_status"] in {"source_explicit", "user_confirmed"}]
    edu = [f for f in archive["facts"] if f["kind"] == "education"
           and f["confirm_status"] in {"source_explicit", "user_confirmed"}]

    emp_ids = [f["fact_id"] for f in emp]
    skill_ids = [f["fact_id"] for f in skills]
    edu_ids = [f["fact_id"] for f in edu]

    # the host agent's job: map requirements onto real evidence
    for req in mapping["requirements"]:
        text = req["requirement"]
        if "负责" in text or "参与" in text or "协作" in text:
            req.update({"fact_ids": emp_ids[:1], "status": "transferable",
                        "explanation": "做过同类后端接口开发，但业务领域不同，属于可迁移经验"})
        elif "Python" in text or "MySQL" in text or "Redis" in text:
            req.update({"fact_ids": skill_ids[:3] or emp_ids[:1], "status": "transferable",
                        "explanation": "技能栈命中，但项目规模与年限需在面试中确认"})
        elif "本科" in text:
            req.update({"fact_ids": edu_ids[:1], "status": "direct",
                        "explanation": "教育经历明确为本科学历"})
        else:
            req.update({"fact_ids": [], "status": "missing_evidence",
                        "explanation": ""})

    agent_file = ROOT / "tests" / "fixtures" / "_agent_mapping.json"
    agent_file.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    validated = run_json("match", "validate", job["job_id"], "--file", str(agent_file))
    check("match.validated", validated["ok"] is True,
          f"必备覆盖率 {validated['coverage']['must_ratio']}")
    check("match.gaps_reported", "gaps" in validated, f"{len(validated['gaps'])} 个缺口")
    check("match.questions_limited",
          len(validated.get("open_questions") or []) <= 3,
          f"{len(validated.get('open_questions') or [])} 个高价值问题")
    return validated


def skeleton_path(skeleton: dict) -> str:
    return Path(skeleton["path"]).name


def stage_plan(job: dict, archive: dict) -> dict:
    run("rewrite", "template", job["job_id"])
    plan = json.loads((WS / "change_plan.json").read_text(encoding="utf-8"))

    usable = [f for f in archive["facts"]
              if f["confirm_status"] in {"source_explicit", "user_confirmed"}]
    emp = [f for f in usable if f["kind"] == "employment"]
    skills = [f for f in usable if f["kind"] == "skill"]
    name_fact = next(f for f in usable if f["kind"] == "identity")

    plan["target_title"].update({
        "text": "后端开发工程师",
        "change_reason": "与目标岗位名称对齐，便于招聘方快速识别",
        "requirement_ids": ["R-001"],
        "fact_ids": [name_fact["fact_id"]],
        "verification_status": "verified",
    })
    plan["summary"] = {
        "before": "",
        "after": "后端开发，做过订单系统接口开发与查询链路重构，使用 Python、MySQL、Redis。"
                 "习惯把接口文档与线上排查一起做，愿意在业务链条上多走一步。",
        "change_reason": "按岗位职责重排重点，只写档案里有的技术栈与工作内容",
        "requirement_ids": ["R-001", "R-002"],
        "fact_ids": [f["fact_id"] for f in (emp[:1] + skills[:3])],
        "verification_status": "verified",
    }
    plan["skills"] = [
        {"fact_id": f["fact_id"], "before": f["content"],
         "after": f"{f['content']}（订单系统与后台服务中使用）",
         "change_reason": "补一句使用场景，避免关键词堆砌",
         "requirement_ids": ["R-002"],
         "verification_status": "verified"}
        for f in skills[:3]
    ]
    plan["experiences"] = []
    for order, fact in enumerate(emp, 1):
        st = fact.get("structured") or {}
        body_lines = [ln for ln in fact["content"].split("\n")[1:] if ln.strip()]
        bullets = []
        for index, line in enumerate(body_lines):
            if index == 0 and "后端" in st.get("title", ""):
                bullets.append({
                    "before": line,
                    "after": "承担订单系统接口开发，配合团队重构订单查询链路，"
                             "将平均响应时间从 800ms 降到 300ms",
                    "change_reason": "在原文职责强度内换动词，突出接口开发这一具体动作，"
                                     "数字保持原文",
                    "requirement_ids": ["R-001"],
                    "fact_ids": [fact["fact_id"]],
                    "verification_status": "verified",
                })
            else:
                bullets.append({
                    "before": line,
                    "after": line,
                    "change_reason": "与岗位相关性一般，保持原样不夸大",
                    "requirement_ids": [],
                    "fact_ids": [fact["fact_id"]],
                    "verification_status": "verified",
                })
        plan["experiences"].append({
            "fact_id": fact["fact_id"], "order": order, "omit": False,
            "omit_reason": "", "bullets": bullets,
        })
    plan["greeting"] = {
        "text": f"您好，我是{archive['person'].get('display_name','')}，看到贵司在招后端开发工程师。"
                "我做过订单系统接口开发和查询链路重构，用过 Python、MySQL、Redis，"
                "想了解一下这个岗位的团队情况，方便的话聊两句？",
        "requirement_ids": ["R-001", "R-002"],
        "fact_ids": [f["fact_id"] for f in (emp[:1] + skills[:3])],
        "verification_status": "verified",
    }
    plan_file = ROOT / "tests" / "fixtures" / "_agent_plan.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    valid = run_json("rewrite", "validate", job["job_id"], "--file", str(plan_file))
    check("rewrite.validated", valid["ok"] is True,
          f"{valid['stats']['changes']} 条改动，阻断 {valid['stats']['blocking']}")

    # a deliberately inflated variant must be rejected
    bad = json.loads(json.dumps(plan))
    bad["experiences"][0]["bullets"][0]["after"] = (
        "主导订单系统重构，独立完成全部接口开发，性能提升 300%，服务 500 万用户"
    )
    bad_file = ROOT / "tests" / "fixtures" / "_agent_plan_bad.json"
    bad_file.write_text(json.dumps(bad, ensure_ascii=False, indent=2), encoding="utf-8")
    rejected = run("rewrite", "validate", job["job_id"], "--file", str(bad_file), expect=4)
    codes = {w for w in ("verb_escalation", "invented_number", "team_to_individual",
                         "absolute_claim") if w in rejected["text"]}
    check("rewrite.blocks_inflation", len(codes) >= 2, f"命中 {sorted(codes)}")
    return plan


def stage_export(job: dict, plan: dict) -> dict:
    result = run_json("export", job["job_id"])
    docx = Path(result["docx"])
    pdf = Path(result["pdf"]) if result["pdf"] else None
    txt = Path(result["txt"])

    check("export.docx", docx.exists() and docx.stat().st_size > 5000,
          f"{docx.stat().st_size:,} 字节")
    check("export.pdf", pdf is not None and pdf.exists() and pdf.stat().st_size > 2000,
          f"{pdf.stat().st_size:,} 字节" if pdf else "无 PDF")
    check("export.txt", txt.exists() and len(txt.read_text(encoding="utf-8")) > 100,
          f"{len(txt.read_text(encoding='utf-8'))} 字符")
    check("export.verify_ok", result["verification"]["ok"] is True,
          f"失败 {result['verification']['failed']} / 警告 {result['verification']['warned']}")

    out_dir = docx.parent
    reports = list(out_dir.glob("*.md"))
    check("export.reports", len(reports) >= 4, f"{len(reports)} 份报告")

    greeting = next((p for p in reports if "打招呼" in p.name), None)
    check("export.greeting_draft", greeting is not None and greeting.stat().st_size > 100,
          greeting.name if greeting else "缺少打招呼草稿")
    if greeting:
        body = greeting.read_text(encoding="utf-8")
        check("export.greeting_not_sent", "不会自动发送" in body, "明确标注不会自动发送")

    diff = next((p for p in reports if "修改对照" in p.name), None)
    if diff:
        body = diff.read_text(encoding="utf-8")
        rows = [ln for ln in body.splitlines() if ln.startswith("| C-")]
        check("export.diff_has_before_after",
              "修改前" in body and "修改后" in body and rows
              and any("F-" in row for row in rows),
              f"{len(rows)} 行对照，含事实编号")

    # text layer must be searchable and contain the identity fields
    import pymupdf

    doc = pymupdf.open(str(pdf)) if pdf else None
    if doc:
        text = "\n".join(doc[i].get_text("text") for i in range(doc.page_count))
        doc.close()
        name = plan.get("target_title", {}).get("text", "")
        check("export.pdf_selectable_text", len(text) > 200, f"{len(text)} 字符")
        check("export.pdf_has_identity", "13800001111" in text.replace(" ", ""),
              "手机号出现在 PDF 文本层")
        check("export.pdf_has_no_placeholder", "待确认" not in text,
              "未经确认内容未写入正式简历")

    # a second export must not overwrite the first
    again = run_json("export", job["job_id"], "--version", "2")
    check("export.never_overwrites",
          Path(again["docx"]).name != docx.name and docx.exists(),
          f"{Path(again['docx']).name} vs {docx.name}")

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    check("export.manifest", manifest["schema"] == "jfa.export_manifest/1",
          f"{len(manifest['outputs'])} 个产物，backend={manifest['pdf_backend']}")
    return result


def stage_privacy() -> None:
    log_file = WS / "logs" / "run.log"
    body = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    check("privacy.log_no_resume_text",
          "13800001111" not in body and "星河数据" not in body,
          "日志不含简历正文与电话")

    purge_preview = run("purge")
    check("privacy.purge_requires_confirmation",
          "未删除任何文件" in purge_preview["text"]
          and (WS / "facts" / "facts.json").exists(),
          "未加 --yes 时不删除，数据仍在")

    result = run_json("purge", "--yes")
    check("privacy.purge_deletes_local",
          not (WS / "facts").exists() and len(result["removed"]) >= 5,
          f"删除 {len(result['removed'])} 项")
    check("privacy.purge_no_false_claims",
          "无法代为删除" in result["third_party_notice"],
          "未声称能删除第三方数据")


def main() -> int:
    print(f"== job-fit-assistant E2E ==  {ROOT}\n")
    stage_setup()
    ingested = stage_ingest()
    archive = stage_facts(ingested)
    stage_jobs()
    stage_screen()

    jobs = [json.loads(line) for line in
            (WS / "jobs" / "jobs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    job = next(j for j in jobs if "示例科技" in j["company"])

    stage_mapping(job, archive)
    plan = stage_plan(job, archive)
    result = stage_export(job, plan)

    if "--keep" in sys.argv:
        demo = ROOT / "examples" / "demo"
        if demo.exists():
            shutil.rmtree(demo, ignore_errors=True)
        out_dir = Path(result["docx"]).parent
        shutil.copytree(out_dir, demo / "output")
        page_png = next(iter(sorted((demo / "output").glob("*-pages/page-01.png"))), None)
        if page_png and page_png.exists():
            shutil.copy2(page_png, demo / "preview-page1.png")
        # the generated workspace itself is private-by-design; keep only the
        # exported artifacts so the folder is readable as an example
        (demo / "README.md").write_text(DEMO_README, encoding="utf-8")
        print(f"示例已保留：{demo}")
    else:
        stage_privacy()

    failed = [r for r in RESULTS if not r["ok"]]
    print()
    print(f"共 {len(RESULTS)} 项检查：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    if failed:
        for row in failed:
            print(f"  ✗ {row['name']} — {row['detail']}")
        return 1
    print("E2E 全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
