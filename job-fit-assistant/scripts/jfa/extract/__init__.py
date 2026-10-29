# -*- coding: utf-8 -*-
"""Extraction adapters: plain text, DOCX, PDF, images."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import blocks as B


@dataclass
class SourceResult:
    source_id: str
    path: Path
    kind: str
    blocks: list[B.Block] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "path": str(self.path),
            "kind": self.kind,
            "block_count": len(self.blocks),
            "needs_review": sum(1 for b in self.blocks
                                if b.check_status in {B.CHECK_REVIEW, B.CHECK_UNVERIFIED}),
            "report": self.report,
            "warnings": self.warnings,
            "errors": self.errors,
        }
