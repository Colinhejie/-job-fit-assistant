# -*- coding: utf-8 -*-
"""OCR provider interface.

The interface is deliberately swappable: `providers()` returns whatever is
actually importable, and calling an unavailable provider raises
`OcrUnavailable` with install guidance. Nothing here fabricates text.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...errors import OcrUnavailable


@dataclass
class OcrLine:
    text: str
    confidence: float | None = None
    bbox: list[float] | None = None      # [x0, y0, x1, y1]
    kind: str = "text"
    raw_text: str = ""                   # 折半角前的原始识别串（若发生折叠）


@dataclass
class OcrResult:
    provider: str
    model: str = ""
    lines: list[OcrLine] = field(default_factory=list)
    layout: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    page: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


class OcrProvider(ABC):
    name: str = "base"
    supports_structure: bool = False

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """True only when the backend is actually importable right now."""

    @abstractmethod
    def describe(self) -> dict:
        """Static capability description for the startup report."""

    @abstractmethod
    def recognize(
        self,
        image_path: Path,
        *,
        page: int | None = None,
        lang: str = "ch",
        structure: bool = False,
    ) -> OcrResult:
        """Run recognition. Must raise OcrUnavailable instead of returning junk."""


_INSTALL_HINT = (
    "安装本地 OCR（CPU 路径）：\n"
    "  1) pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/\n"
    "  2) pip install paddleocr==3.4.1\n"
    "  首次运行会自动下载 PP-OCRv5 / PP-StructureV3 模型（数百 MB，需联网），"
    "并需要约 2GB 可用内存；模型缓存在 ~/.paddlex/official_models。\n"
    "  若不安装，扫描件与图片会被如实标记为“无法识别”，不会编造内容。"
)


def providers() -> dict[str, type[OcrProvider]]:
    from .fixture import FixtureProvider
    from .paddle import PaddleProvider
    from .rapidocr import RapidOcrProvider

    return {"paddleocr": PaddleProvider, "rapidocr": RapidOcrProvider,
            "fixture": FixtureProvider}


def select(name: str = "auto", *, structure: bool = False) -> OcrProvider:
    """Resolve a provider name to a live instance.

    `auto` prefers PaddleOCR (local, no API key) and never silently falls back to
    a fake recogniser — the fixture provider is test-only and requires an
    explicit opt-in environment variable.
    """
    registry = providers()
    if name == "auto":
        ordered = ["paddleocr", "rapidocr", "fixture"]
    else:
        if name not in registry:
            raise OcrUnavailable(
                f"未知的 OCR 提供者：{name}",
                hint=f"可选：{sorted(registry)}",
                detail={"available": sorted(registry)},
            )
        ordered = [name]

    reasons: list[str] = []
    for key in ordered:
        cls = registry[key]
        if cls.available():
            return cls()
        reasons.append(f"{key}: 未安装或不可用")
    raise OcrUnavailable(
        "没有可用的 OCR 后端，扫描件/图片无法识别文字。",
        hint=_INSTALL_HINT,
        detail={"checked": reasons},
    )


def fixture_enabled() -> bool:
    """Test double must be opted into explicitly and loudly."""
    return os.environ.get("JFA_ALLOW_TEST_PROVIDER") == "1"


INSTALL_HINT = _INSTALL_HINT
