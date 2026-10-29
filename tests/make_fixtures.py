# -*- coding: utf-8 -*-
"""Build the test fixtures used by the test suite.

Fixtures are generated (not committed as binaries) so the repo stays text-only
and every fixture is reproducible. Run:

    python tests/make_fixtures.py

Everything written here is synthetic: names, phone numbers and companies are
made up. No real resume is ever used in tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "job-fit-assistant" / "scripts"))

FIXTURES = HERE / "fixtures"

RESUME_TEXT = """张三
13800001111 | zhangsan@example.com | 上海 | github.com/zhangsan

工作经历
星河数据科技有限公司 | 后端开发工程师 | 2021.03 - 2023.06
协助团队完成订单系统接口开发，参与重构订单查询链路，将平均响应时间从 800ms 降到 300ms
负责编写接口文档与值班排查，累计处理线上工单 60 余个
云帆网络有限公司 | 初级开发工程师 | 2019.07 - 2021.02
参与内部管理后台开发，使用 Python 与 MySQL 完成报表导出模块

教育背景
云帆大学 计算机科学与技术 本科 2015.09 - 2019.06

技能
Python、MySQL、Redis、Docker、Git

证书与荣誉
软件设计师（中级）
"""

JOB_FULL = """后端开发工程师
示例科技有限公司 · 上海 · 20-30K·13薪
发布于 2026-10-20

岗位职责
1. 负责订单与支付核心链路的服务端设计与开发
2. 参与系统性能优化，保障高并发场景下的稳定性
3. 与产品、测试协作推进需求落地

任职要求
1. 本科及以上学历，计算机相关专业
2. 3 年以上后端开发经验，熟悉 Python 或 Go
3. 熟悉 MySQL、Redis，了解消息队列

加分项
1. 有高并发系统经验
2. 有支付或电商业务经验
"""

JOB_PARTIAL = """高级后端工程师
云帆网络有限公司 · 上海

岗位职责
- 负责基础架构演进

（页面未加载完整，任职要求与薪资区域未读取）
"""

JOB_SECOND = """Python 后端工程师
星河数据科技有限公司 · 上海 · 面议

岗位职责
负责数据平台的接口开发与维护

任职要求
1. 本科及以上
2. 熟悉 Python，了解 Kubernetes 者优先

加分项
有数据仓库经验
"""


def build_text_fixtures() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "resume.txt").write_text(RESUME_TEXT, encoding="utf-8", newline="\n")
    (FIXTURES / "job_full.txt").write_text(JOB_FULL, encoding="utf-8", newline="\n")
    (FIXTURES / "job_partial.txt").write_text(JOB_PARTIAL, encoding="utf-8", newline="\n")
    (FIXTURES / "job_second.txt").write_text(JOB_SECOND, encoding="utf-8", newline="\n")


def build_docx() -> None:
    """DOCX with a table, a header/footer and a text box.

    The text box exists because plain `python-docx` parsing silently drops it —
    the extractor must find it and flag it.
    """
    import docx
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    from docx.shared import Pt

    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)

    document.add_paragraph("张三")
    p = document.add_paragraph("13800001111 | zhangsan@example.com | 上海")

    document.add_paragraph("工作经历")
    document.add_paragraph("星河数据科技有限公司 | 后端开发工程师 | 2021.03 - 2023.06")
    document.add_paragraph(
        "协助团队完成订单系统接口开发，参与重构订单查询链路，"
        "将平均响应时间从 800ms 降到 300ms"
    )
    document.add_paragraph("负责编写接口文档与值班排查，累计处理线上工单 60 余个")
    document.add_paragraph("云帆网络有限公司 | 初级开发工程师 | 2019.07 - 2021.02")
    document.add_paragraph("参与内部管理后台开发，使用 Python 与 MySQL 完成报表导出模块")

    document.add_paragraph("教育背景")
    table = document.add_table(rows=1, cols=4)
    cells = table.rows[0].cells
    for idx, value in enumerate(("云帆大学", "计算机科学与技术", "本科", "2015.09 - 2019.06")):
        cells[idx].text = value

    document.add_paragraph("技能")
    document.add_paragraph("Python、MySQL、Redis、Docker、Git")

    # text box: extra certification that plain paragraph parsing would miss
    _w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _v = "urn:schemas-microsoft-com:vml"
    textbox = parse_xml(
        f'<w:p xmlns:w="{_w}" xmlns:v="{_v}">'
        '<w:r><w:pict><v:shape style="width:200pt;height:30pt" fillcolor="white">'
        '<v:textbox><w:txbxContent>'
        '<w:p><w:r><w:t>证书：软件设计师（中级）</w:t></w:r></w:p>'
        '</w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>'
    )
    document.element.body.append(textbox)

    section = document.sections[0]
    section.header.paragraphs[0].text = "张三 · 后端开发工程师"
    section.footer.paragraphs[0].text = "本简历为合成测试数据"

    target = FIXTURES / "resume.docx"
    document.save(str(target))


def build_pdf() -> None:
    """Same person, different format: the usual "DOCX + exported PDF" pair.

    Ingesting both must not double the employment history (see facts.dedupe).
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4 in points
    lines = [
        "张三",
        "13800001111 | zhangsan@example.com | 上海",
        "工作经历",
        "星河数据科技有限公司 | 后端开发工程师 | 2021.03 - 2023.06",
        "协助团队完成订单系统接口开发，参与重构订单查询链路，将平均响应时间从 800ms 降到 300ms",
        "负责编写接口文档与值班排查，累计处理线上工单 60 余个",
        "云帆网络有限公司 | 初级开发工程师 | 2019.07 - 2021.02",
        "参与内部管理后台开发，使用 Python 与 MySQL 完成报表导出模块",
        "教育背景",
        "云帆大学 计算机科学与技术 本科 2015.09 - 2019.06",
        "技能",
        "Python、MySQL、Redis、Docker、Git",
    ]
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s", fontsize=11)
        y += 20
    doc.save(str(FIXTURES / "resume_text.pdf"))
    doc.close()


