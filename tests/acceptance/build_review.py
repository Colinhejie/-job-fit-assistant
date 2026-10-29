# -*- coding: utf-8 -*-
"""Build a self-contained, reviewable resume package.

Produces `review/` with: the original resume, three complete postings, three
tailored versions (docx/pdf/txt), the change diffs, and the fact archive the
diffs point at — so a human can check every claim against its source without
running anything.

    python tests/acceptance/build_review.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import run_acceptance as A  # noqa: E402

OUT = ROOT / "review"
HELD = HERE / "heldout"


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- inputs (copied verbatim, never modified) ------------------------
    (OUT / "01-输入").mkdir()
    shutil.copy2(HELD / "heldout_resume.txt", OUT / "01-输入" / "原始简历.txt")
    for key, name in (("same_role", "职位A-数据分析师"),
                      ("growth", "职位B-增长数据分析师"),
                      ("manager", "职位C-数据分析主管")):
        shutil.copy2(HELD / f"heldout_job_{key}.txt", OUT / "01-输入" / f"{name}.txt")

    ws = A.ART / "ws-review"
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    A.cli(ws, "init")
    A.cli_json(ws, "ingest", str(HELD / "heldout_resume.txt"))
    A.cli(ws, "facts", "build")
    A.cli(ws, "prefs", "set", "--file", str(HELD / "heldout_prefs.json"))
    archive = A.read_json(ws / "facts" / "facts.json")

    jobs = []
    for key, label in (("same_role", "A"), ("growth", "B"), ("manager", "C")):
        text = (HELD / f"heldout_job_{key}.txt").read_text(encoding="utf-8")
        company, title = A._company_title(text)
        job = A.cli_json(ws, "job", "add", "--company", company, "--title", title,
                         "--text", text, "--read-scope", "full",
                         "--url", f"https://example.org/jobs/{key}")
        A.author_mapping(ws, job, archive)
        plan_path = A.author_plan(ws, job, archive)
        A.cli_json(ws, "rewrite", "validate", job["job_id"], "--file", str(plan_path))
        result = A.cli_json(ws, "export", job["job_id"])
        jobs.append((label, job, result, A.read_json(plan_path)))

    # ---- outputs ---------------------------------------------------------
    (OUT / "02-简历版本").mkdir()
    (OUT / "03-修改对照").mkdir()
    (OUT / "04-事实与校验").mkdir()
    for label, job, result, plan in jobs:
        docx = Path(result["docx"])
        folder = OUT / "02-简历版本" / f"版本{label}-{job['title']}"
        folder.mkdir(parents=True, exist_ok=True)
        for suffix in (".docx", ".pdf", ".txt"):
            src = docx.with_suffix(suffix)
            if src.exists():
                shutil.copy2(src, folder / f"简历{suffix}")
        out_dir = docx.parent
        for md in out_dir.glob("*.md"):
            if "修改对照" in md.name:
                shutil.copy2(md, OUT / "03-修改对照" / f"版本{label}-修改对照.md")
            elif "事实" in md.name:
                shutil.copy2(md, OUT / "04-事实与校验" / f"版本{label}-事实与待确认.md")
            elif "匹配" in md.name:
                shutil.copy2(md, OUT / "04-事实与校验" / f"版本{label}-匹配与缺口.md")
        plan_copy = OUT / "03-修改对照" / f"版本{label}-change_plan.json"
        plan_copy.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    shutil.copy2(ws / "facts" / "facts.json", OUT / "04-事实与校验" / "事实档案.json")
    shutil.copy2(ws / "preferences.json", OUT / "01-输入" / "求职偏好.json")

    # ---- index -----------------------------------------------------------
    rows = []
    for label, job, result, plan in jobs:
        rows.append(f"| 版本{label} | {job['company']} · {job['title']} | "
                    f"`02-简历版本/版本{label}-{job['title']}/` | "
                    f"`03-修改对照/版本{label}-修改对照.md` |")
    (OUT / "README.md").write_text(
        "# 可审查的简历结果\n\n"
        "全部为合成数据。输入未被修改，产物只新增。\n\n"
        "| 版本 | 目标岗位 | 产物 | 修改对照 |\n| --- | --- | --- | --- |\n"
        + "\n".join(rows) + "\n\n"
        "## 目录\n\n"
        "- `01-输入/`：原始简历、三份完整职位、求职偏好（原件副本）\n"
        "- `02-简历版本/`：三份定制简历（DOCX / PDF / TXT）\n"
        "- `03-修改对照/`：每条改动的 before/after/理由/要求编号/事实编号，以及方案 JSON\n"
        "- `04-事实与校验/`：事实档案、匹配与缺口、事实与待确认\n"
        "- `05-逐条语义审计.md`：人工逐条核对结论（不是程序自评）\n",
        encoding="utf-8")

    audit = HERE / "SEMANTIC_AUDIT.md"
    if audit.exists():
        shutil.copy2(audit, OUT / "05-逐条语义审计.md")

    print(f"review package: {OUT}")
    for label, job, _, _ in jobs:
        print(f"  版本{label} {job['company']} · {job['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
