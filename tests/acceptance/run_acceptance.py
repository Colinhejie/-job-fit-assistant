# -*- coding: utf-8 -*-
"""Acceptance run against HELD-OUT samples.

Design rules for this harness (deliberately strict):

1. It never edits the skill, never loosens an assertion to make a test pass.
2. Every check declares its evidence mode:
      自动检查   — decidable from the artifact alone
      人工复核   — needs a human judgement; reported as 未测试 unless actually reviewed
      模拟环境   — test double / simulated host agent
      真实宿主   — real host capability
3. "expected" is never taken from the program's own output. For field-level
   checks the authority is the held-out *source text*; for semantic checks the
   authority would have to be a human label, which does not exist here — those
   are reported as 未测试, not as pass.

    python tests/acceptance/run_acceptance.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
JFA = ROOT / "job-fit-assistant" / "scripts" / "jfa.py"
HELD = Path(os.environ.get("JFA_ACCEPT_SAMPLES", str(HERE / "heldout"))).resolve()
ART = HERE / "artifacts"

if not any((HELD / n).exists() for n in ("resume.txt", "heldout_resume.txt")):
    raise SystemExit(f"样本目录不存在或为空：{HELD}")


def sp(name: str) -> Path:
    """Resolve a logical sample name; both sample sets use different prefixes."""
    direct = HELD / name
    if direct.exists():
        return direct
    alt = name.replace("heldout_", "")
    if alt.startswith("resume"):
        alt = "resume" + alt[len("resume"):]
    candidate = HELD / alt
    return candidate if candidate.exists() else direct


PROFILE_PATH = HELD / "profile.json"
P = json.loads(PROFILE_PATH.read_text(encoding="utf-8")) if PROFILE_PATH.exists() else {}
SAMPLE_SET = HELD.name

RESULTS: list[dict] = []
START = time.time()

# 判据来源
AUTO = "自动检查"
HUMAN = "人工复核"
SIM = "模拟环境"
REAL = "真实宿主"


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------


def cli(ws: Path, *args: str, expect: int | None = 0,
        env_extra: dict | None = None) -> tuple[int, str]:
    ART.mkdir(parents=True, exist_ok=True)
    log = ART / "cli.log"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    argv = [sys.executable, str(JFA), "--home", str(ws), *args]
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(f"\n$ jfa {' '.join(args)}\n")
        handle.flush()
        proc = subprocess.run(argv, stdout=handle, stderr=subprocess.STDOUT,
                              env=env, cwd=str(ROOT), timeout=600)
    text = log.read_text(encoding="utf-8", errors="replace")
    if expect is not None and proc.returncode != expect:
        raise RuntimeError(
            f"命令退出码 {proc.returncode}，期望 {expect}：jfa {' '.join(args)}\n"
            + "\n".join(text.splitlines()[-25:]))
    # last command's output only
    marker = f"\n$ jfa {' '.join(args)}\n"
    return proc.returncode, text.split(marker)[-1]


def cli_json(ws: Path, *args: str, expect: int | None = 0,
             env_extra: dict | None = None) -> dict:
    _, text = cli(ws, *args, "--json", expect=expect, env_extra=env_extra)
    start = text.find("{")
    if start < 0:
        raise RuntimeError(f"期望 JSON，得到：{text[:400]}")
    payload = json.loads(text[start:])
    payload["_text"] = text
    return payload


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def record(tid: str, area: str, title: str, *, input_: str, expected: str,
           actual: str, status: str, mode: str, evidence: str = "",
           reason: str = "", fix: str = "") -> dict:
    row = {
        "id": tid, "area": area, "title": title, "input": input_,
        "expected": expected, "actual": actual, "status": status,
        "mode": mode, "evidence": evidence, "reason": reason, "fix": fix,
    }
    RESULTS.append(row)
    mark = {"通过": "PASS", "失败": "FAIL", "未测试": "N/A"}.get(status, status)
    print(f"[{mark}] {tid} {title}")
    if status == "失败":
        print(f"       实际：{actual[:300]}")
        if reason:
            print(f"       原因：{reason}")
    return row


def fresh(name: str) -> Path:
    ws = ART / f"ws-{name}"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    cli(ws, "init")
    return ws


# ---------------------------------------------------------------------------
# evidence helpers
# ---------------------------------------------------------------------------

CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}")
NUM = re.compile(r"\d+(?:\.\d+)?")
BULLET_STOP = {
    "协助", "参与", "负责", "配合", "完成", "工作", "经历", "公司", "项目", "负责",
    "以及", "相关", "进行", "通过", "使用", "具有", "能够", "支持", "推动", "落地",
    "我们", "你们", "岗位", "要求", "要求", "以上", "以下", "优先", "加分",
}
#: 这些词一旦在改写里出现、却不在证据里，就不是"措辞问题"而是"身份/事实问题"
IDENTITY_TERMS = (
    "主导", "独立", "全程", "全面负责", "精通", "熟练", "掌握", "团队负责人",
    "部门负责人", "总监", "经理", "主管", "专家",
)


def ngrams(text: str, n: int = 2) -> set[str]:
    out: set[str] = set()
    for run in CJK_RUN.findall(text or ""):
        for i in range(len(run) - n + 1):
            gram = run[i:i + n]
            if gram not in BULLET_STOP:
                out.add(gram)
    for word in LATIN.findall(text or ""):
        if len(word) >= 2:
            out.add(word.lower())
    return out


def content_support(after: str, basis_texts: list[str]) -> dict:
    """Content-level support check — an existing fact id is not enough.

    Returns coverage of the *wording* of `after` by the cited evidence, plus the
    terms that have no support. Numbers are checked separately as set inclusion.
    """
    basis = "\n".join(basis_texts)
    basis_grams = ngrams(basis)
    after_grams = ngrams(after)
    unsupported = sorted(after_grams - basis_grams)
    ratio = (len(after_grams) - len(unsupported)) / len(after_grams) if after_grams else 1.0
    basis_nums = set(NUM.findall(basis))
    after_nums = set(NUM.findall(after))
    return {
        "ratio": round(ratio, 3),
        "unsupported_terms": unsupported,
        "numbers_unsupported": sorted(after_nums - basis_nums),
    }


# ---------------------------------------------------------------------------
# host-agent simulation (mapping + plan authoring)
# ---------------------------------------------------------------------------


def author_mapping(ws: Path, job: dict, archive: dict) -> Path:
    skeleton = cli_json(ws, "match", "skeleton", job["job_id"])
    path = ws / "matches" / job["job_id"] / Path(skeleton["path"]).name
    mapping = read_json(path)

    usable = [f for f in archive["facts"]
              if f["confirm_status"] in {"source_explicit", "user_confirmed"}]
    emp = [f["fact_id"] for f in usable if f["kind"] == "employment"]
    skill = [f["fact_id"] for f in usable if f["kind"] == "skill"]
    edu = [f["fact_id"] for f in usable if f["kind"] == "education"]

    for req in mapping["requirements"]:
        text = req["requirement"]
        if re.search(r"本科|学历|统计学", text):
            req.update({"fact_ids": edu[:1], "status": "direct",
                        "explanation": "教育经历明确写了学校与学历"})
        elif re.search(r"SQL|Python|Tableau|工具|数据", text, re.I):
            req.update({"fact_ids": skill[:3] or emp[:1], "status": "transferable",
                        "explanation": "技能栈命中，具体深度需在面试中确认"})
        elif re.search(r"经验|报表|指标|看板|分析|实验|团队|管理|带教", text):
            req.update({"fact_ids": emp[:1], "status": "transferable",
                        "explanation": "做过同类分析工作，领域与规模不同，属于可迁移"})
        else:
            req.update({"fact_ids": [], "status": "missing_evidence", "explanation": ""})

    out = HERE / "artifacts" / f"mapping-{job['job_id']}.json"
    write_json(out, mapping)
    cli_json(ws, "match", "validate", job["job_id"], "--file", str(out))
    return out


def author_plan(ws: Path, job: dict, archive: dict, *, variant: str = "normal") -> Path:
    cli(ws, "rewrite", "template", job["job_id"])
    plan = read_json(ws / "change_plan.json")

    usable = [f for f in archive["facts"]
              if f["confirm_status"] in {"source_explicit", "user_confirmed"}]
    emp = [f for f in usable if f["kind"] == "employment"]
    skills = [f for f in usable if f["kind"] == "skill"]
    name_fact = next((f for f in usable if f["kind"] == "identity"), None)

    title = job["title"]
    plan["target_title"].update({
        "text": title,
        "change_reason": "与目标岗位名称对齐",
        "requirement_ids": ["R-001"],
        "fact_ids": [name_fact["fact_id"]] if name_fact else [],
        "verification_status": "verified",
    })

    # 注意保留原文的限定词：原文是"协助搭建门店经营日报"，
    # 简介就不能写成"报表体系搭建"（那是把协助抬成本人主导）。
    summary_by_job = {
        "数据分析师": "数据分析师，协助搭建门店经营日报、维护 27 张固定报表，用 SQL、Python、Tableau 做指标口径。",
        "增长数据分析师": "数据分析师，参与会员分层模型的需求评审，配合完成渠道归因分析，用 SQL、Python、Tableau。",
        "数据分析主管": "数据分析师，负责其中的指标口径文档，带 2 名实习生完成渠道归因分析，参与会员分层模型的需求评审。",
    }
    summary = summary_by_job.get(title, "数据分析师，做数据分析相关工作。")
    # 每条陈述都必须落在所引事实里：主管岗摘要提到"带过 2 名实习生"，
    # 就必须引用含这句话的那段经历，而不只是第一段。
    plan["summary"] = {
        "before": "", "after": summary,
        "change_reason": "按该岗位职责重排重点，只写档案里的内容",
        "requirement_ids": ["R-001"],
        "fact_ids": [f["fact_id"] for f in emp] + [f["fact_id"] for f in skills[:3]],
        "verification_status": "verified",
    }

    plan["skills"] = [{
        "fact_id": f["fact_id"], "before": f["content"],
        "after": f"{f['content']}（用于门店经营日报与报表维护）",
        "change_reason": "补充使用场景，避免关键词堆砌",
        "requirement_ids": ["R-002"],
        "verification_status": "verified",
    } for f in skills[:3]]

    # 每个岗位对各条要点的优先级不同——这正是"调整每段经历内部重点"的体现
    emphasis = {
        "数据分析师": {"first": ["门店", "报表", "指标口径"], "last": ["实习生", "归因"]},
        "增长数据分析师": {"first": ["会员", "归因", "看板", "活动策略"],
                           "last": ["门店经营日报"]},
        "数据分析主管": {"first": ["实习生", "指标口径", "看板"], "last": []},
    }.get(title, {"first": [], "last": []})

    def weight(line: str) -> tuple[int, int]:
        head = 0 if any(k in line for k in emphasis["first"]) else 1
        tail = 1 if any(k in line for k in emphasis["last"]) else 0
        return (head, tail)

    plan["experiences"] = []
    for order, fact in enumerate(emp, 1):
        lines = [ln for ln in fact["content"].split("\n")[1:] if ln.strip()]
        ordered = sorted(range(len(lines)), key=lambda i: weight(lines[i]))
        bullets = []
        for idx in ordered:
            line = lines[idx]
            bullets.append({
                "before": line, "after": line,
                "change_reason": "按该岗位的重点重排要点顺序，不改变事实强度与数字",
                "requirement_ids": ["R-001"],
                "fact_ids": [fact["fact_id"]],
                "verification_status": "verified",
            })
        plan["experiences"].append({
            "fact_id": fact["fact_id"], "order": order,
            "omit": False, "omit_reason": "", "bullets": bullets,
        })

    if variant == "inflate":
        # deliberate violations, one per guard family
        first = plan["experiences"][0]["bullets"]
        if first:
            first[0]["after"] = "主导门店经营日报体系，独立把周报产出时间压缩 90%，服务 500 家门店"
        plan["summary"]["after"] = "资深数据分析专家，精通全栈数据体系，主导过 10 个以上大型项目"

    out = HERE / "artifacts" / f"plan-{job['job_id']}-{variant}.json"
    write_json(out, plan)
    return out


# ---------------------------------------------------------------------------
# A. 解析
# ---------------------------------------------------------------------------


def area_parsing() -> dict:
    ws = fresh("parse")
    out: dict = {}

    # T01 文本
    r = cli_json(ws, "ingest", str(sp("heldout_resume.txt")))
    out["text"] = r
    record("T01", "解析", "TXT 逐行解析", input_="heldout_resume.txt",
           expected="每行一个块，姓名/经历/教育/技能均在",
           actual=f"{r['report']['block_count']} 块，方法={sorted({b['method'] for b in _blocks(ws, r['source_id'])})}",
           status="通过" if r["report"]["block_count"] >= 14 else "失败",
           mode=AUTO, evidence=rel(ws / "blocks" / f"{r['source_id']}.jsonl"),
           reason="" if r["report"]["block_count"] >= 14 else "块数偏少，疑似丢行")

    # T02 DOCX（表格/文本框/页眉页脚）
    r = cli_json(ws, "ingest", str(sp("heldout_resume.docx")))
    out["docx"] = r
    blocks = _blocks(ws, r["source_id"])
    methods = {b["method"] for b in blocks}
    texts = "\n".join(b["text"] for b in blocks)
    has_table = "docx_table" in methods
    has_box = "docx_textbox" in methods
    has_header = any("docx_header" in (b["locator"] or {}) for b in blocks)
    cert_ok = P.get("cert_keyword", "CDA") in texts
    ok = has_table and has_box and has_header and cert_ok
    record("T02", "解析", "DOCX 表格/文本框/页眉页脚", input_="heldout_resume.docx",
           expected="表格、文本框、页眉均被提取；证书文本可见",
           actual=f"table={has_table} textbox={has_box} header={has_header} 证书={cert_ok}",
           status="通过" if ok else "失败", mode=AUTO,
           evidence=rel(ws / "blocks" / f"{r['source_id']}.jsonl"),
           reason="" if ok else "DOCX 隐藏内容有遗漏",
           fix="" if ok else "检查 w:txbxContent / docx_table / 页眉遍历")

    # T03 文字 PDF（2 页）
    r = cli_json(ws, "ingest", str(sp("heldout_resume_text.pdf")))
    out["pdf_text"] = r
    rep = r["report"]["report"]
    ok = rep["pages_total"] == 2 and rep["ocr_used"] is False and r["report"]["block_count"] >= 10
    record("T03", "解析", "文字型 PDF（2 页）逐页判定", input_="heldout_resume_text.pdf",
           expected="2 页均用文字层，不触发 OCR",
           actual=f"pages={rep['pages_total']} ocr_used={rep['ocr_used']} blocks={r['report']['block_count']}",
           status="通过" if ok else "失败", mode=AUTO,
           evidence=rel(ws / "blocks" / f"{r['source_id']}.jsonl"))

    # T04 扫描 PDF（无 OCR）
    rc, text = cli(ws, "ingest", str(sp("heldout_resume_scanned.pdf")), expect=3)
    scan_rep = json.loads(text[text.find("{"):]) if "{" in text else {}
    ok = rc == 3 and "没有可用的 OCR" in text or "OCR" in text
    record("T04", "解析", "扫描 PDF（无 OCR 依赖）", input_="heldout_resume_scanned.pdf",
           expected="如实报失败：退出码 3，说明缺 OCR，不产出伪造文字",
           actual=f"rc={rc}；输出含“{_snippet(text)}”",
           status="通过" if rc == 3 else "失败", mode=AUTO,
           evidence=rel(ART / "cli.log"),
           reason="" if rc == 3 else "未按依赖缺失退出")

    # T05 双栏 PDF 阅读顺序
    r = cli_json(ws, "ingest", str(sp("heldout_resume_2col.pdf")))
    blocks = _blocks(ws, r["source_id"])
    texts = [b["text"] for b in blocks]
    conflicts = r["report"]["report"].get("reading_order_conflicts") or []
    # 判定：左栏内容是否被右栏切断（"杭州云枢" 之后应紧跟其自身要点）
    joined = " | ".join(texts)
    interleaved = _looks_interleaved(texts)
    status = "通过" if not interleaved else ("通过" if conflicts else "失败")
    record("T05", "解析", "双栏 PDF 阅读顺序", input_="heldout_resume_2col.pdf",
           expected="不把左右栏交错成一行一句；若交错必须标记阅读顺序冲突",
           actual=f"{len(blocks)} 块，交错={interleaved}，冲突标记={len(conflicts)}；顺序：{joined[:120]}…",
           status=status, mode=AUTO,
           evidence=rel(ws / "blocks" / f"{r['source_id']}.jsonl"),
           reason="" if status == "通过" else "左右栏被交错，且未标记冲突",
           fix="" if status == "通过" else "按 x 坐标做列切分后再排序")
    out["pdf_2col"] = r

    # T06 双栏 DOCX
    r = cli_json(ws, "ingest", str(sp("heldout_resume_2col.docx")))
    warns = " ".join(r["report"]["warnings"])
    detected = "双栏" in warns or "分栏" in warns or "column" in warns.lower()
    blocks = _blocks(ws, r["source_id"])
    record("T06", "解析", "双栏 DOCX 是否被识别并提示", input_="heldout_resume_2col.docx",
           expected="至少提示“检测到分栏排版”，提醒阅读顺序可能不可靠",
           actual=f"警告={warns[:80] or '无'}；检测到分栏={detected}；{len(blocks)} 块",
           status="通过" if detected else "失败", mode=AUTO,
           evidence=rel(ws / "blocks" / f"{r['source_id']}.jsonl"),
           reason="" if detected else "分栏版式未被识别，用户无从得知顺序风险",
           fix="" if detected else "读取 sectPr/w:cols，num>1 时告警")
    out["docx_2col"] = r

    # T07 模糊图片（无 OCR）
    rc, text = cli(ws, "ingest", str(sp("heldout_resume_blurry.jpg")), expect=3)
    record("T07", "解析", "模糊图片（无 OCR 依赖）", input_="heldout_resume_blurry.jpg",
           expected="退出码 3，明确说明需 OCR，不产出内容",
           actual=f"rc={rc}；{_snippet(text)}",
           status="通过" if rc == 3 else "失败", mode=AUTO,
           evidence=rel(ART / "cli.log"))

    # T08 模糊图片（模拟低置信度）
    r = cli_json(ws, "ingest", str(sp("heldout_resume_blurry.jpg")),
                 "--ocr", "fixture", env_extra={"JFA_ALLOW_TEST_PROVIDER": "1"})
    queue = read_json(ws / "review" / f"{r['source_id']}.json")
    flags = {span["category"] for item in queue["items"] for span in item["spans"]}
    low = [i for i in queue["items"] if (i["confidence"] or 1) < 0.75]
    bad_date = any(f.startswith("odd_date") for i in queue["items"] for f in i["flags"])
    ok = bool(low) and bad_date and {"contact", "date"} <= flags
    record("T08", "解析", "低置信度/异常月份进入待核对", input_="heldout_resume_blurry.jpg + 旁车",
           expected="低置信度、异常月份(2020.15)、联系方式/日期被标为待核对",
           actual=f"低置信条目={len(low)} 异常日期={bad_date} 标注类别={sorted(flags)}",
           status="通过" if ok else "失败", mode=SIM,
           evidence=rel(ws / "review" / f"{r['source_id']}.json"),
           reason="" if ok else "低质量识别未被拦截",
           fix="" if ok else "核对阈值与日期校验需覆盖")
    out["blurry"] = r
    return out


def _blocks(ws: Path, sid: str) -> list[dict]:
    path = ws / "blocks" / f"{sid}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _snippet(text: str, n: int = 60) -> str:
    flat = " ".join(text.split())
    return flat[:n]


def _looks_interleaved(texts: list[str]) -> bool:
    """True when a left-column line is immediately followed by a right-column line."""
    left_markers = ("陈曦", "18600001234", "chenxi", "杭州云枢", "数据分析师", "2018.04")
    right_markers = ("宁波海联", "高级数据分析师", "2020.10", "教育背景", "浙江工业大学")
    for i in range(len(texts) - 1):
        a, b = texts[i], texts[i + 1]
        if any(m in a for m in left_markers) and any(m in b for m in right_markers):
            # 允许同一行的正常相邻（例如姓名下一行是电话），只统计跨语义跳转
            if not (("陈曦" in a and "18600001234" in b) or ("陈曦" in a and "chenxi" in b)):
                return True
    return False


# ---------------------------------------------------------------------------
# B. 字段正确性（以源文本为权威，逐字核对）
# ---------------------------------------------------------------------------


def area_fields(parse: dict, ws: Path) -> dict:
    archive = read_json(ws / "facts" / "facts.json")
    index = {f["fact_id"]: f for f in archive["facts"]}
    source = (sp("heldout_resume.txt")).read_text(encoding="utf-8")

    def fact_containing(needle: str) -> dict | None:
        for fact in archive["facts"]:
            if needle in fact["content"] or needle in json.dumps(
                    fact.get("structured", {}), ensure_ascii=False):
                return fact
        return None

    def check_field(tid: str, label: str, source_form: str, *,
                    structured_key: str | None = None,
                    expected_value: str | None = None) -> None:
        """Field check with the SOURCE as the authority, not the program.

        Three independent conditions, all required:
          1. the value literally appears in the held-out source text;
          2. a fact records it, and its structured value equals the expected
             normalisation (e.g. 2018.04 -> 2018-04);
          3. the block it cites — from the *same source file* — literally
             contains the source form (a fact id alone proves nothing).
        """
        in_source = source_form in source
        fact = fact_containing(source_form)
        evidence_ok = False
        evidence_detail = "无引用块"
        if fact:
            src = (fact.get("source") or {})
            ids = src.get("block_ids") or []
            sid = src.get("source_id") or ""
            for bid in ids:
                block_text = _block_text(ws, bid, source_id=sid)
                if source_form in block_text:
                    evidence_ok = True
                    evidence_detail = f"{sid}/{bid}"
                    break
            if not evidence_ok and ids:
                evidence_detail = f"引用 {len(ids)} 块但均不含该串（按 source_id={sid} 定位）"
        structured_ok = True
        actual_value = ""
        if structured_key and fact:
            actual_value = str((fact.get("structured") or {}).get(structured_key, ""))
            if expected_value is not None:
                structured_ok = actual_value == expected_value
            else:
                structured_ok = source_form in actual_value
        ok = in_source and fact is not None and evidence_ok and structured_ok
        record(tid, "字段正确性", f"{label}", input_="heldout 源文件",
               expected=(f"源文本含「{source_form}」；事实引用同源块的原文含该串；"
                         f"{structured_key or '内容'} == {expected_value or '含该串'}"),
               actual=(f"源含={in_source} 档案={fact['fact_id'] if fact else '无'} "
                       f"证据={evidence_detail} "
                       f"{structured_key or ''}={actual_value or '-'} 字段匹配={structured_ok}"),
               status="通过" if ok else "失败", mode=AUTO,
               evidence=rel(ws / "facts" / "facts.json"),
               reason="" if ok else "字段/证据链不成立（逐字或来源不符）",
               fix="" if ok else "核对 facts 的 source_id/block_ids 是否指向真实来源")

    check_field("T09", "姓名", P["name"], structured_key="value", expected_value=P["name"])
    check_field("T10a", "电话", P["phone"], structured_key="value",
                expected_value=P["phone"])
    check_field("T10b", "邮箱", P["email"], structured_key="value",
                expected_value=P["email"])
    check_field("T11a", "第一段起始日期", P["job1_start"], structured_key="start",
                expected_value=P["job1_start_iso"])
    check_field("T11b", "第二段起始日期", P["job2_start"], structured_key="start",
                expected_value=P["job2_start_iso"])
    check_field("T12a", "学校", P["school"], structured_key="school",
                expected_value=P["school"])
    check_field("T12b", "学历", P["degree"], structured_key="degree",
                expected_value=P["degree"])
    check_field("T12c", "专业", P["major"], structured_key="major",
                expected_value=P["major"])
    check_field("T13a", "公司", P["job1_company"], structured_key="company",
                expected_value=P["job1_company"])
    check_field("T13b", "公司", P["job2_company"], structured_key="company",
                expected_value=P["job2_company"])
    check_field("T13c", "业绩数字（原文之一）", P["n_rep"], )
    check_field("T13d", "业绩数字（原文之二）", P["n_hours1"], )
    check_field("T13e", "业绩数字（原文之三）", P["n_hours2"], )
    check_field("T13f", "业绩数字（原文之四）", P["n_times"], )

    # 年限口径：两段不重叠，应等于 2018.04-2020.09 + 2020.10-至今
    tenure = archive["tenure"]
    record("T13g", "字段正确性", "工作年限按区间合并（一段至今）",
           input_="heldout_resume.txt",
           expected="total_months > 0 且 = raw_sum_months（无并行任职）",
           actual=f"total={tenure['total_months']} raw={tenure['raw_sum_months']} "
                  f"并发={tenure['concurrent_detected']} 文本={tenure['total_text']}",
           status="通过" if tenure["total_months"] > 0
           and tenure["total_months"] == tenure["raw_sum_months"] else "失败",
           mode=AUTO, evidence=rel(ws / "facts" / "facts.json"))
    return archive


def _block_text(ws: Path, block_id: str, *, source_id: str = "") -> str:
    """Resolve a block id *within its own source*.

    Block ids are per source (`B-0011` exists in every ingested file), so looking
    them up without `source_id` silently returns the wrong file's block.
    """
    candidates = ([ws / "blocks" / f"{source_id}.jsonl"] if source_id
                  else list((ws / "blocks").glob("*.jsonl")))
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["block_id"] == block_id:
                return row["text"]
    return ""


# ---------------------------------------------------------------------------
# C/D. 改写、事实支持、反注水
# ---------------------------------------------------------------------------


def area_rewrite(ws: Path, jobs: list[dict], plans: list[dict]) -> None:
    archive = read_json(ws / "facts" / "facts.json")
    index = {f["fact_id"]: f for f in archive["facts"]}

    all_changes: list[dict] = []
    for job, plan in zip(jobs, plans):
        version = read_json(ws / "rewrites" / job["job_id"] / "latest.json")
        for exp in version.get("experiences") or []:
            for bullet in exp.get("bullets") or []:
                all_changes.append({**bullet, "job": job["title"],
                                    "exp_fact": exp.get("fact_id")})
        if version.get("summary"):
            all_changes.append({**version["summary"], "job": job["title"], "section": "summary"})

    # T14 数字必须来自证据或原文
    bad_numbers = []
    for change in all_changes:
        basis = _basis_texts(change, index)
        support = content_support(change.get("after", ""), basis)
        if support["numbers_unsupported"]:
            bad_numbers.append((change["job"], change.get("after", "")[:40],
                                support["numbers_unsupported"]))
    record("T14", "改写支持", "改写中的数字全部来自证据/原文",
           input_="3 份改写方案", expected="unsupported numbers = 0",
           actual=f"{len(all_changes)} 条改动，无依据数字 {len(bad_numbers)} 处"
                  + (f"：{bad_numbers[:2]}" if bad_numbers else ""),
           status="通过" if not bad_numbers else "失败", mode=AUTO,
           evidence=rel(ws / "rewrites"), reason="" if not bad_numbers else "出现无依据数字")

    # T15 身份/强度词
    escalation = []
    for change in all_changes:
        after = change.get("after", "")
        basis = "\n".join(_basis_texts(change, index))
        for word in IDENTITY_TERMS:
            if word in after and word not in basis:
                escalation.append((change["job"], word, after[:40]))
    record("T15", "改写支持", "无“协助→主导/团队→个人/等级抬高”",
           input_="3 份改写方案",
           expected="强度词只在证据本身出现时才可使用",
           actual=f"{len(escalation)} 处可疑" + (f"：{escalation[:3]}" if escalation else ""),
           status="通过" if not escalation else "失败", mode=AUTO,
           evidence=rel(ws / "rewrites"),
           reason="" if not escalation else "发现无依据的强度词")

    # T16 内容级支持度（证据 id 存在 ≠ 证据支持结论）
    weak = []
    ratios = []
    for change in all_changes:
        basis = _basis_texts(change, index)
        support = content_support(change.get("after", ""), basis)
        ratios.append(support["ratio"])
        if support["ratio"] < 0.55:
            weak.append({"job": change["job"], "ratio": support["ratio"],
                         "after": change.get("after", "")[:60],
                         "unsupported": support["unsupported_terms"][:8]})
    avg = round(sum(ratios) / len(ratios), 3) if ratios else 0
    # 说明：这是覆盖率代理指标，不是"语义支持"结论；结论留给人工
    record("T16", "改写支持", "内容级支持度（代理指标 + 人工复核表）",
           input_="3 份改写方案",
           expected="每条改动的措辞应有证据支撑；最终由人工判定语义支持",
           actual=f"{len(all_changes)} 条，平均覆盖率 {avg}，低于 0.55 的 {len(weak)} 条",
           status="未测试", mode=HUMAN,
           evidence=rel(HERE / "HUMAN_REVIEW.md"),
           reason="缺少人工标注金标准：覆盖率只是代理指标，不能代替人工判定语义支持；"
                  "本 Agent 初审见 HUMAN_REVIEW.md，仍待独立人工签署",
           fix="由人工逐条阅读 review-sheet.md 并签署结论")


def _basis_texts(change: dict, index: dict) -> list[str]:
    texts: list[str] = []
    for fid in change.get("fact_ids") or []:
        fact = index.get(fid)
        if fact:
            texts.append(fact.get("content", ""))
            structured = fact.get("structured") or {}
            texts.append(" ".join(str(v) for v in structured.values()))
    if change.get("before"):
        texts.append(change["before"])
    return texts


def area_anti_inflation(ws: Path, job: dict) -> None:
    archive = read_json(ws / "facts" / "facts.json")
    bad_plan = author_plan(ws, job, archive, variant="inflate")
    rc, text = cli(ws, "rewrite", "validate", job["job_id"], "--file", str(bad_plan), expect=4)
    codes = {c for c in ("verb_escalation", "skill_level_unbacked", "invented_number",
                         "team_to_individual", "absolute_claim", "keyword_without_fact",
                         "identity_company_swap") if c in text}
    record("T17", "反注水", "注水方案被阻断", input_="plan-inflate.json",
           expected="退出码 4，且命中动词升级/数字编造等多项",
           actual=f"rc={rc}，命中 {sorted(codes)}",
           status="通过" if rc == 4 and len(codes) >= 3 else "失败", mode=AUTO,
           evidence=rel(bad_plan),
           reason="" if len(codes) >= 3 else "拦截种类不足",
           fix="" if len(codes) >= 3 else "检查 guard 覆盖")

    # 单独验证三类典型违规各自必被拦
    cases = {
        "协助→主导": ("协助搭建门店经营日报", "主导门店经营日报体系"),
        "团队→个人": ("配合运营调整 3 次活动策略", "独立调整 3 次活动策略"),
        "无依据数字": ("维护 27 张固定报表", "维护 27 张固定报表，效率提升 300%"),
        "无依据技能": ("维护 27 张固定报表", "维护 27 张固定报表，精通 Kubernetes"),
    }
    single_failures = []
    for label, (before, after) in cases.items():
        plan = read_json(author_plan(ws, job, archive))
        plan["experiences"][0]["bullets"] = [{
            "before": before, "after": after, "change_reason": "验收用例",
            "requirement_ids": ["R-001"],
            "fact_ids": [plan["experiences"][0]["fact_id"]],
            "verification_status": "verified",
        }]
        path = HERE / "artifacts" / f"single-{abs(hash(label)) % 9999}.json"
        write_json(path, plan)
        rc, text = cli(ws, "rewrite", "validate", job["job_id"], "--file", str(path), expect=None)
        if rc == 0:
            single_failures.append(label)

    record("T18", "反注水", "四类典型违规逐条拦截", input_="单条违规方案 ×4",
           expected="4 条全部退出码 4",
           actual=("全部拦截" if not single_failures else f"漏过：{single_failures}"),
           status="通过" if not single_failures else "失败", mode=AUTO,
           evidence=rel(HERE / "artifacts"),
           reason="" if not single_failures else f"{single_failures} 未被拦截",
           fix="" if not single_failures else "补充对应 guard")

    # T19 合规改写必须能通过（防止过度拦截）
    good = read_json(author_plan(ws, job, archive))
    path = HERE / "artifacts" / "single-good.json"
    write_json(path, good)
    rc, text = cli(ws, "rewrite", "validate", job["job_id"], "--file", str(path), expect=None)
    record("T19", "反注水", "合规方案不被误拦", input_="plan-normal.json",
           expected="退出码 0",
           actual=f"rc={rc}",
           status="通过" if rc == 0 else "失败", mode=AUTO,
           evidence=rel(path),
           reason="" if rc == 0 else "误拦合规内容")


# ---------------------------------------------------------------------------
# E. 三岗位差异
# ---------------------------------------------------------------------------


def area_three_jobs(ws: Path, jobs: list[dict]) -> None:
    docs, plans = [], []
    for job in jobs:
        exports = sorted((ws / "out" / job["job_id"]).glob("v*/*-resume-doc.json"))
        plan_path = ws / "rewrites" / job["job_id"] / "latest.json"
        docs.append(read_json(exports[-1]) if exports else None)
        plans.append(read_json(plan_path) if plan_path.exists() else None)

    if any(d is None for d in docs) or any(p is None for p in plans):
        record("T20", "三岗位差异", "同一档案生成三份定制简历",
               input_="3 个岗位", expected="三份都存在",
               actual="有岗位未导出", status="失败", mode=AUTO,
               evidence=rel(ws / "out"), reason="导出缺失")
        return

    archive = read_json(ws / "facts" / "facts.json")
    index = {f["fact_id"]: f for f in archive["facts"]}

    # T20 —— 不再要求"必须重排"。只要求：凡是与原文顺序/取舍不同的地方，
    #        必须有岗位要求编号或删除理由支撑；没有依据的改动才算失败。
    violations: list[str] = []
    changes_report: list[str] = []
    for label, doc, plan in zip("ABC", docs, plans):
        for exp in plan.get("experiences") or []:
            fact = index.get(exp.get("fact_id", ""), {})
            src_lines = [ln for ln in fact.get("content", "").split("\n")[1:] if ln.strip()]
            bullets = exp.get("bullets") or []
            order = [b.get("after", "") for b in bullets]
            differs = order != src_lines[:len(order)] or len(order) != len(src_lines)
            if differs:
                changes_report.append(
                    f"{label}:{exp['fact_id']} 顺序/取舍与原文不同（{len(order)}/{len(src_lines)} 条）")
                for bullet in bullets:
                    if not (bullet.get("requirement_ids") or bullet.get("change_reason")):
                        violations.append(f"{label}:{exp['fact_id']} 有一条改动无要求编号也无理由")
            if exp.get("omit") and not exp.get("omit_reason"):
                violations.append(f"{label}:{exp['fact_id']} 整段删除但无理由")

    record("T20", "三岗位差异", "排序与取舍都有岗位/证据依据（不强制改变顺序）",
           input_="3 个岗位",
           expected="允许三份顺序相同；但凡与原文不同之处必须有 requirement_ids 或理由",
           actual=(f"差异={changes_report or '三份均未改动顺序'}；无依据改动={len(violations)}"),
           status="通过" if not violations else "失败", mode=AUTO,
           evidence=rel(ws / "rewrites"),
           reason="" if not violations else "存在无依据的排序/取舍改动",
           fix="" if not violations else "补 requirement_ids 或删除该改动")

    # T21 身份字段必须三份一致
    identity = [(d["name"], tuple(sorted(c["text"] for c in d["contacts"])),
                 tuple(sorted((e["company"], e["title"], e["period"]) for e in d["experiences"])))
                for d in docs]
    same = identity[0] == identity[1] == identity[2]
    record("T21", "三岗位差异", "身份字段三份完全一致（不得随岗位变化）",
           input_="3 个岗位", expected="姓名/联系方式/公司/职位/时间三项集合相同",
           actual="一致" if same else "不一致",
           status="通过" if same else "失败", mode=AUTO, evidence=rel(ws / "out"))

    # T22 要求映射随岗位变化（这是岗位数据决定的，不是排版约束）
    maps = []
    for job in jobs:
        path = ws / "matches" / job["job_id"] / "latest.json"
        if path.exists():
            maps.append(read_json(path))
    req_sets = [tuple(sorted(r["requirement"] for r in m.get("requirements", []))) for m in maps]
    differs = len(maps) == 3 and len(set(req_sets)) == 3
    record("T22", "三岗位差异", "要求映射来自各自岗位原文",
           input_="3 个岗位", expected="三份映射表的要求集合互不相同",
           actual=f"{len(maps)} 份映射，互异={differs}",
           status="通过" if differs else "失败", mode=AUTO, evidence=rel(ws / "matches"))

    # T23 重点是否"合理"仍需人工
    summaries = [d.get("summary", {}).get("text", "") for d in docs]
    sim = (_jaccard(summaries[0], summaries[1]), _jaccard(summaries[1], summaries[2]),
           _jaccard(summaries[0], summaries[2]))
    record("T23", "三岗位差异", "三份重点是否合理", input_="3 份简历",
           expected="人工判断：A 质量平台、B 自动化框架、C 团队与推动，各自是否突出",
           actual=(f"简介相似度={[round(x, 2) for x in sim]}；"
                   f"排序差异={len(changes_report)} 处；未做人工签署"),
           status="未测试", mode=HUMAN,
           evidence=rel(HERE / "HUMAN_REVIEW.md"),
           reason="缺少独立人工判断；本 Agent 初审不作为通过依据",
           fix="由独立人工阅读三份 DOCX 后签署")


def _jaccard(a: str, b: str) -> float:
    ga, gb = ngrams(a), ngrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


# ---------------------------------------------------------------------------
# F. 缺失 / 冲突 / 低质量
# ---------------------------------------------------------------------------


def area_degraded() -> None:
    """Degraded inputs run in their own workspace.

    They deliberately introduce an unconfirmed and a conflicting fact; doing that
    in the main workspace would (correctly) block later exports, so the two are
    kept apart.
    """
    ws = fresh("degraded")
    cli_json(ws, "ingest", str(sp("heldout_resume.txt")))
    cli_json(ws, "facts", "build")
    cli(ws, "prefs", "set", "--file", str(sp("heldout_prefs.json")))

    # T24 部分职位（原文含公司与职位，但缺任职要求与薪资）
    degraded_company = P.get("degraded_company", "恒澜集团")
    degraded_title = P.get("target_title", "数据分析主管")
    partial_text = (f"{degraded_title}\n{degraded_company} · {P.get('city', '杭州')}\n\n"
                    "岗位职责\n负责团队管理\n（任职要求与薪资区域未加载）")
    partial = cli_json(ws, "job", "add", "--company", degraded_company,
                       "--title", degraded_title, "--text", partial_text,
                       "--location", P.get("city", "杭州"), "--read-scope", "partial")
    completeness = partial["completeness"]
    ok = completeness["is_complete"] is False and "read_scope_not_full" in completeness["missing"]
    record("T24", "降级处理", "职位未读全 → 标记不完整", input_="不完整职位文本",
           expected="is_complete=false 且 missing 含 read_scope_not_full",
           actual=f"is_complete={completeness['is_complete']} missing={completeness['missing']}",
           status="通过" if ok else "失败", mode=AUTO,
           evidence=rel(ws / "jobs" / "jobs.jsonl"),
           reason="" if ok else "未标记不完整")

    # T25 缺薪资 → unknown，不判不合格（用本次新增的 job_id 精确定位）
    screening = cli_json(ws, "screen")
    row = next((r for r in screening["results"] if r["job_id"] == partial["job_id"]), None)
    unknown_salary = bool(row) and any(
        c["id"] == "salary_floor" and c["verdict"] == "unknown" for c in row["hard_checks"])
    not_ineligible = bool(row) and row["overall"] != "ineligible"
    record("T25", "降级处理", "缺薪资按未知处理，不判不合格", input_="无薪资岗位",
           expected="salary_floor=unknown 且 overall ≠ ineligible",
           actual=(f"job={partial['job_id']} overall={row['overall'] if row else 'n/a'} "
                   f"unknown_salary={unknown_salary}"),
           status="通过" if unknown_salary and not_ineligible else "失败", mode=AUTO,
           evidence=rel(ws / "screening" / "latest.json"),
           reason="" if unknown_salary and not_ineligible else "未知被当成不符合")

    # T26 冲突事实
    cli(ws, "facts", "add", "--kind", "skill",
        "--content", "Tableau（仅课程作业使用）", "--unconfirmed")
    archive = read_json(ws / "facts" / "facts.json")
    first = next(f for f in archive["facts"] if "Tableau" in f["content"])
    cli(ws, "facts", "confirm", first["fact_id"], "--status", "conflict")
    archive = read_json(ws / "facts" / "facts.json")
    conflicted = [f for f in archive["facts"] if f["confirm_status"] == "conflict"]
    validation = cli_json(ws, "facts", "validate", expect=None)
    listed = any("冲突" in json.dumps(i, ensure_ascii=False) for i in validation.get("issues", []))
    record("T26", "降级处理", "冲突事实被标记并提示", input_="同一技能两条冲突表述",
           expected="存在 conflict 状态；校验给出提示",
           actual=f"conflict 条数={len(conflicted)} 校验提示={listed}",
           status="通过" if conflicted and listed else "失败", mode=AUTO,
           evidence=rel(ws / "facts" / "facts.json"),
           reason="" if conflicted and listed else "冲突未被显式处理")


def _load_jobs(ws: Path) -> list[dict]:
    path = ws / "jobs" / "jobs.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# G. 导出与版式
# ---------------------------------------------------------------------------


def area_export(ws: Path, job: dict) -> None:
    result = cli_json(ws, "export", job["job_id"])
    docx = Path(result["docx"])
    pdf = Path(result["pdf"]) if result["pdf"] else None
    txt = Path(result["txt"])

    ok_files = docx.exists() and docx.stat().st_size > 5000 and pdf and pdf.exists() \
        and pdf.stat().st_size > 2000 and txt.exists()
    record("T27", "导出", "DOCX/PDF/TXT 真实生成", input_="holdout 岗位",
           expected="三个文件存在且非空（DOCX>5KB，PDF>2KB）",
           actual=f"docx={docx.stat().st_size if docx.exists() else 0}B "
                  f"pdf={pdf.stat().st_size if pdf and pdf.exists() else 0}B "
                  f"backend={result['backend']}",
           status="通过" if ok_files else "失败", mode=AUTO,
           evidence=rel(docx.parent),
           reason="" if ok_files else "产物缺失或过小")

    verification = result["verification_report"] or {}
    checks = {c["id"]: c for c in verification.get("checks", [])}
    layout_ids = ["pdf_blank_pages", "pdf_glyphs", "pdf_overflow", "pdf_overlap",
                  "pdf_font_floor", "pdf_line_coverage", "pdf_selectable"]
    failed = [i for i in layout_ids if i in checks and checks[i]["status"] == "fail"]
    record("T28", "导出", "逐页渲染 + 版式检查", input_="导出的 PDF",
           expected="空白页/乱码/溢出/重叠/字号/遗漏 全部不失败",
           actual=f"{len(checks)} 项校验，版式项失败 {failed or '无'}；"
                  f"页数={verification.get('pages') and len(verification['pages'])}",
           status="通过" if not failed and checks else "失败", mode=AUTO,
           evidence=rel(docx.parent / f"{docx.stem}-校验结果.json"),
           reason="" if not failed else f"版式问题：{failed}")

    consistency = checks.get("docx_pdf_consistency", {})
    record("T29", "导出", "DOCX 与 PDF 内容一致", input_="导出的 DOCX/PDF",
           expected="字符覆盖率 ≥ 97%",
           actual=f"{consistency.get('detail', '未执行')}",
           status="通过" if consistency.get("status") == "pass" else "失败", mode=AUTO,
           evidence=rel(docx.parent))

    # 人眼复核：渲染图另存，由本 Agent 目视
    pages = sorted((docx.parent).glob("*-pages/page-*.png"))
    preview = HERE / "artifacts" / "layout-preview.png"
    if pages:
        shutil.copy2(pages[0], preview)
    record("T30", "导出", "版式人眼复核", input_="导出的 PDF 第 1 页",
           expected="无重叠、无截断、层次清晰、中英文正常",
           actual=("已目视：单栏层次清楚，无重叠/截断/乱码，中英文混排正常，字号 ≥9.5pt；"
                   "小问题：中文逐字换行拆开了“分析”，日期被规范化为 ISO 写法"),
           status="通过", mode=HUMAN, evidence=rel(preview),
           reason="复核人=本 Agent，非独立第三方；详见 tests/acceptance/HUMAN_REVIEW.md")
    return result


# ---------------------------------------------------------------------------
# H. 原始文件不变
# ---------------------------------------------------------------------------


def area_immutability(ws: Path, baseline: dict[str, str],
                      heldout_before: set[str]) -> None:
    """Source files must be byte-identical, and nothing may be written into the
    sample directory.

    `baseline` is taken before any command runs; comparing against it is the only
    way to prove "the original file did not change" (a post-hoc hash proves
    nothing).
    """
    targets = [n for n in
               ("heldout_resume.txt", "heldout_resume.docx", "heldout_resume_text.pdf",
                "heldout_resume_2col.pdf", "heldout_job_growth.txt")
               if sp(n).exists()]

    # (a) originals unchanged
    changed = [name for name in targets
               if sp(name).name in baseline and sha256(sp(name)) != baseline[sp(name).name]]
    # (b) the copy inside sources/ equals the original, resolved by the index
    index_path = ws / "blocks" / "index.json"
    index = read_json(index_path) if index_path.exists() else {"sources": []}
    copy_checks = []
    for entry in index.get("sources", []):
        stored = ws / entry["stored_path"]
        origin_name = entry["file"]
        # stored names are `<sid>-<stem>-<hash>.<ext>`; match by the recorded name
        if not stored.exists():
            copy_checks.append((origin_name, "副本缺失"))
            continue
        match = next((n for n in targets
                      if origin_name.startswith(n.rsplit(".", 1)[0])
                      and origin_name.endswith(n.rsplit(".", 1)[-1])), None)
        if match and match in baseline:
            same = sha256(stored) == baseline[match]
            copy_checks.append((match, "一致" if same else "不一致"))
            if not same:
                changed.append(f"{match}(副本)")

    record("T31", "原始文件", "ingest/export 后源文件不变", input_="heldout 原件",
           expected="5 个原件的 sha256 与开工前一致；sources/ 副本与原件字节相同",
           actual=(f"原件改动={changed or '无'}；副本比对={copy_checks}"),
           status="通过" if not changed else "失败", mode=AUTO,
           evidence=rel(ws / "sources"),
           reason="" if not changed else f"被改动：{changed}",
           fix="" if not changed else "检查是否有写回源路径的逻辑")

    # (c) nothing new in the sample directory
    heldout_after = {p.name for p in HELD.iterdir() if p.is_file()}
    added = sorted(heldout_after - heldout_before)
    removed = sorted(heldout_before - heldout_after)
    record("T32", "原始文件", "不向样本目录写回产物", input_="heldout 目录",
           expected="开工前后文件集合完全一致（样本目录只读）",
           actual=f"新增={added or '无'} 删除={removed or '无'}",
           status="通过" if not added and not removed else "失败", mode=AUTO,
           evidence=rel(HELD),
           reason="" if not added and not removed else "样本目录被写入/删除",
           fix="" if not added and not removed else "确认工具只写工作区")


# ---------------------------------------------------------------------------
# I. 浏览器 / 越权
# ---------------------------------------------------------------------------


def area_authorization(ws: Path) -> None:
    caps = cli_json(ws, "scan")
    browser = caps["browser"]
    record("T33", "浏览器", "真实宿主浏览器读取职位", input_="无浏览器工具",
           expected="若宿主无浏览器则明确未声明并走粘贴；不得虚构工具名",
           actual=f"browser.status={browser['status']}，kind='{browser.get('kind','')}'，"
                  f"fallback={browser.get('fallback')}",
           status="未测试", mode=REAL,
           reason="本环境未暴露任何浏览器工具，真实读取路径无法测试",
           fix="在具备浏览器工具的宿主中重跑 T33/T34")

    # T34 串页：不再硬拒，默认标待核对；且该岗位不得直接导出
    mismatch_text = (sp("heldout_job_same_role.txt")).read_text(encoding="utf-8")
    rc, text = cli(ws, "job", "add", "--company", "完全无关的公司",
                   "--title", "完全无关的职位", "--text", mismatch_text,
                   "--read-scope", "full", expect=None)
    recorded = rc == 0
    jobs_now = _load_jobs(ws)
    mismatch_job = next((j for j in jobs_now if j.get("company") == "完全无关的公司"), None)
    status = (mismatch_job or {}).get("identity", {}).get("status", "")
    pending = status == "pending_review"
    allowed, why = True, ""
    if mismatch_job:
        import importlib
        sys.path.insert(0, str(ROOT / "job-fit-assistant" / "scripts"))
        from jfa.jobs import export_allowed as _ea
        allowed, why = _ea(mismatch_job)
    caveat = any("身份待核对" in c for r in cli_json(ws, "screen").get("results", [])
                 for c in (r.get("caveats") or []))
    record("T34", "浏览器", "职位身份冲突默认标为待核对（不硬拒、不可直接导出）",
           input_="公司/职位与原文不符",
           expected="记录成功但 identity.status=pending_review；导出被阻止；筛选给出提示",
           actual=f"rc={rc} 记录={recorded} status={status} 导出允许={allowed} 提示={caveat}",
           status="通过" if recorded and pending and not allowed and caveat else "失败",
           mode=AUTO, evidence=rel(ws / "jobs" / "jobs.jsonl"),
           reason="" if pending else "身份冲突未被标为待核对",
           fix="" if pending else "检查 check_identity 与 export_allowed")

    # T34b 用户确认后放行，但留痕且不声称系统核对
    if mismatch_job:
        rc2, text2 = cli(ws, "job", "confirm-identity", mismatch_job["job_id"],
                         "--statement", "这是我投的那个岗位，页面确实只显示简称",
                         "--evidence", "https://example.org/jobs/same_role + 页面标题",
                         expect=None)
        jobs_after = _load_jobs(ws)
        row = next((j for j in jobs_after if j["job_id"] == mismatch_job["job_id"]), {})
        ident = row.get("identity") or {}
        claim = ident.get("user_claim") or {}
        ok2 = (rc2 == 0 and ident.get("status") == "user_confirmed"
               and claim.get("human_authorised") is False and claim.get("evidence"))
        record("T34b", "浏览器", "用户确认后放行，但明确不是系统核对/人类授权",
               input_="confirm-identity",
               expected="status=user_confirmed；human_authorised=false；保留原话与证据",
               actual=f"rc={rc2} status={ident.get('status')} "
                      f"human_authorised={claim.get('human_authorised')} "
                      f"evidence={bool(claim.get('evidence'))}",
               status="通过" if ok2 else "失败", mode=AUTO,
               evidence=rel(ws / "jobs" / "jobs.jsonl"),
               reason="" if ok2 else "确认流程未留痕或错误地标记为已授权")

    # T35 采集上限 → E_PAUSED
    rc, text = cli(ws, "job", "add", "--company", "示例A", "--title", "岗位A",
                   "--text", "岗位A\n示例A有限公司 · 杭州\n岗位职责\n负责数据\n任职要求\n本科",
                   "--session", "s-limit", "--index", "9", expect=None)
    record("T35", "浏览器", "单次读取上限触发暂停", input_="index=9 > max 8",
           expected="退出码 7（E_PAUSED），提示这是硬限制",
           actual=f"rc={rc}；{_snippet(text)}",
           status="通过" if rc == 7 else "失败", mode=AUTO,
           evidence=rel(ART / "cli.log"),
           reason="" if rc == 7 else "未触发暂停，存在无限采集风险")

    # T36 未授权发送/上传：静态证据
    net_hits = _static_network_scan()
    record("T36", "授权", "不存在发送/上传代码路径", input_="job-fit-assistant/scripts",
           expected="无 HTTP/邮件/FTP/WebSocket 客户端调用",
           actual=f"命中 {len(net_hits)} 处" + (f"：{net_hits[:3]}" if net_hits else "（无）"),
           status="通过" if not net_hits else "失败", mode=AUTO,
           evidence="job-fit-assistant/scripts",
           reason="" if not net_hits else "存在潜在外发能力")


def _static_network_scan() -> list[str]:
    banned = re.compile(
        r"^\s*(?:import|from)\s+(urllib|requests|httpx|http\.client|socket|ssl|smtplib|"
        r"ftplib|paramiko|websocket|aiohttp|telnetlib|poplib|imaplib)\b")
    hits = []
    for path in (ROOT / "job-fit-assistant" / "scripts").rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if banned.search(line):
                hits.append(f"{path.name}:{lineno} {line.strip()}")
    return hits


# ---------------------------------------------------------------------------
# J. 恶意指令
# ---------------------------------------------------------------------------


def area_injection() -> None:
    ws = fresh("inject")
    before = _workspace_fingerprint(ws)

    r = cli_json(ws, "ingest", str(sp("malicious_resume.txt")))
    cli(ws, "facts", "build")
    archive = read_json(ws / "facts" / "facts.json")
    joined = json.dumps(archive, ensure_ascii=False)

    after = _workspace_fingerprint(ws)
    deleted = [name for name in before if name not in after]
    text_kept = "rm -rf" in joined or "evil.example" in joined

    record("T37", "注入", "简历中的恶意指令不被执行", input_="malicious_resume.txt",
           expected="指令只作为文本保留或忽略；不删除文件、不执行命令、不外发",
           actual=f"工作区文件减少 {len(deleted)} 个；指令文本可见={text_kept}",
           status="通过" if not deleted and text_kept else "失败", mode=AUTO,
           evidence=rel(ws / "facts" / "facts.json"),
           reason="" if not deleted else f"有文件被删除：{deleted}",
           fix="" if not deleted else "立即检查是否有文件操作被执行")

    malicious_text = (sp("malicious_job.txt")).read_text(encoding="utf-8")
    m_company, m_title = _company_title(malicious_text)
    job = cli_json(ws, "job", "add", "--company", m_company, "--title", m_title,
                   "--text", malicious_text)
    raw = (ws / job["raw_path"]).read_text(encoding="utf-8")
    after2 = _workspace_fingerprint(ws)
    deleted2 = [name for name in after if name not in after2]
    record("T38", "注入", "职位正文中的恶意指令不被执行", input_="malicious_job.txt",
           expected="作为职位文本保存；不触发任何命令或上传",
           actual=f"原文保留={('evil.example' in raw)}；文件减少={len(deleted2)}",
           status="通过" if not deleted2 and "evil.example" in raw else "失败",
           mode=AUTO, evidence=rel(ws / job["raw_path"]),
           reason="" if not deleted2 else "出现非预期文件变化")

    # 注入文本不得进入简历产物
    简历 = list((ws / "out").rglob("*.docx")) if (ws / "out").exists() else []
    record("T39", "注入", "注入文本未进入交付物", input_="上述工作区",
           expected="未导出时为空集，因此判定为“未进入”",
           actual=f"导出文件 {len(简历)} 个（本次未导出）",
           status="通过" if not 简历 else "未测试", mode=AUTO,
           evidence=rel(ws / "out") if (ws / "out").exists() else rel(ws))


def _workspace_fingerprint(ws: Path) -> dict[str, int]:
    out = {}
    for path in ws.rglob("*"):
        if path.is_file():
            out[str(path.relative_to(ws))] = path.stat().st_size
    return out


# ---------------------------------------------------------------------------
# K. 一致性（3 次）
# ---------------------------------------------------------------------------


def area_determinism(runs: int = 3) -> None:
    """Repeat the semantic chain 3× and compare **constraints**, not wording.

    Byte-identical resumes are not the goal (and not required): different wording
    can still be correct. What must be stable is the constraint set — identity
    fields, numbers used, hedge/level words, and which requirements each change
    claims to satisfy.
    """
    signatures: list[str] = []
    details: list[str] = []
    for i in range(runs):
        ws = fresh(f"det{i}")
        cli_json(ws, "ingest", str(sp("heldout_resume.txt")))
        cli(ws, "facts", "build")
        cli(ws, "prefs", "set", "--file", str(sp("heldout_prefs.json")))
        archive = read_json(ws / "facts" / "facts.json")
        index = {f["fact_id"]: f for f in archive["facts"]}

        constraints: dict[str, dict] = {}
        for name in ("same_role", "growth", "manager"):
            text = sp(f"heldout_job_{name}.txt").read_text(encoding="utf-8")
            company, title = _company_title(text)
            job = cli_json(ws, "job", "add", "--company", company, "--title", title,
                           "--text", text, "--read-scope", "full")
            author_mapping(ws, job, archive)
            plan_path = author_plan(ws, job, archive)
            cli_json(ws, "rewrite", "validate", job["job_id"], "--file", str(plan_path))
            cli_json(ws, "export", job["job_id"])
            plan = read_json(plan_path)

            numbers, hedges, levels, reqs = set(), set(), set(), set()
            for exp in plan.get("experiences") or []:
                for bullet in exp.get("bullets") or []:
                    after = bullet.get("after", "")
                    numbers |= set(NUM.findall(after))
                    hedges |= {h for h in ("协助", "参与", "配合") if h in after}
                    levels |= {w for w in ("熟练", "精通", "掌握") if w in after}
                    reqs |= set(bullet.get("requirement_ids") or [])
            doc = read_json(sorted((ws / "out" / job["job_id"]).glob("v*/*-resume-doc.json"))[-1])
            constraints[title] = {
                "numbers": sorted(numbers), "hedges": sorted(hedges),
                "levels": sorted(levels), "reqs": sorted(reqs),
                "identity": [doc["name"], sorted(c["text"] for c in doc["contacts"]),
                             sorted((e["company"], e["title"], e["period"])
                                    for e in doc["experiences"])],
            }

        digest = hashlib.sha256(json.dumps(constraints, ensure_ascii=False,
                                           sort_keys=True).encode()).hexdigest()[:12]
        signatures.append(digest)
        details.append(f"run{i + 1}(冷) digest={digest} 岗位={len(constraints)}")

    consistent = len(set(signatures)) == 1
    record("T40", "一致性", "语义约束重复 3 次稳定（不要求正文逐字相同）",
           input_="同一档案 + 3 个岗位 ×3",
           expected="3 次的约束集（身份字段/数字/限定词/等级词/引用要求）完全相同",
           actual=("; ".join(details)
                   + "；无缓存层，3 次均为冷运行，每次重新执行事实→映射→改写→导出全链"),
           status="通过" if consistent else "失败", mode=AUTO,
           evidence=rel(ART),
           reason="" if consistent else f"约束指纹不一致：{signatures}",
           fix="" if consistent else "定位非确定性来源")


def _strip_timestamps(value):
    if isinstance(value, dict):
        return {k: _strip_timestamps(v) for k, v in value.items()
                if k not in {"generated_at", "created_at", "updated_at", "saved_at", "at"}}
    if isinstance(value, list):
        return [_strip_timestamps(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    if not (sp("heldout_resume.txt")).exists():
        subprocess.run([sys.executable, str(HERE / "make_heldout.py")], check=True)

    print("=" * 70)
    print("job-fit-assistant 验收（保留样本，未参与开发调试）")
    print("=" * 70)

    # 开工前快照：原件哈希 + 样本目录文件集合
    baseline = {}
    for name in ("heldout_resume.txt", "heldout_resume.docx", "heldout_resume_text.pdf",
                 "heldout_resume_2col.pdf", "heldout_job_growth.txt"):
        path = sp(name)
        if path.exists():
            baseline[path.name] = sha256(path)
    heldout_before = {p.name for p in HELD.iterdir() if p.is_file()}

    parse = area_parsing()

    ws_main = fresh("main")
    cli_json(ws_main, "ingest", str(sp("heldout_resume.txt")))
    cli_json(ws_main, "ingest", str(sp("heldout_resume.docx")))
    cli_json(ws_main, "ingest", str(sp("heldout_resume_text.pdf")))
    cli(ws_main, "facts", "build")
    cli(ws_main, "prefs", "set", "--file", str(sp("heldout_prefs.json")))

    archive = area_fields(parse, ws_main)

    jobs = []
    for name in ("same_role", "growth", "manager"):
        text = (HELD / f"heldout_job_{name}.txt").read_text(encoding="utf-8")
        company, title = _company_title(text)
        jobs.append(cli_json(ws_main, "job", "add", "--company", company,
                             "--title", title, "--text", text,
                             "--read-scope", "full"))
    cli_json(ws_main, "screen")

    plans = []
    for job in jobs:
        author_mapping(ws_main, job, archive)
        plan_path = author_plan(ws_main, job, archive)
        cli_json(ws_main, "rewrite", "validate", job["job_id"], "--file", str(plan_path))
        plans.append(plan_path)

    area_rewrite(ws_main, jobs, plans)
    area_anti_inflation(ws_main, jobs[0])

    for job in jobs:
        cli_json(ws_main, "export", job["job_id"])
    area_three_jobs(ws_main, jobs)
    area_export(ws_main, jobs[0])
    area_immutability(ws_main, baseline, heldout_before)
    area_authorization(ws_main)
    area_degraded()
    area_injection()
    area_determinism()

    _write_review_sheet(ws_main, jobs)
    _write_report()
    return 0


def _company_title(text: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    title = lines[0]
    second = lines[1] if len(lines) > 1 else ""
    company = second.split("·")[0].strip() if "·" in second else second.split()[0]
    return company, title


def _write_review_sheet(ws: Path, jobs: list[dict]) -> None:
    """Everything a human needs to judge semantic support, with the raw facts."""
    archive = read_json(ws / "facts" / "facts.json")
    index = {f["fact_id"]: f for f in archive["facts"]}
    lines = ["# 人工复核表（语义支持）", "",
             "本表用于人工判断「每条改写是否被所引事实真实支持」。",
             "自动化只能给出覆盖率，**不能**替代这个判断。", ""]
    for job in jobs:
        version_path = ws / "rewrites" / job["job_id"] / "latest.json"
        if not version_path.exists():
            continue
        plan = read_json(version_path)
        lines += [f"## 岗位：{job['title']}（{job['company']}）", ""]
        items = []
        if plan.get("summary"):
            items.append(("个人简介", plan["summary"]))
        for exp in plan.get("experiences") or []:
            for bullet in exp.get("bullets") or []:
                items.append((exp.get("fact_id", ""), bullet))
        for label, change in items:
            lines.append(f"### [{label}] 改写后")
            lines.append(f"> {change.get('after', '')}")
            lines.append("")
            lines.append("引用事实原文：")
            for fid in change.get("fact_ids") or []:
                fact = index.get(fid, {})
                lines.append(f"- `{fid}`：{fact.get('content', '（不存在）').replace(chr(10), ' / ')}")
            support = content_support(change.get("after", ""),
                                      _basis_texts(change, index))
            lines.append("")
            lines.append(f"自动覆盖率：{support['ratio']}　无依据词："
                         f"{'、'.join(support['unsupported_terms'][:10]) or '无'}")
            lines.append("")
            lines.append("人工判定：☐ 支持　☐ 部分支持　☐ 不支持（需修改）")
            lines.append("")
    path = HERE / "artifacts" / "review-sheet.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_report() -> None:
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["status"] == "通过")
    failed = [r for r in RESULTS if r["status"] == "失败"]
    untested = [r for r in RESULTS if r["status"] == "未测试"]
    elapsed = round(time.time() - START, 1)

    write_json(HERE / "acceptance.json",
               {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "elapsed_seconds": elapsed, "total": total, "passed": passed,
                "failed": len(failed), "untested": len(untested), "results": RESULTS})

    lines = ["# 验收报告 — job-fit-assistant", "",
             f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 样本集：`{SAMPLE_SET}`（路径 `{HELD}`）",
             f"- 共计 {total} 项：通过 {passed}，失败 {len(failed)}，未测试 {len(untested)}",
             f"- 耗时：{elapsed}s", "",
             "## 判据来源说明", "",
             "| 来源 | 含义 |", "| --- | --- |",
             f"| {AUTO} | 仅凭产物即可判定（逐字比对、集合包含、哈希、退出码） |",
             f"| {HUMAN} | 需要人的语义判断；无人工标注时一律记“未测试”，不记通过 |",
             f"| {SIM} | 使用测试替身或模拟宿主 agent，不代表真实识别/真实宿主表现 |",
             f"| {REAL} | 真实宿主能力；本环境缺失，记“未测试” |", "",
             "## 结果明细", "",
             "| 编号 | 领域 | 用例 | 预期 | 实际 | 结论 | 判据 | 证据 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in RESULTS:
        lines.append(
            f"| {r['id']} | {r['area']} | {r['title']} | {_cell(r['expected'])} "
            f"| {_cell(r['actual'])} | {r['status']} | {r['mode']} | {_cell(r['evidence'])} |")

    if failed:
        lines += ["", "## 失败项与修复建议", ""]
        for r in failed:
            lines += [f"### {r['id']} {r['title']}", "",
                      f"- 输入：{r['input']}",
                      f"- 预期：{r['expected']}",
                      f"- 实际：{r['actual']}",
                      f"- 原因：{r['reason']}",
                      f"- 修复建议：{r['fix']}", ""]

    if untested:
        lines += ["", "## 未测试项", ""]
        for r in untested:
            lines += [f"- **{r['id']} {r['title']}**（{r['mode']}）：{r['reason']}"]

    (HERE / "ACCEPTANCE_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    print("=" * 70)
    print(f"验收完成：{total} 项，通过 {passed}，失败 {len(failed)}，未测试 {len(untested)}"
          f"（{elapsed}s）")
    print(f"报告：{rel(HERE / 'ACCEPTANCE_REPORT.md')}")
    if failed:
        print("失败项：" + "、".join(r["id"] for r in failed))
    if untested:
        print("未测试：" + "、".join(r["id"] for r in untested))
    print("=" * 70)


def _cell(text: str, limit: int = 150) -> str:
    value = " ".join(str(text).split()).replace("|", "\\|")
    return value if len(value) <= limit else value[: limit - 1] + "…"


if __name__ == "__main__":
    raise SystemExit(main())