def build_scanned_pdf() -> None:
    """Image-only PDF: exercises the per-page OCR decision and failure path."""
    import pymupdf

    src = pymupdf.open(str(FIXTURES / "resume_text.pdf"))
    pix = src[0].get_pixmap(dpi=150)
    jpg = FIXTURES / "resume_scanned_page.jpg"
    pix.save(str(jpg), jpg_quality=72)   # keep the fixture small; it is image-only anyway
    src.close()

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(jpg))
    doc.save(str(FIXTURES / "resume_scanned.pdf"), deflate=True, garbage=3)
    doc.close()


def build_image_with_sidecar() -> None:
    """PNG + hand-written sidecar for the test-only OCR provider.

    Same person and same content as the DOCX, so the two can be compared. The
    sidecar is deliberately *not* the truth: one wrong phone digit and one
    impossible month, so the review workflow has something to catch.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [
        "张三",
        "13800001111 | zhangsan@example.com | 上海",
        "工作经历",
        "星河数据科技有限公司 | 后端开发工程师 | 2021.03 - 2023.06",
        "协助团队完成订单系统接口开发，参与重构订单查询链路",
    ]
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s", fontsize=11)
        y += 20
    pix = page.get_pixmap(dpi=150)
    png = FIXTURES / "resume_image.png"
    pix.save(str(png))
    doc.close()

    sidecar = {
        "model": "hand-authored-fixture",
        "note": "测试旁车：故意包含 1 处电话号码错位与 1 处异常月份",
        "lines": [
            {"text": "张三", "confidence": 0.98, "bbox": [72, 55, 110, 75]},
            {"text": "13800001111 | zhangsan@example.com | 上海", "confidence": 0.71,
             "bbox": [72, 75, 380, 95]},
            {"text": "工作经历", "confidence": 0.95, "bbox": [72, 95, 140, 115]},
            {"text": "星河数据科技有限公司 | 后端开发工程师 | 2021.03 - 2023.13",
             "confidence": 0.62, "bbox": [72, 115, 430, 135]},
            {"text": "协助团队完成订单系统接口开发，参与重构订单查询链路", "confidence": 0.88,
             "bbox": [72, 135, 440, 155]},
        ],
    }
    (FIXTURES / "resume_image.png.ocr.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_prefs() -> None:
    prefs = {
        "schema": "jfa.preferences/1",
        "hard": {
            "cities": ["上海", "杭州"],
            "work_modes": ["坐班", "混合办公"],
            "min_monthly_base": 18000,
            "target_annual": 360000,
            "required_degree_max": "本科",
            "exclude_companies": ["某已离职公司"],
            "must_have_keywords": [],
            "exclude_keywords": [],
        },
        "soft": {
            "weights": {"title_match": 0.35, "skill_overlap": 0.35,
                        "salary_fit": 0.2, "location_fit": 0.1},
            "preferred_titles": ["后端开发工程师", "Python 后端工程师"],
            "preferred_industries": ["数据", "电商"],
        },
    }
    (FIXTURES / "prefs.json").write_text(
        json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    build_text_fixtures()
    build_prefs()
    build_docx()
    build_pdf()
    build_scanned_pdf()
    build_image_with_sidecar()
    for path in sorted(FIXTURES.iterdir()):
        print(f"{path.name:34s} {path.stat().st_size:>9,d} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
