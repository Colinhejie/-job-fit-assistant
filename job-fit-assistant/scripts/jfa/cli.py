# -*- coding: utf-8 -*-
"""Command line interface for the job-fit-assistant local core.

Every command supports `--json` so the host agent can parse results instead of
scraping prose. Exit codes: 0 ok, 2 usage, 3 dependency, 4 data/validation,
5 export, 6 safety, 7 paused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import blocks as B
from . import capability, facts as F, jobs as J, mapping as M, reports, rewrite as RW, screen
from . import salary as salary_mod
from .errors import JfaError, NotFoundError, UsageError, ValidationError
from .extract import router as extract_router
from .extract.ocr import review as review_mod
from .util import now_iso, read_json, safe_component, squash, truncate, write_json
from .workspace import CORE_VERSION, Workspace, resolve_workspace

PREFS_FILE = "preferences.json"
PLAN_FILE = "change_plan.json"


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except JfaError as exc:
        _emit_error(exc, as_json=getattr(args, "json", False))
        return exc.exit_code
    except KeyboardInterrupt:
        print("已中断。未完成的写入不会产生半成品文件。", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jfa",
        description="job-fit-assistant 本地核心：解析、校验证、筛选、生成与导出（不调用外部模型 API）",
    )
    parser.add_argument("--home", help="工作区目录（默认 ./job-fit-workspace 或 $JFA_HOME）")
    parser.add_argument("--version", action="version", version=f"jfa {CORE_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="能力检查：执行位置/文件/OCR/PDF导出/浏览器")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("init", help="初始化工作区")
    p.add_argument("--force", action="store_true", help="已存在时仍重新写入元数据（不删资料）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("host", help="宿主能力声明（浏览器等）")
    host_sub = p.add_subparsers(dest="host_command", required=True)
    hd = host_sub.add_parser("declare", help="声明宿主真实具备的浏览器工具")
    hd.add_argument("--browser-kind", required=True,
                    help="浏览器类型，例如 in-app-browser / playwright / host-tool（按宿主真实情况填写）")
    hd.add_argument("--tool", action="append", default=[],
                    help="宿主实际提供的工具名，可重复")
    hd.add_argument("--login-scope", default="", help="登录状态范围：local / cloud / none")
    hd.add_argument("--notes", default="")
    hd.add_argument("--json", action="store_true")
    hd.set_defaults(func=cmd_host_declare)
    hs = host_sub.add_parser("show", help="查看宿主声明")
    hs.add_argument("--json", action="store_true")
    hs.set_defaults(func=cmd_host_show)

    p = sub.add_parser("ingest", help="解析简历文件（DOCX/PDF/图片/文本）")
    p.add_argument("file")
    p.add_argument("--ocr", default="auto", choices=["auto", "paddleocr", "rapidocr", "fixture", "none"],
                   help="OCR 提供者；none 表示禁用 OCR")
    p.add_argument("--lang", default="ch")
    p.add_argument("--dpi", type=int, default=220)
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--structure", action="store_true", help="复杂版式使用 PP-StructureV3")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("sources", help="列出已解析来源")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("ocr", help="OCR 核对流程")
    ocr_sub = p.add_subparsers(dest="ocr_command", required=True)
    ol = ocr_sub.add_parser("list", help="列出待核对条目")
    ol.add_argument("source_id", nargs="?")
    ol.add_argument("--json", action="store_true")
    ol.set_defaults(func=cmd_ocr_list)
    os_ = ocr_sub.add_parser("show", help="输出核对清单 markdown")
    os_.add_argument("source_id")
    os_.add_argument("--limit", type=int, default=40)
    os_.set_defaults(func=cmd_ocr_show)
    for name, func in (("accept", cmd_ocr_accept), ("reject", cmd_ocr_reject),
                       ("correct", cmd_ocr_correct)):
        sp = ocr_sub.add_parser(name, help=f"{name} 一条核对项")
        sp.add_argument("source_id")
        sp.add_argument("item_id")
        if name == "correct":
            sp.add_argument("--text", required=True, help="更正后的文字")
        sp.add_argument("--note", default="")
        sp.add_argument("--json", action="store_true")
        sp.set_defaults(func=func)

    p = sub.add_parser("facts", help="事实档案")
    f_sub = p.add_subparsers(dest="facts_command", required=True)
    fb = f_sub.add_parser("build", help="从已解析块生成候选事实档案")
    fb.add_argument("--source-id", help="只用某个来源；默认全部")
    fb.add_argument("--force", action="store_true", help="覆盖已有 facts.json（先备份）")
    fb.add_argument("--json", action="store_true")
    fb.set_defaults(func=cmd_facts_build)
    fv = f_sub.add_parser("validate", help="校验事实档案")
    fv.add_argument("--json", action="store_true")
    fv.set_defaults(func=cmd_facts_validate)
    fs = f_sub.add_parser("show", help="查看事实档案摘要")
    fs.add_argument("--kind", help="只看某类事实")
    fs.add_argument("--status", help="只看某个确认状态")
    fs.add_argument("--json", action="store_true")
    fs.set_defaults(func=cmd_facts_show)
    fc = f_sub.add_parser("confirm", help="确认/更正一条事实")
    fc.add_argument("fact_id")
    fc.add_argument("--content", help="更正后的内容")
    fc.add_argument("--status", choices=list(F.STATUS_VALUES))
    fc.add_argument("--reason", default="")
    fc.add_argument("--json", action="store_true")
    fc.set_defaults(func=cmd_facts_confirm)
    fa = f_sub.add_parser("add", help="加入用户口述的事实（默认已确认）")
    fa.add_argument("--kind", required=True, choices=list(F.KIND_VALUES))
    fa.add_argument("--content", required=True)
    fa.add_argument("--label", default="")
    fa.add_argument("--unconfirmed", action="store_true")
    fa.add_argument("--json", action="store_true")
    fa.set_defaults(func=cmd_facts_add)
    ft = f_sub.add_parser("tenure", help="工作年限（区间合并，不累加并行任职）")
    ft.add_argument("--json", action="store_true")
    ft.set_defaults(func=cmd_facts_tenure)

    p = sub.add_parser("prefs", help="求职偏好")
    pre_sub = p.add_subparsers(dest="prefs_command", required=True)
    ps = pre_sub.add_parser("set", help="写入偏好（JSON 文件或键值）")
    ps.add_argument("--file")
    ps.add_argument("--json-patch", help="直接覆盖的 JSON 字符串片段")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_prefs_set)
    pss = pre_sub.add_parser("show", help="查看偏好")
    pss.add_argument("--json", action="store_true")
    pss.set_defaults(func=cmd_prefs_show)

    p = sub.add_parser("job", help="职位记录")
    job_sub = p.add_subparsers(dest="job_command", required=True)
    ja = job_sub.add_parser("add", help="记录一个已读取的职位")
    ja.add_argument("--company", required=True)
    ja.add_argument("--title", required=True)
    ja.add_argument("--text", help="职位原文")
    ja.add_argument("--file", help="职位原文文件")
    ja.add_argument("--url", default="")
    ja.add_argument("--location", default="")
    ja.add_argument("--source-kind", default=J.SOURCE_USER_PASTE,
                    choices=list(J.SOURCE_VALUES))
    ja.add_argument("--host-tool", default="")
    ja.add_argument("--read-scope", default=J.SCOPE_FULL, choices=list(J.SCOPE_VALUES))
    ja.add_argument("--session", default="")
    ja.add_argument("--index", type=int, default=0)
    ja.add_argument("--sections", help="宿主已结构化的栏目 JSON")
    ja.add_argument("--notes", default="")
    ja.add_argument("--job-ref", default="",
                    help="平台岗位编号 / 详情页 ID（能拿到就必须填，用于事后追溯）")
    ja.add_argument("--read-at", default="",
                    help="宿主记录的实际读取时间（ISO）；不填则用当前时间")
    ja.add_argument("--detail-region", default="",
                    help="读取的详情区域标识（如 job-detail / 详情面板 / #job-title）")
    ja.add_argument("--override-statement", default="",
                    help="（兼容保留）用户本人确认原话；正式流程请用 job confirm-identity")
    ja.add_argument("--override-evidence", default="",
                    help="（兼容保留）页面证据；正式流程请用 job confirm-identity")
    ja.add_argument("--json", action="store_true")
    ja.set_defaults(func=cmd_job_add)
    jl = job_sub.add_parser("list", help="列出职位")
    jl.add_argument("--limit", type=int, default=20)
    jl.add_argument("--json", action="store_true")
    jl.set_defaults(func=cmd_job_list)
    js = job_sub.add_parser("show", help="查看职位详情")
    js.add_argument("job_id")
    js.add_argument("--json", action="store_true")
    js.set_defaults(func=cmd_job_show)
    jc = job_sub.add_parser("confirm-identity", help="用户确认职位身份（记录，不等于系统核对）")
    jc.add_argument("job_id")
    jc.add_argument("--statement", required=True, help="用户本人的确认原话（不要代写）")
    jc.add_argument("--evidence", required=True,
                    help="页面证据：URL / 页面标题 / 截图路径 / 岗位编号")
    jc.add_argument("--json", action="store_true")
    jc.set_defaults(func=cmd_job_confirm_identity)

    jm = job_sub.add_parser("session", help="暂停/恢复采集会话")
    jm.add_argument("--session", default="default")
    jm.add_argument("--status", required=True,
                    choices=["running", "paused", "cancelled", "done"])
    jm.add_argument("--reason", default="")
    jm.add_argument("--json", action="store_true")
    jm.set_defaults(func=cmd_job_session)

    p = sub.add_parser("screen", help="硬条件筛选 + 软偏好排序")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--job-id", action="append", default=[])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("match", help="岗位要求 ↔ 个人证据映射")
    m_sub = p.add_subparsers(dest="match_command", required=True)
    ms = m_sub.add_parser("skeleton", help="生成映射骨架（要求列表，证据留空）")
    ms.add_argument("job_id")
    ms.add_argument("--json", action="store_true")
    ms.set_defaults(func=cmd_match_skeleton)
    mv = m_sub.add_parser("validate", help="校验宿主填写的映射表")
    mv.add_argument("job_id")
    mv.add_argument("--file")
    mv.add_argument("--json", action="store_true")
    mv.set_defaults(func=cmd_match_validate)
    mshow = m_sub.add_parser("show", help="查看当前映射")
    mshow.add_argument("job_id")
    mshow.add_argument("--json", action="store_true")
    mshow.set_defaults(func=cmd_match_show)

    p = sub.add_parser("rewrite", help="简历改写方案")
    r_sub = p.add_subparsers(dest="rewrite_command", required=True)
    rv = r_sub.add_parser("validate", help="校验改写方案（反注水）")
    rv.add_argument("job_id")
    rv.add_argument("--file")
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(func=cmd_rewrite_validate)
    rs = r_sub.add_parser("show", help="查看当前改写方案")
    rs.add_argument("job_id")
    rs.add_argument("--text", action="store_true", help="输出文本化简历预览")
    rs.add_argument("--json", action="store_true")
    rs.set_defaults(func=cmd_rewrite_show)
    rt = r_sub.add_parser("template", help="输出空白改写方案模板")
    rt.add_argument("job_id")
    rt.set_defaults(func=cmd_rewrite_template)

    p = sub.add_parser("export", help="导出 DOCX/PDF/TXT + 报告并校验")
    p.add_argument("job_id")
    p.add_argument("--version", type=int, default=0)
    p.add_argument("--style")
    p.add_argument("--photo")
    p.add_argument("--max-pages", type=int, default=0)
    p.add_argument("--min-font-pt", type=float, default=0)
    p.add_argument("--allow-pending", action="store_true",
                   help="把待确认内容也写进简历（默认不写）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("schema", help="查看/校验 JSON schema")
    s_sub = p.add_subparsers(dest="schema_command", required=True)
    sl = s_sub.add_parser("list", help="列出随包 schema")
    sl.add_argument("--json", action="store_true")
    sl.set_defaults(func=cmd_schema_list)
    sc = s_sub.add_parser("check", help="用某个 schema 校验 JSON 文件")
    sc.add_argument("kind")
    sc.add_argument("file")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(func=cmd_schema_check)

    p = sub.add_parser("purge", help="删除本工具本地资料")
    p.add_argument("--yes", action="store_true", help="确认删除，无此参数不会删除任何文件")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("selftest", help="自检：能力 + 内存管线冒烟测试")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_selftest)

    return parser


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ws(args) -> Workspace:
    return resolve_workspace(args.home)


def _emit(data: Any, as_json: bool, text: str) -> int:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


def _emit_error(exc: JfaError, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return
    print(f"错误 [{exc.code}] {exc.message}", file=sys.stderr)
    if exc.hint:
        print(f"处理建议：{exc.hint}", file=sys.stderr)
    if exc.detail:
        print("详细信息：" + json.dumps(exc.detail, ensure_ascii=False)[:1200], file=sys.stderr)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(str(cell)))
    lines = ["  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)),
             "  ".join("-" * widths[i] for i in range(len(headers)))]
    for row in rows:
        lines.append("  ".join(_pad(str(c), widths[i]) for i, c in enumerate(row)))
    return "\n".join(lines)


def _display_width(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


# ---------------------------------------------------------------------------
# commands: capability / lifecycle
# ---------------------------------------------------------------------------


def cmd_scan(args) -> int:
    ws = _ws(args)
    caps = capability.run_probe(ws)
    return _emit(caps, args.json, capability.render_report(caps))


def cmd_init(args) -> int:
    ws = _ws(args)
    meta = ws.init(force=args.force)
    ws.log("workspace init")
    text = "\n".join([
        f"工作区已就绪：{ws.root}",
        f"创建时间：{meta['created_at']}",
        "下一步：",
        "  1) jfa scan                       # 先看能力，缺什么会明说",
        "  2) jfa ingest <简历文件>           # 解析原文",
        "  3) jfa facts build                # 生成候选事实档案",
    ])
    return _emit({"ok": True, **meta}, args.json, text)


def cmd_host_declare(args) -> int:
    ws = _ws(args).require()
    cfg = ws.load_config()
    cfg["host"] = {
        "declared": True,
        "browser_kind": args.browser_kind,
        "browser_tool_names": args.tool or [],
        "login_scope": args.login_scope,
        "declared_at": now_iso(),
        "notes": args.notes,
    }
    ws.save_config(cfg)
    if not args.tool:
        print("注意：没有声明任何浏览器工具名；抓取流程将退回用户粘贴文本。", file=sys.stderr)
    return _emit({"ok": True, **cfg["host"]}, args.json,
                 capability.render_report(capability.run_probe(ws)))


def cmd_host_show(args) -> int:
    ws = _ws(args)
    cfg = ws.load_config() if ws.exists() else {"host": {}}
    host = cfg.get("host") or {}
    if args.json:
        print(json.dumps(host, ensure_ascii=False, indent=2))
    else:
        print("宿主声明：")
        print(f"- 已声明：{'是' if host.get('declared') else '否'}")
        print(f"- 浏览器类型：{host.get('browser_kind') or '未声明'}")
        print(f"- 工具名：{', '.join(host.get('browser_tool_names') or []) or '未声明'}")
        print(f"- 登录范围：{host.get('login_scope') or '未声明'}")
        print(f"- 声明时间：{host.get('declared_at') or '—'}")
    return 0


# ---------------------------------------------------------------------------
# commands: ingestion & OCR review
# ---------------------------------------------------------------------------


def cmd_ingest(args) -> int:
    ws = _ws(args).require()
    allow_ocr = args.ocr != "none"
    provider_name = "auto" if args.ocr == "auto" else args.ocr
    result = extract_router.ingest(
        ws, Path(args.file), provider_name=provider_name, lang=args.lang,
        dpi=args.dpi, max_pages=args.max_pages, structure=args.structure,
        allow_ocr=allow_ocr,
    )
    rep = result["report"]
    lines = [
        f"已解析：{result['stored_as']}",
        f"- 来源 ID：{result['source_id']}",
        f"- 文本块：{rep['block_count']}（需人工核对 {rep['needs_review']}）",
        f"- 是否使用 OCR：{'是' if rep['report'].get('ocr_used') else '否'}",
    ]
    if "pages_total" in rep["report"]:
        lines.append(f"- 页数：处理 {rep['report']['pages_processed']} / 共 {rep['report']['pages_total']}")
        lines.append(f"- 文字层页：{rep['report'].get('text_pages')}；OCR 页：{rep['report'].get('ocr_pages')}")
    if rep["report"].get("page_note"):
        lines.append(f"- 定位说明：{rep['report']['page_note']}")
    if rep["warnings"]:
        lines += ["", "警告："] + [f"- {w}" for w in rep["warnings"]]
    if rep["errors"]:
        lines += ["", "错误："] + [f"- {e}" for e in rep["errors"]]
    lines += ["", f"下一步：`jfa ocr show {result['source_id']}` 人工核对 {result['review']['pending']} 条待确认内容"]
    for warning in rep["warnings"]:
        pass
    code = 0 if result["ok"] else 3
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
    return code


def cmd_sources(args) -> int:
    ws = _ws(args).require()
    rows = extract_router.sources(ws)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("还没有解析过任何文件。运行 `jfa ingest <文件>`。")
        return 0
    print(_table(["来源 ID", "文件", "类型", "块", "待核对"],
                 [[r["source_id"], r["file"], r["kind"], str(r["blocks"]), str(r["needs_review"])]
                  for r in rows]))
    return 0


def _load_queue(ws, source_id: str) -> dict:
    path = ws.review_dir / f"{source_id}.json"
    return review_mod.load_queue(path)


def cmd_ocr_list(args) -> int:
    ws = _ws(args).require()
    ids = [args.source_id] if args.source_id else [s["source_id"] for s in extract_router.sources(ws)]
    out = []
    for sid in ids:
        queue = _load_queue(ws, sid)
        for item in queue.get("items", []):
            out.append({"source_id": sid, **{k: item[k] for k in
                        ("item_id", "block_id", "page", "state", "method", "confidence")},
                        "preview": truncate(item["text"], 60),
                        "categories": sorted({s["category"] for s in item["spans"]})})
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if not out:
        print("没有待核对条目。")
        return 0
    print(_table(["来源", "条目", "页", "状态", "类别", "预览"],
                 [[r["source_id"], r["item_id"], str(r["page"]), r["state"],
                   ",".join(r["categories"]) or "-", r["preview"]] for r in out]))
    return 0


def cmd_ocr_show(args) -> int:
    ws = _ws(args).require()
    queue = _load_queue(ws, args.source_id)
    print(review_mod.render_review_markdown(queue, limit=args.limit))
    return 0


def _ocr_decide(ws, args, action: str) -> int:
    queue = _load_queue(ws, args.source_id)
    item = review_mod.apply_decision(
        queue, args.item_id, action=action,
        corrected_text=getattr(args, "text", "") or "", note=args.note,
    )
    review_mod.save_queue(ws.review_dir / f"{args.source_id}.json", queue)
    review_mod.append_correction_log(ws.review_dir / "corrections.jsonl", {
        "source_id": args.source_id, "item_id": args.item_id,
        "block_id": item["block_id"], "action": action,
        "before": item["text"], "after": item.get("corrected_text", ""),
        "note": args.note,
    })
    text = "\n".join([
        f"{args.item_id} → {item['state']}",
        f"原文保留在 revisions 中，未被静默替换。",
        f"剩余待核对：{queue['pending']}",
    ])
    return _emit({"ok": True, "item": item, "pending": queue["pending"]},
                 getattr(args, "json", False), text)


def cmd_ocr_accept(args) -> int:
    return _ocr_decide(_ws(args).require(), args, "accept")


def cmd_ocr_reject(args) -> int:
    return _ocr_decide(_ws(args).require(), args, "reject")


def cmd_ocr_correct(args) -> int:
    return _ocr_decide(_ws(args).require(), args, "correct")


# ---------------------------------------------------------------------------
# commands: facts
# ---------------------------------------------------------------------------


def cmd_facts_build(args) -> int:
    ws = _ws(args).require()
    blocks, missing = extract_router.all_blocks(ws)
    if args.source_id:
        blocks = [b for b in blocks if b.source_id == args.source_id]
    if not blocks:
        raise NotFoundError(
            "没有可用的文本块。",
            hint="先运行 `jfa ingest <简历文件>`；纯文本/Word/PDF 都可以。",
        )
    source_file = ""
    source_id = ""
    if args.source_id:
        rows = [s for s in extract_router.sources(ws) if s["source_id"] == args.source_id]
        if rows:
            source_file = rows[0]["file"]
            source_id = args.source_id
    archive = F.new_archive()
    source_map = {s["source_id"]: s["file"] for s in extract_router.sources(ws)}
    archive = F.build_from_blocks(archive, blocks, source_file=source_file,
                                  source_id=source_id, source_map=source_map)
    archive["notes"].append(f"由 {len(blocks)} 个文本块生成；"
                            "机器拆分出的结构化字段默认未确认。")
    if missing:
        archive["notes"].append(f"缺失解析结果的来源：{missing}")

    target = ws.facts_file
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.force:
        candidate = target.with_name("facts.candidate.json")
        write_json(candidate, archive)
        raise UsageError(
            "已存在事实档案，未覆盖。",
            hint=f"候选档案已写入 {candidate.name}；确认差异后用 `jfa facts build --force` 覆盖"
                 "（原文件会先备份）。",
            detail={"candidate": str(candidate.relative_to(ws.root)),
                    "existing": str(target.relative_to(ws.root))},
        )
    if target.exists():
        backup = target.with_name(f"facts.backup-{now_iso().replace(':', '')}.json")
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    write_json(target, archive)
    ws.log(f"facts build: {len(archive['facts'])} facts")

    issues = F.validate(archive)
    summary = F.summarize(archive)
    text = "\n".join([
        f"候选事实档案已生成：{target.relative_to(ws.root)}",
        f"- 事实 {summary['total']} 条：明确提供 {summary['by_status'].get(F.STATUS_SOURCE_EXPLICIT, 0)}，"
        f"未确认 {summary['by_status'].get(F.STATUS_UNCONFIRMED, 0)}",
        f"- 合并后工作年限：{summary['tenure'].get('total_text')}",
        "",
        "必须人工过一遍：机器拆分出的公司/职位/日期/技能等级都标为未确认，",
        "用 `jfa facts confirm <ID> --content \"...\"` 修正后再导出。",
    ])
    if issues:
        text += "\n\n校验提示：" + "\n".join(
            f"- [{i['severity']}] {i['message']}" for i in issues[:10])
    return _emit({"ok": True, "path": str(target.relative_to(ws.root)),
                  "summary": summary, "issues": issues}, args.json, text)


def cmd_facts_validate(args) -> int:
    ws = _ws(args).require()
    archive = ws.load_facts()
    issues = F.validate(archive)
    blocking = F.blocking_issues(issues)
    text_lines = [f"事实档案：{len(archive['facts'])} 条"]
    if not issues:
        text_lines.append("校验通过，没有发现问题。")
    for issue in issues:
        text_lines.append(f"- [{issue['severity']}] {issue['message']}")
    code = 4 if blocking else 0
    if args.json:
        print(json.dumps({"ok": not blocking, "issues": issues,
                          "summary": F.summarize(archive)}, ensure_ascii=False, indent=2))
    else:
        print("\n".join(text_lines))
    return code


def cmd_facts_show(args) -> int:
    ws = _ws(args).require()
    archive = ws.load_facts()
    facts = archive.get("facts", [])
    if args.kind:
        facts = [f for f in facts if f.get("kind") == args.kind]
    if args.status:
        facts = [f for f in facts if f.get("confirm_status") == args.status]
    if args.json:
        print(json.dumps({"person": archive.get("person"), "tenure": archive.get("tenure"),
                          "facts": facts}, ensure_ascii=False, indent=2))
        return 0
    tenure = archive.get("tenure") or {}
    print(f"姓名：{(archive.get('person') or {}).get('display_name') or '—'}")
    print(f"事实：{len(facts)} 条　工作年限（合并）：{tenure.get('total_text', '—')}")
    print()
    print(_table(["ID", "类型", "状态", "内容", "需确认"],
                 [[f["fact_id"], f.get("kind", ""), f.get("confirm_status", ""),
                   truncate(f.get("content", ""), 44), "是" if f.get("needs_review") else ""]
                  for f in facts]))
    return 0


def cmd_facts_confirm(args) -> int:
    ws = _ws(args).require()
    archive = ws.load_facts()
    status = args.status
    if status is None:
        status = F.STATUS_USER_CONFIRMED if args.content is None else F.STATUS_USER_CONFIRMED
    fact = F.revise_fact(archive, args.fact_id, content=args.content,
                         confirm_status=status, reason=args.reason or "用户确认/更正")
    write_json(ws.facts_file, archive)
    text = "\n".join([
        f"{fact['fact_id']} → {fact['confirm_status']}（v{fact['version']}）",
        f"内容：{truncate(fact['content'], 90)}",
        "修订记录已追加到 revisions，原内容未被删除。",
    ])
    return _emit({"ok": True, "fact": fact}, args.json, text)


def cmd_facts_add(args) -> int:
    ws = _ws(args).require()
    archive = ws.load_facts()
    fact = F.add_user_fact(archive, kind=args.kind, content=args.content,
                           label=args.label, confirmed=not args.unconfirmed)
    write_json(ws.facts_file, archive)
    return _emit({"ok": True, "fact": fact}, args.json,
                 f"已加入 {fact['fact_id']}（{fact['confirm_status']}）：{truncate(fact['content'], 80)}")


def cmd_facts_tenure(args) -> int:
    ws = _ws(args).require()
    archive = ws.load_facts()
    tenure = F.tenure_summary(archive)
    if args.json:
        print(json.dumps(tenure, ensure_ascii=False, indent=2))
        return 0
    print(f"合并后总年限：{tenure['total_text']}（{tenure['total_months']} 个月）")
    print(f"直接相加：{tenure['raw_sum_text']}（{tenure['raw_sum_months']} 个月）")
    print(f"说明：{tenure['note']}")
    if tenure.get("concurrent_detected"):
        print("⚠ 检测到并行任职：已按区间合并，未简单累加。")
    return 0


# ---------------------------------------------------------------------------
# commands: preferences & jobs
# ---------------------------------------------------------------------------


def _prefs_path(ws) -> Path:
    return ws.root / PREFS_FILE


def cmd_prefs_set(args) -> int:
    ws = _ws(args).require()
    prefs = read_json(_prefs_path(ws), None) or screen.empty_prefs()
    if args.file:
        incoming = read_json(Path(args.file), None)
        if incoming is None:
            raise NotFoundError(f"偏好文件不存在：{args.file}")
        prefs = incoming
    if args.json_patch:
        patch = json.loads(args.json_patch)
        prefs = _deep_merge_dict(prefs, patch)
    issues = screen.validate_prefs(prefs)
    blocking = [i for i in issues if i["severity"] == "block"]
    if blocking:
        raise ValidationError("偏好内容不合法。", detail={"issues": blocking})
    write_json(_prefs_path(ws), prefs)
    text = f"偏好已保存：{_prefs_path(ws).relative_to(ws.root)}"
    if issues:
        text += "\n\n提示：\n" + "\n".join(f"- [{i['severity']}] {i['message']}" for i in issues)
    return _emit({"ok": True, "issues": issues}, args.json, text)


def cmd_prefs_show(args) -> int:
    ws = _ws(args).require()
    prefs = read_json(_prefs_path(ws), None)
    if prefs is None:
        raise NotFoundError("还没有设置求职偏好。",
                            hint="用 `jfa prefs set --json-patch '{...}'` 写入。")
    if args.json:
        print(json.dumps(prefs, ensure_ascii=False, indent=2))
        return 0
    hard = prefs.get("hard") or {}
    print("硬条件：")
    for key in ("cities", "work_modes", "min_monthly_base", "target_annual",
                "target_monthly", "required_degree_max", "must_have_keywords",
                "exclude_keywords", "exclude_companies"):
        print(f"- {key}: {hard.get(key)}")
    print("软偏好：")
    print(f"- weights: {(prefs.get('soft') or {}).get('weights')}")
    print(f"- preferred_titles: {(prefs.get('soft') or {}).get('preferred_titles')}")
    return 0


def _deep_merge_dict(base: dict, patch: dict) -> dict:
    out = json.loads(json.dumps(base))
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def cmd_job_add(args) -> int:
    ws = _ws(args).require()
    if args.text and args.file:
        raise UsageError("--text 与 --file 只能给一个。")
    if args.file:
        path = Path(args.file)
        if not path.exists():
            raise NotFoundError(f"职位文本文件不存在：{path}")
        raw = path.read_text(encoding="utf-8", errors="replace")
    elif args.text:
        raw = args.text
    else:
        raise UsageError(
            "缺少职位原文。",
            hint="从宿主浏览器读取后通过 --text 传入；没有浏览器时请用户粘贴文本。",
        )
    sections = json.loads(args.sections) if args.sections else None
    record = J.capture(
        ws, company=args.company, title=args.title, raw_text=raw,
        location=args.location, url=args.url, source_kind=args.source_kind,
        host_tool=args.host_tool, read_scope=args.read_scope,
        session_id=args.session, index=args.index, sections=sections,
        notes=args.notes, job_ref=args.job_ref, read_at=args.read_at,
        detail_region=args.detail_region,
        identity_override=(
            {"statement": args.override_statement,
             "evidence": {"value": args.override_evidence}}
            if args.override_statement or args.override_evidence else None
        ),
    )
    dup = record.get("_duplicate")
    completeness = record["completeness"]
    text = "\n".join([
        f"{'已存在（去重命中）' if dup else '已记录'}：{record['job_id']}",
        f"- {record['company']} · {record['title']} · {record['sections'].get('city') or '地点未读到'}",
        f"- 薪资：{salary_mod.render_salary(salary_mod.from_dict(record['salary']))}",
        f"- 读取范围：{record['source']['read_scope']}；完整：{'是' if completeness['is_complete'] else '否'}",
        (f"- 缺失：{', '.join(completeness['missing'])}" if completeness["missing"] else ""),
    ])
    return _emit(record, args.json, "\n".join(x for x in text.split("\n") if x))


def cmd_job_list(args) -> int:
    ws = _ws(args).require()
    rows = J.list_jobs(ws, limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("还没有记录职位。")
        return 0
    print(_table(["职位 ID", "公司", "职位", "地点", "完整"],
                 [[r["job_id"], truncate(r["company"], 16), truncate(r["title"], 20),
                   truncate(r["sections"].get("city") or r.get("location", ""), 10),
                   "是" if r["completeness"]["is_complete"] else "否"] for r in rows]))
    return 0


def cmd_job_show(args) -> int:
    ws = _ws(args).require()
    job = ws.find_job(args.job_id)
    if args.json:
        print(json.dumps(job, ensure_ascii=False, indent=2))
        return 0
    sections = job.get("sections") or {}
    print(f"{job['company']} · {job['title']}")
    print(f"地点：{sections.get('city') or job.get('location') or '—'}　工作方式：{sections.get('work_mode')}")
    print(f"薪资：{salary_mod.render_salary(salary_mod.from_dict(job.get('salary')))}")
    print(f"来源：{(job.get('source') or {}).get('url') or '用户提供'}　"
          f"读取时间：{(job.get('source') or {}).get('read_at')}")
    print(f"完整：{'是' if job['completeness']['is_complete'] else '否'}"
          f"　缺失：{', '.join(job['completeness']['missing']) or '无'}")
    ident = job.get("identity") or {}
    src = job.get("source") or {}
    status_label = {"verified": "页头匹配（辅助证据）",
                    "pending_review": "待核对",
                    "user_confirmed": "用户已确认（非系统核对）"}.get(
                        ident.get("status", ""), ident.get("status", "未记录"))
    print(f"身份状态：{status_label}"
          f"　岗位编号：{src.get('job_ref') or '未提供'}"
          f"　读取区域：{src.get('detail_region') or '未提供'}"
          f"　读取时间：{src.get('read_at') or '—'}")
    if ident.get("override"):
        override = ident["override"]
        print(f"⚠ 身份由用户覆盖：{override.get('statement')}"
              f"（证据：{override.get('evidence')}）")
    for key, label in (("responsibilities", "职责"), ("must_have", "必备条件"),
                       ("nice_to_have", "加分项"), ("education_experience", "学历/经验")):
        items = sections.get(key) or []
        if items:
            print(f"\n{label}（{len(items)}）：")
            for item in items[:12]:
                print(f"  - {truncate(item, 100)}")
    return 0


def cmd_job_confirm_identity(args) -> int:
    ws = _ws(args).require()
    job = J.confirm_identity(ws, args.job_id, statement=args.statement,
                             evidence=args.evidence)
    text = "\n".join([
        f"{job['job_id']} 身份已由用户确认（status=user_confirmed）",
        f"- 用户原话：{args.statement}",
        f"- 页面证据：{args.evidence}",
        "- 注意：这是用户陈述的记录，不是系统核对，也不是人类授权凭据；",
        "  导出物会在 manifest 与匹配报告中标注。",
    ])
    return _emit({"ok": True, "job_id": job["job_id"],
                  "identity": job["identity"]}, args.json, text)


def cmd_job_session(args) -> int:
    ws = _ws(args).require()
    state = J.mark_session(ws, args.session, status=args.status, reason=args.reason)
    return _emit({"ok": True, "session": state}, args.json,
                 f"会话 {args.session} → {args.status}"
                 + (f"（{args.reason}）" if args.reason else ""))


# ---------------------------------------------------------------------------
# commands: screening / mapping / rewrite
# ---------------------------------------------------------------------------


def cmd_screen(args) -> int:
    ws = _ws(args).require()
    prefs = read_json(_prefs_path(ws), None)
    if prefs is None:
        raise NotFoundError("还没有设置求职偏好。",
                            hint="先 `jfa prefs set --file prefs.json`，硬条件先于软偏好。")
    jobs = ws.load_jobs()
    if not jobs:
        raise NotFoundError("还没有记录职位。", hint="用 `jfa job add` 记录已读取的岗位。")
    if args.job_id:
        jobs = [j for j in jobs if j["job_id"] in set(args.job_id)]
    archive = None
    terms: set[str] | None = None
    if ws.facts_file.exists():
        archive = ws.load_facts()
        terms = set()
        for fact in F.usable_facts(archive):
            text = squash(fact.get("content", "")) + " " + squash(str(fact.get("structured", "")))
            terms.update(t.lower() for t in screen._TOKEN_RE.findall(text) if len(t) >= 2)
    result = screen.screen_all(jobs, prefs, facts_terms=terms, limit=args.limit)
    stored = ws.screen_file
    write_json(stored, result)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"筛选结果：合格 {result['counts'].get('eligible', 0)}，"
          f"待补信息 {result['counts'].get('conditional', 0) + result['counts'].get('insufficient_info', 0)}，"
          f"不合格 {result['counts'].get('ineligible', 0)}")
    print("口径：" + result["policy"]["note"])
    print()
    label = {"eligible": "合格", "conditional": "条件待确认", "insufficient_info": "信息不足",
             "ineligible": "不合格"}
    print(_table(["职位 ID", "公司", "职位", "结论", "硬条件", "软分", "薪资结论"],
                 [[r["job_id"][:12], truncate(r["company"], 14), truncate(r["title"], 18),
                   label.get(r["overall"], r["overall"]),
                   f"失败{len(r['hard_failures'])}/未知{len(r['hard_unknowns'])}",
                   f"{r['soft']['score']:.2f}",
                   r["salary"].get("verdict_label", "")]
                  for r in result["results"]]))
    print()
    print("注意：硬条件未知的岗位不会被当成不符合；薪资区间相交不等于确定能拿到目标薪资。")
    return 0


def _mapping_path(ws, job_id: str) -> Path:
    return ws.matches_dir / job_id / "latest.json"


def cmd_match_skeleton(args) -> int:
    ws = _ws(args).require()
    job = ws.find_job(args.job_id)
    archive = ws.load_facts() if ws.facts_file.exists() else F.new_archive()
    mapping = M.build_skeleton(job, archive)
    mapping["job_id"] = job["job_id"]
    version, path = ws.save_versioned(ws.matches_dir / job["job_id"], mapping)
    text = "\n".join([
        f"映射骨架已生成（v{version}）：{path.relative_to(ws.root)}",
        f"- 从岗位提取要求 {len(mapping['requirements'])} 条，全部先标为「缺少证据」",
        "- 宿主 agent 需要为每条填写 fact_ids / status / explanation 后回传校验",
        f"- 校验：`jfa match validate {job['job_id']} --file <填写后的文件>`",
    ])
    return _emit({"ok": True, "version": version, "path": str(path),
                  "requirements": len(mapping["requirements"])}, args.json, text)


def cmd_match_validate(args) -> int:
    ws = _ws(args).require()
    job = ws.find_job(args.job_id)
    archive = ws.load_facts()
    if args.file:
        incoming = read_json(Path(args.file), None)
        if incoming is None:
            raise NotFoundError(f"映射文件不存在：{args.file}")
        base = read_json(_mapping_path(ws, args.job_id), None) or M.empty_map(args.job_id)
        mapping = M.merge_agent_mapping(base, incoming) if base.get("requirements") else incoming
    else:
        mapping = read_json(_mapping_path(ws, args.job_id), None)
        if mapping is None:
            raise NotFoundError("还没有映射表。",
                                hint=f"先 `jfa match skeleton {args.job_id}`。")
    mapping["job_id"] = job["job_id"]
    issues = M.validate_map(mapping, archive)
    blocking = [i for i in issues if i["severity"] == "block"]
    mapping["coverage"] = M.compute_coverage(mapping)
    mapping["gaps"] = M.gaps(mapping)
    mapping["open_questions"] = M.high_value_questions(mapping, archive)
    mapping["issues"] = issues
    version, path = ws.save_versioned(ws.matches_dir / job["job_id"], mapping)
    coverage = mapping["coverage"]
    text = "\n".join([
        f"映射已校验并保存（v{version}）：{path.relative_to(ws.root)}",
        f"- 要求 {coverage['total']} 条（必备 {coverage['must_total']}）",
        f"- 必备覆盖率 {coverage['must_ratio']}　加分覆盖率 {coverage['nice_ratio']}　综合 {coverage['score']}",
        f"- 阻断问题 {len(blocking)}，警告 {len(issues) - len(blocking)}",
    ])
    if blocking:
        text += "\n\n阻断问题：\n" + "\n".join(f"- [{i['code']}] {i['message']}" for i in blocking[:10])
    if mapping["gaps"]:
        text += "\n\n缺口：\n" + "\n".join(
            f"- {g['requirement_id']}（{g['status_label']}）{truncate(g['requirement'], 60)}"
            for g in mapping["gaps"][:8])
    if mapping["open_questions"]:
        text += "\n\n建议追问（最多 3 个高价值问题）：\n" + "\n".join(
            f"- {q['question']}" for q in mapping["open_questions"])
    if args.json:
        print(json.dumps({"ok": not blocking, "issues": issues,
                          "coverage": coverage, "gaps": mapping["gaps"],
                          "open_questions": mapping["open_questions"],
                          "path": str(path)}, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 4 if blocking else 0


def cmd_match_show(args) -> int:
    ws = _ws(args).require()
    mapping = read_json(_mapping_path(ws, args.job_id), None)
    if mapping is None:
        raise NotFoundError("还没有映射表。", hint=f"先 `jfa match skeleton {args.job_id}`。")
    if args.json:
        print(json.dumps(mapping, ensure_ascii=False, indent=2))
        return 0
    print(reports.render_match_report(mapping, job=ws.find_job(args.job_id)))
    return 0


def cmd_rewrite_validate(args) -> int:
    ws = _ws(args).require()
    job = ws.find_job(args.job_id)
    archive = ws.load_facts()
    mapping = read_json(_mapping_path(ws, args.job_id), None)
    if args.file:
        plan = read_json(Path(args.file), None)
        if plan is None:
            raise NotFoundError(f"改写方案文件不存在：{args.file}")
    else:
        plan = read_json(ws.rewrite_file(args.job_id), None)
        if plan is None:
            raise NotFoundError("还没有改写方案。",
                                hint=f"用 `jfa rewrite template {args.job_id}` 取模板。")
    plan["job_id"] = job["job_id"]
    report = RW.validate_plan(plan, archive, job=job, mapping=mapping)
    doc = RW.build_resume_doc(archive, plan, job=job)
    trace = RW.check_traceability(doc, archive)
    report["traceability"] = trace
    if any(i.get("severity") == "block" for i in trace):
        report["ok"] = False
        report["blocking"] += [i for i in trace if i.get("severity") == "block"]
    readiness = RW.readiness(doc, report, mapping, job)
    if report["ok"]:
        version, path = ws.save_versioned(ws.rewrites_dir / job["job_id"], plan)
        report["saved_version"] = version
        report["saved_path"] = str(path.relative_to(ws.root))
    stats = report["stats"]
    text = "\n".join([
        f"改写方案校验：{'通过' if report['ok'] else '未通过'}",
        f"- 改动 {stats['changes']} 条：已核验 {stats['verified']}，待确认 {stats['pending']}",
        f"- 阻断 {stats['blocking']} 条，警告 {stats['warnings']} 条",
        f"- 结论：{readiness['statement']}",
    ])
    if report["blocking"]:
        text += "\n\n阻断问题：\n" + "\n".join(
            f"- [{i['code']}] {i['message']}" for i in report["blocking"][:15])
    if report["warnings"]:
        text += "\n\n警告：\n" + "\n".join(
            f"- [{i['code']}] {i['message']}" for i in report["warnings"][:10])
    if doc.get("pending_items"):
        text += f"\n\n{len(doc['pending_items'])} 条待确认内容不会写入简历。"
    if report.get("saved_version"):
        text += f"\n\n已保存为 v{report['saved_version']}。"
    if args.json:
        print(json.dumps({**report, "readiness": readiness,
                          "pending_items": doc.get("pending_items")},
                         ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0 if report["ok"] else 4


def cmd_rewrite_show(args) -> int:
    ws = _ws(args).require()
    plan = read_json(ws.rewrite_file(args.job_id), None)
    if plan is None:
        raise NotFoundError("还没有改写方案。", hint=f"用 `jfa rewrite template {args.job_id}`。")
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if args.text:
        archive = ws.load_facts()
        job = ws.find_job(args.job_id)
        doc = RW.build_resume_doc(archive, plan, job=job)
        sys.stdout.write(RW.render_text(doc))
        return 0
    print(reports.render_change_diff(plan, job=ws.find_job(args.job_id),
                                    mapping=read_json(_mapping_path(ws, args.job_id), None)))
    return 0


def cmd_rewrite_template(args) -> int:
    ws = _ws(args).require()
    job = ws.find_job(args.job_id)
    archive = ws.load_facts() if ws.facts_file.exists() else F.new_archive()
    mapping = read_json(_mapping_path(ws, args.job_id), None) or M.empty_map(args.job_id)
    plan = RW.empty_plan(job["job_id"])
    plan["target_title"]["text"] = job["title"]
    plan["target_title"]["verification_status"] = "pending_confirmation"
    for fact in F.usable_facts(archive):
        if fact.get("kind") == F.KIND_EMPLOYMENT:
            plan["experiences"].append({
                "fact_id": fact["fact_id"],
                "order": len(plan["experiences"]) + 1,
                "omit": False,
                "omit_reason": "",
                "bullets": [],
            })
    must = [r["requirement_id"] for r in mapping.get("requirements") or []
            if r.get("importance") == M.IMPORTANCE_MUST]
    plan["notes"] = [
        "每条 bullets 必须填写 before / after / change_reason / requirement_ids / fact_ids。",
        "客户公司、职位、时间由事实档案注入，方案中不可改写。",
        "数字必须来自事实档案，否则导出会被阻断。",
        f"岗位必备要求编号：{', '.join(must) or '（映射表为空）'}",
    ]
    path = ws.root / PLAN_FILE
    write_json(path, plan)
    print(f"改写方案模板已写入：{path}")
    print("填写后用 `jfa rewrite validate <job_id> --file change_plan.json` 校验并保存。")
    return 0


# ---------------------------------------------------------------------------
# commands: export / purge / selftest
# ---------------------------------------------------------------------------


def cmd_export(args) -> int:
    from .export import export_bundle

    ws = _ws(args).require()
    job = ws.find_job(args.job_id)
    archive = ws.load_facts()
    plan = read_json(ws.rewrite_file(args.job_id), None)
    if plan is None:
        raise NotFoundError("还没有通过校验的改写方案。",
                            hint=f"先 `jfa rewrite validate {args.job_id} --file <方案>`。")
    mapping = read_json(_mapping_path(ws, args.job_id), None)
    version = args.version or int(plan.get("version") or 1)
    screening = None
    if ws.screen_file.exists():
        data = read_json(ws.screen_file, {})
        for row in data.get("results") or []:
            if row.get("job_id") == job["job_id"]:
                screening = row
                break

    result = export_bundle(
        ws, job, plan=plan, mapping=mapping, archive=archive, screening=screening,
        version=version,
        style_path=Path(args.style) if args.style else None,
        photo_path=Path(args.photo) if args.photo else None,
        max_pages=args.max_pages or None,
        min_font_pt=args.min_font_pt or None,
        include_pending=args.allow_pending,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"导出目录：{Path(result['docx']).parent}")
        print(f"- 简历 DOCX：{Path(result['docx']).name}")
        if result["pdf"]:
            print(f"- 简历 PDF：{Path(result['pdf']).name}（后端 {result['backend']}，{result['pages']} 页）")
        else:
            print(f"- 简历 PDF：失败 — {result['pdf_error']}")
            if result["pdf_hint"]:
                print(f"  安装建议：{result['pdf_hint']}")
        print(f"- 纯文本：{Path(result['txt']).name}")
        print("- 报告：修改对照 / 匹配与缺口 / 事实与待确认 / 打招呼草稿（同名 .md）")
        print(f"- 正文字号：{result['body_pt']}pt　试排记录：{result['fit_attempts']}")
        if result["verification"]["available"]:
            rep = result["verification_report"]
            print(f"- 导出校验：{rep['summary']['verdict']}"
                  f"（通过 {rep['summary']['passed']}，失败 {rep['summary']['failed']}）")
            for failure in result["verification_report"]["failures"]:
                print(f"   ✗ {failure['name']}：{failure['detail']}")
        else:
            print("- 导出校验：未执行（没有 PDF，无法逐页检查）")
        ready = result["readiness"]
        print(f"- 完成度：{ready['statement']}")
        if result["pending_items"]:
            print(f"- 待确认未写入：{result['pending_items']} 条（见事实与待确认清单）")
    return 0 if result["ok"] else 5


def cmd_schema_list(args) -> int:
    from . import schema as schema_mod

    rows = schema_mod.list_schemas()
    return _emit(rows, args.json, schema_mod.render_list(rows))


def cmd_schema_check(args) -> int:
    from . import schema as schema_mod

    path = Path(args.file)
    if not path.exists():
        raise NotFoundError(f"文件不存在：{path}")
    problems = schema_mod.validate_file(args.kind, path)
    if args.json:
        print(json.dumps({"ok": not problems, "kind": args.kind,
                          "problems": problems}, ensure_ascii=False, indent=2))
    else:
        if not problems:
            print(f"通过：{path.name} 符合 {args.kind} schema。")
        else:
            print(f"{path.name} 有 {len(problems)} 处不符合 {args.kind} schema：")
            for problem in problems[:30]:
                print(f"  - {problem['path']}: {problem['message']}")
    return 0 if not problems else 4


def encode_label(kind: str) -> str:  # pragma: no cover - helper for readability
    return kind.replace("_", " ")


def cmd_purge(args) -> int:
    ws = _ws(args).require()
    if not args.yes:
        print("未删除任何文件。")
        print(f"将删除的工作区：{ws.root}")
        print("确认后请加 --yes。")
        print("注意：只删除本工具写入的本地资料；招聘平台、浏览器与宿主云端的数据不在本工具控制范围内。")
        return 0
    result = ws.purge()
    text = "\n".join([
        f"已删除 {len(result['removed'])} 项本地数据（{result['workspace_root']}）：",
        *[f"- {item}" for item in result["removed"]],
        "",
        f"仍保留：{', '.join(result['still_exists'])}",
        "",
        result["third_party_notice"],
    ])
    return _emit(result, args.json, text)


def cmd_selftest(args) -> int:
    """End-to-end smoke test entirely in memory + a temp dir."""
    import tempfile

    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    text = (
        "张三\n13800001111 | zhangsan@example.com | 上海\n"
        "工作经历\n"
        "某某科技有限公司 | 后端开发工程师 | 2021.03 - 2023.06\n"
        "协助团队完成后端接口开发，参与订单系统重构\n"
        "教育背景\n"
        "某某大学 计算机科学与技术 本科 2017.09 - 2021.06\n"
        "技能\n"
        "Python、MySQL、Redis\n"
    )
    blocks: list[B.Block] = []
    builder = B.BlockBuilder("selftest")
    for i, line in enumerate(text.split("\n"), 1):
        if line.strip():
            blocks.append(builder.add(line, method="plain_text", locator={"line": i}))

    archive = F.build_from_blocks(F.new_archive(), blocks, source_file="selftest.txt",
                                  source_id="selftest")
    check("blocks", len(blocks) > 0, f"{len(blocks)} 块")
    check("facts", len(archive["facts"]) > 0, f"{len(archive['facts'])} 条事实")
    check("tenure", archive["tenure"]["total_months"] > 0,
          archive["tenure"].get("total_text", ""))

    job = {
        "job_id": "job-selftest", "company": "示例公司", "title": "后端开发工程师",
        "location": "上海",
        "sections": {"city": "上海", "work_mode": "坐班",
                     "responsibilities": ["负责订单系统后端开发"],
                     "must_have": ["3 年以上 Python 经验", "本科及以上"],
                     "nice_to_have": ["有高并发经验"], "salary_raw": "20-30K·13薪",
                     "education_experience": ["本科"]},
        "salary": salary_mod.parse_salary("20-30K·13薪").to_dict(),
        "completeness": {"is_complete": True, "missing": []},
        "source": {"read_at": now_iso(), "read_scope": "full"},
    }
    sal = salary_mod.from_dict(job["salary"])
    check("salary", sal.min_monthly == 20000 and sal.months_per_year == 13,
          f"{sal.min_monthly}-{sal.max_monthly} × {sal.months_per_year}")

    prefs = screen.empty_prefs()
    prefs["hard"].update({"cities": ["上海"], "min_monthly_base": 15000, "target_annual": 300000})
    result = screen.screen_job(job, prefs)
    check("screen", result["overall"] in {"eligible", "conditional"}, result["overall"])

    mapping = M.build_skeleton(job, archive)
    direct_fact = next((f["fact_id"] for f in archive["facts"]
                        if f["kind"] == F.KIND_SKILL), "")
    if direct_fact and mapping["requirements"]:
        mapping["requirements"][0].update({
            "fact_ids": [direct_fact], "status": M.STATUS_TRANSFERABLE,
            "explanation": "技能相关但量级不同，属于可迁移",
        })
    issues = M.validate_map(mapping, archive)
    check("mapping_validation", not [i for i in issues if i["severity"] == "block"],
          f"{len(issues)} 条提示")

    plan = RW.empty_plan(job["job_id"])
    plan["target_title"]["text"] = "后端开发工程师"
    plan["target_title"]["verification_status"] = "verified"
    plan["target_title"]["fact_ids"] = [archive["facts"][0]["fact_id"]]
    emp = next((f for f in archive["facts"] if f["kind"] == F.KIND_EMPLOYMENT), None)
    if emp:
        plan["experiences"].append({
            "fact_id": emp["fact_id"], "order": 1, "omit": False, "omit_reason": "",
            "bullets": [{
                "before": "协助团队完成后端接口开发，参与订单系统重构",
                "after": "协助团队完成后端接口开发，参与订单系统重构",
                "change_reason": "保持原职责强度",
                "requirement_ids": ["R-001"],
                "fact_ids": [emp["fact_id"]],
                "verification_status": "verified",
            }],
        })
    bad_plan = json.loads(json.dumps(plan))
    if bad_plan["experiences"]:
        bad_plan["experiences"][0]["bullets"][0]["after"] = "主导订单系统重构，效率提升 300%"
    bad_report = RW.validate_plan(bad_plan, archive, job=job, mapping=mapping)
    check("anti_inflation", not bad_report["ok"],
          f"拦截 {len(bad_report['blocking'])} 处（自造数字/越级动词）")
    good_report = RW.validate_plan(plan, archive, job=job, mapping=mapping)
    check("plan_ok", good_report["ok"],
          f"{len(good_report['blocking'])} 阻断 / {len(good_report['warnings'])} 警告")

    doc_model = RW.build_resume_doc(archive, plan, job=job)
    check("doc_model", bool(doc_model["experiences"]), f"{doc_model['stats']}")
    rendered = RW.render_text(doc_model)
    check("render_text", "张三" in rendered and "某某科技" in rendered, f"{len(rendered)} 字符")

    tmp = Path.cwd() / ".jfa-selftest"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        from .export import docx_out

        style = docx_out.load_style(None)
        target = tmp / "selftest.docx"
        try:
            info = docx_out.render(doc_model, style, target)
            check("docx_render", target.exists() and target.stat().st_size > 1000,
                  f"{info['size_bytes']} 字节")
        except Exception as exc:
            check("docx_render", False, str(exc)[:120])
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(c["ok"] for c in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    else:
        print("自检结果：" + ("全部通过" if ok else "存在失败项"))
        for check_row in checks:
            print(f"  [{'✓' if check_row['ok'] else '✗'}] {check_row['name']}：{check_row['detail']}")
        print("\n说明：自检只用内置样例文本，不涉及你的真实简历。")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
