# -*- coding: utf-8 -*-
"""Built-in PDF renderer (PyMuPDF, embedded CJK font).

Why this exists: DOCX→PDF through LibreOffice or Word needs an external
application, and both can be missing, licence-restricted, or wedged behind a
modal dialog. Without a fallback the tool would have to either fail or hand back
an empty file. This renderer draws the *same* document model directly, so a
selectable, searchable PDF is always produced from verified content.

Trade-off, stated plainly in the report: this backend's line breaking and
spacing differ from Word's, so it is the last choice, not the first.

Font handling: a real system CJK font (YaHei / PingFang / Noto) is embedded when
one exists, including a real bold face. The built-in "china-s" font is only a
fallback — it draws Latin at full width, which looks wrong in a resume.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..errors import DependencyMissing, ExportError

CM = 28.346456692913385  # points per centimetre
PAGE_W, PAGE_H = 595.0, 842.0  # A4 in points

_CJK_RANGES = (
    (0x2E80, 0x9FFF), (0x3000, 0x303F), (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF), (0x2000, 0x206F),
)

#: preferred system fonts, per platform, regular then bold
FONT_CANDIDATES: dict[str, list[str]] = {
    "regular": [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    ],
    "bold": [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\Dengb.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    ],
}

BUILTIN_FONT = "china-s"


def available() -> bool:
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False
    return True


def find_font(weight: str) -> str:
    for candidate in FONT_CANDIDATES.get(weight, []):
        if Path(candidate).exists():
            return candidate
    return ""


def font_report() -> dict:
    return {
        "regular": find_font("regular") or BUILTIN_FONT,
        "bold": find_font("bold") or "",
        "note": (
            "使用系统中文字体，Latin 与中文均为正常字宽"
            if find_font("regular") else
            "未找到系统中文字体，退回 PyMuPDF 内置字体；Latin 会被排成全角，"
            "版面可用但不算美观。"
        ),
    }


def render(
    doc_model: dict,
    style: dict,
    out_path: Path,
    *,
    photo_path: Path | None = None,
    body_pt: float | None = None,
) -> dict:
    try:
        import pymupdf
    except ImportError as exc:
        raise DependencyMissing(
            "缺少 PyMuPDF，无法使用内置 PDF 渲染器。",
            hint="pip install -r scripts/requirements.txt",
        ) from exc

    layout = _Layout(pymupdf, doc_model, style, body_pt=body_pt)
    layout.draw()
    layout.finish(photo_path=photo_path)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # A full CJK font is ~20MB. Subset it to the glyphs actually used, otherwise
    # every exported resume is a 20MB attachment.
    try:
        layout.doc.subset_fonts(verbose=False)
    except Exception:
        pass
    layout.doc.save(str(out_path), garbage=4, deflate=True, clean=True)
    pages = layout.doc.page_count
    layout.doc.close()
    if not out_path.exists() or out_path.stat().st_size < 1024:
        raise ExportError("内置 PDF 渲染器没有生成有效文件。")
    return {"path": str(out_path), "pages": pages, "backend": "builtin",
            "body_pt": layout.body_pt, "size_bytes": out_path.stat().st_size,
            "fonts": layout.font_names}


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


class _Layout:
    def __init__(self, pymupdf, doc_model: dict, style: dict, *, body_pt: float | None):
        self.pymupdf = pymupdf
        self.model = doc_model
        self.style = style
        sizes = dict(style.get("sizes", {}))
        self.body_pt = float(body_pt or sizes.get("body", 10))
        delta = self.body_pt - float(sizes.get("body", 10))
        self.sizes = {k: round(float(v) + (delta if k in {"body", "meta", "contact"} else 0), 2)
                      for k, v in sizes.items()}

        self.regular_file = find_font("regular")
        self.bold_file = find_font("bold")
        self.font_names = {"regular": "jfaR", "bold": "jfaB" if self.bold_file else "jfaR"}
        self._metrics: dict[str, Any] = {}

        colors = style.get("colors", {})
        self.colors = {
            "text": _rgb(colors.get("text", "1A1A1A")),
            "accent": _rgb(colors.get("accent", "1F4E5F")),
            "muted": _rgb(colors.get("muted", "4A4A4A")),
        }
        margins = (style.get("page", {}) or {}).get("margins_cm", {})
        self.margin_l = float(margins.get("left", 1.7)) * CM
        self.margin_r = float(margins.get("right", 1.7)) * CM
        self.margin_t = float(margins.get("top", 1.5)) * CM
        self.margin_b = float(margins.get("bottom", 1.5)) * CM
        self.width = PAGE_W - self.margin_l - self.margin_r
        self.bottom = PAGE_H - self.margin_b
        self.labels = style.get("labels", {})
        layout_cfg = style.get("layout", {}) or {}
        self.bullet_char = layout_cfg.get("bullet_char", "·")
        self.section_rule = layout_cfg.get("section_rule", True)
        self.doc = pymupdf.open()
        self.page = None
        self.y = 0.0
        self.new_page()

    # ---------------------------------------------------------------- pages
    def new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        if self.regular_file:
            for name, path in ((self.font_names["regular"], self.regular_file),
                               (self.font_names["bold"], self.bold_file)):
                if not path:
                    continue
                try:
                    self.page.insert_font(fontname=name, fontfile=path)
                except Exception:
                    pass
        self.y = self.margin_t

    def ensure(self, height: float) -> None:
        if self.y + height > self.bottom:
            self.new_page()

    # ------------------------------------------------------------- fonts
    def _font(self, bold: bool):
        """(fontname, fontfile-or-None, metric-font) for this weight."""
        if bold and self.bold_file:
            return self.font_names["bold"], self.bold_file, self._metric(self.bold_file)
        if self.regular_file:
            return (self.font_names["regular"], self.regular_file,
                    self._metric(self.regular_file))
        return BUILTIN_FONT, None, None

    def _metric(self, path: str):
        if path not in self._metrics:
            try:
                self._metrics[path] = self.pymupdf.Font(fontfile=path)
            except Exception:
                self._metrics[path] = None
        return self._metrics[path]

    def text_width(self, text: str, size: float, *, bold: bool = False) -> float:
        _, _, metric = self._font(bold)
        if metric is not None:
            return metric.text_length(text, fontsize=size)
        return self.pymupdf.get_text_length(text, fontname=BUILTIN_FONT, fontsize=size)

    def wrap(self, text: str, size: float, width: float, *, bold: bool = False) -> list[str]:
        budget = width * 0.98
        lines: list[str] = []
        for block in str(text).split("\n"):
            lines.extend(self._wrap_one(block, size, budget, bold=bold))
        return lines or [""]

    def _wrap_one(self, text: str, size: float, width: float, *, bold: bool = False) -> list[str]:
        if not text:
            return [""]
        tokens = _tokenize(text)
        out: list[str] = []
        current = ""
        for token in tokens:
            candidate = current + token
            if self.text_width(candidate, size, bold=bold) <= width or not current:
                current = candidate
            else:
                out.append(current.rstrip())
                current = token.lstrip()
        if current:
            out.append(current.rstrip())
        return out

    # --------------------------------------------------------------- drawing
    def text_line(self, x: float, text: str, size: float, color, *, bold: bool = False) -> None:
        name, path, _ = self._font(bold)
        kwargs: dict[str, Any] = {"fontname": name, "fontsize": size, "color": color}
        if path:
            kwargs["fontfile"] = path
        self.page.insert_text((x, self.y + size), text, **kwargs)

    def paragraph(
        self,
        text: str,
        *,
        size: float,
        color=None,
        bold: bool = False,
        indent: float = 0.0,
        first_indent: float = 0.0,
        line_height: float = 1.32,
        space_before: float = 0.0,
        space_after: float = 2.0,
        keep_with_next: bool = False,
    ) -> None:
        color = color or self.colors["text"]
        width = self.width - indent
        lines = self.wrap(text, size, width, bold=bold)
        line_h = size * line_height
        block_height = len(lines) * line_h + space_before + space_after
        if keep_with_next:
            self.ensure(block_height + line_h)
        else:
            self.ensure(block_height)
        self.y += space_before
        for index, line in enumerate(lines):
            self.ensure(line_h)
            offset = indent + (first_indent if index == 0 else 0)
            self.text_line(self.margin_l + offset, line, size, color, bold=bold)
            self.y += line_h
        self.y += space_after

    def bullet(self, text: str, *, size: float, color=None) -> None:
        indent = 13.0
        width = self.width - indent
        lines = self.wrap(text, size, width)
        line_h = size * 1.32
        self.ensure(len(lines) * line_h)
        for index, line in enumerate(lines):
            self.ensure(line_h)
            if index == 0:
                self.text_line(self.margin_l + 2, self.bullet_char, size, self.colors["muted"])
            self.text_line(self.margin_l + indent, line, size, color or self.colors["text"])
            self.y += line_h
        self.y += 1.0

    def section(self, title: str) -> None:
        size = self.sizes.get("section", 10.5)
        self.ensure(size * 3.2)
        self.y += 3
        self.text_line(self.margin_l, title, size, self.colors["accent"], bold=True)
        if self.section_rule:
            rule_y = self.y + size * 1.28
            self.page.draw_line(
                self.pymupdf.Point(self.margin_l, rule_y),
                self.pymupdf.Point(PAGE_W - self.margin_r, rule_y),
                color=self.colors["accent"], width=0.6,
            )
        self.y += size * 1.9

    def head_with_date(self, head: str, date: str, *, size: float, bold_head: bool = True) -> None:
        """One line: title on the left, date flush right — or date below if tight."""
        meta = self.sizes.get("meta", 9.5)
        head_w = self.text_width(head, size, bold=bold_head) if head else 0.0
        date_w = self.text_width(date, meta) if date else 0.0
        self.ensure(size * 2)
        self.text_line(self.margin_l, head, size, self.colors["text"], bold=bold_head)
        if date and head_w + date_w + 16 <= self.width:
            x = PAGE_W - self.margin_r - date_w
            self.text_line(x, date, meta, self.colors["muted"])
            self.y += size * 1.45
        elif date:
            self.y += size * 1.45
            self.ensure(meta * 1.6)
            x = PAGE_W - self.margin_r - date_w
            self.text_line(x, date, meta, self.colors["muted"])
            self.y += meta * 1.5
        else:
            self.y += size * 1.45

    # ----------------------------------------------------------------- build
    def draw(self) -> None:
        model = self.model
        name = str(model.get("name") or "").strip()
        if name:
            self.paragraph(name, size=self.sizes.get("name", 19), bold=True,
                           line_height=1.25, space_after=1)
        target = str(model.get("target_title") or "").strip()
        if target:
            self.paragraph(target, size=self.sizes.get("target_title", 10.5),
                           color=self.colors["accent"], line_height=1.3, space_after=2)
        contacts = [c["text"] for c in model.get("contacts") or [] if c.get("text")]
        if contacts:
            self.paragraph("  |  ".join(contacts), size=self.sizes.get("contact", 9.5),
                           color=self.colors["muted"], line_height=1.3, space_after=5)

        order = (self.style.get("layout", {}) or {}).get(
            "sections", ["summary", "skills", "experience", "education", "achievements"])

        if "summary" in order and model.get("summary"):
            self.section(self.labels.get("summary", "个人简介"))
            self.paragraph(model["summary"]["text"], size=self.sizes.get("body", 10))

        if "skills" in order and model.get("skills"):
            self.section(self.labels.get("skills", "核心技能"))
            for skill in model["skills"]:
                self.bullet(skill["text"], size=self.sizes.get("body", 10))

        if "experience" in order and model.get("experiences"):
            self.section(self.labels.get("experience", "工作经历"))
            for exp in model["experiences"]:
                head = "  |  ".join(x for x in (exp.get("company"), exp.get("title")) if x)
                self.y += 2
                self.head_with_date(head, exp.get("period") or "",
                                    size=self.sizes.get("body", 10))
                for bullet in exp.get("bullets") or []:
                    text = bullet["text"]
                    if bullet.get("verification_status") == "pending_confirmation":
                        text = f"【待确认】{text}"
                    self.bullet(text, size=self.sizes.get("body", 10))

        if "education" in order and model.get("education"):
            self.section(self.labels.get("education", "教育背景"))
            for edu in model["education"]:
                head = "  |  ".join(x for x in (edu.get("school"), edu.get("degree"),
                                                edu.get("major")) if x) or edu.get("text", "")
                self.head_with_date(head, edu.get("date_raw") or "",
                                    size=self.sizes.get("body", 10), bold_head=False)
                self.y += 1

        if "achievements" in order and model.get("achievements"):
            self.section(self.labels.get("achievements", "证书与荣誉"))
            for item in model["achievements"]:
                self.bullet(item["text"], size=self.sizes.get("body", 10))

    def finish(self, *, photo_path: Path | None = None) -> None:
        photo_cfg = (self.style.get("layout", {}) or {}).get("photo", {}) or {}
        if photo_cfg.get("enabled") and photo_path and Path(photo_path).exists():
            try:
                width = float(photo_cfg.get("width_cm", 2.6)) * CM
                rect = self.pymupdf.Rect(PAGE_W - self.margin_r - width,
                                         self.margin_t, PAGE_W - self.margin_r,
                                         self.margin_t + width * 1.35)
                self.doc[0].insert_image(rect, filename=str(photo_path), keep_proportion=True)
            except Exception:
                pass

        total = self.doc.page_count
        size = 8.5
        for index in range(total):
            page = self.doc[index]
            label = f"第 {index + 1} 页 / 共 {total} 页"
            width = self.text_width(label, size)
            y = PAGE_H - self.margin_b * 0.55
            name, path, _ = self._font(False)
            kwargs: dict[str, Any] = {"fontname": name, "fontsize": size,
                                      "color": self.colors["muted"]}
            if path:
                kwargs["fontfile"] = path
            page.insert_text(((PAGE_W - width) / 2, y), label, **kwargs)


def _rgb(hex_value: str) -> tuple[float, float, float]:
    value = str(hex_value).lstrip("#")
    if len(value) != 6:
        value = "1A1A1A"
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _tokenize(text: str) -> list[str]:
    """Split into CJK characters and Latin words so wrapping respects words."""
    tokens: list[str] = []
    buffer = ""
    for ch in text:
        if _is_cjk(ch) or ch.isspace():
            if buffer:
                tokens.append(buffer)
                buffer = ""
            tokens.append(ch)
        else:
            buffer += ch
    if buffer:
        tokens.append(buffer)
    glued: list[str] = []
    for token in tokens:
        if glued and not glued[-1].strip() and token.strip():
            glued[-1] = glued[-1] + token
        else:
            glued.append(token)
    return glued
