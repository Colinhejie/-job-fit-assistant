# -*- coding: utf-8 -*-
"""Small shared helpers: hashing, atomic JSON, safe filenames, time.

Deliberately dependency-free so the local core still runs on a bare Python
interpreter (the only hard third-party deps are python-docx / PyMuPDF / Pillow,
and those are imported lazily by the extractors that need them).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .errors import DataError, SafetyViolation

# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------


def now() -> datetime:
    """Local-aware current time. All user-facing timestamps are local."""
    return datetime.now().astimezone()


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp() -> str:
    """Compact local timestamp used in artifact filenames."""
    return now().strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------
# hashing
# --------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def short_hash(value: str, length: int = 8) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def write_json(path: Path, obj: Any) -> Path:
    """Atomically write UTF-8 JSON (indent=2, CJK unescaped)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise DataError(
            f"JSON 解析失败：{path.name}",
            hint="文件可能被手工编辑破坏；可用 `.jfa/backups` 或重新生成。",
            detail={"path": str(path), "line": exc.lineno, "col": exc.colno},
        ) from exc


def write_jsonl(path: Path, rows: Iterable[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DataError(
                    f"JSONL 第 {lineno} 行解析失败：{path.name}",
                    detail={"path": str(path), "line": lineno},
                ) from exc
    return out


def append_jsonl(path: Path, row: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def iter_jsonl(path: Path) -> Iterator[dict]:
    yield from read_jsonl(path)


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_ws(text: str) -> str:
    """Collapse intra-line whitespace, keep newlines, drop control chars."""
    text = _CTRL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(_WS_RE.sub(" ", line).strip() for line in text.split("\n"))


def squash(text: str) -> str:
    """All whitespace to single spaces — for comparison, not for display."""
    return _WS_RE.sub(" ", _CTRL_RE.sub("", text)).strip()


def compare_key(text: str) -> str:
    """Normalize text for equality checks across DOCX/PDF extraction drift."""
    text = unicodedata.normalize("NFKC", text)
    text = squash(text)
    # full-width punctuation drift between engines
    for a, b in (("：", ":"), ("，", ","), ("；", ";"), ("（", "("), ("）", ")"),
                 ("｜", "|"), ("／", "/"), ("－", "-"), ("—", "-"), ("–", "-")):
        text = text.replace(a, b)
    return re.sub(r"\s+", "", text).lower()


_SEPARATORS = re.compile(r"[\s|·•,、;:/\\]+")


def loose_key(text: str) -> str:
    """Even more permissive key: also drops layout separators.

    Used to answer "did this text make it into the PDF", where the DOCX uses a
    tab stop and the PDF uses a right-aligned box for the same date.
    """
    text = unicodedata.normalize("NFKC", text)
    for a, b in (("：", ":"), ("，", ","), ("；", ";"), ("（", "("), ("）", ")"),
                 ("｜", "|"), ("／", "/"), ("－", "-"), ("—", "-"), ("–", "-")):
        text = text.replace(a, b)
    return _SEPARATORS.sub("", text).lower()


def truncate(text: str, limit: int = 120, ellipsis: str = "…") -> str:
    text = squash(text)
    return text if len(text) <= limit else text[: limit - 1] + ellipsis


# --------------------------------------------------------------------------
# filenames & paths
# --------------------------------------------------------------------------

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}
_UNSAFE_FS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_component(value: str, *, max_len: int = 48, fallback: str = "untitled") -> str:
    """Turn an arbitrary user/company string into a safe, readable path segment.

    Keeps CJK, letters, digits, `-` and `_`. Never returns `.`/`..`, never
    returns a Windows reserved device name, never returns an empty string.
    """
    value = unicodedata.normalize("NFKC", (value or "").strip())
    value = _UNSAFE_FS.sub("", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-._ ")
    value = value[:max_len].strip("-._ ")
    if not value or value in {".", ".."}:
        return fallback
    if value.split(".")[0].upper() in _WIN_RESERVED:
        value = f"_{value}"
    return value


def ensure_within(base: Path, target: Path) -> Path:
    """Reject paths that escape the workspace via `..`, symlink or drive hop."""
    base_r = Path(os.path.abspath(base))
    target_r = Path(os.path.abspath(target))
    try:
        target_r.relative_to(base_r)
    except ValueError as exc:
        raise SafetyViolation(
            "路径越出工作区，已拒绝操作。",
            hint="只允许读写 job-fit-assistant 工作区内的文件。",
            detail={"base": str(base_r), "target": str(target_r)},
        ) from exc
    return target_r


def unique_path(directory: Path, stem: str, ext: str) -> Path:
    """Return a non-existing path; never overwrite an earlier deliverable."""
    directory.mkdir(parents=True, exist_ok=True)
    ext = ext if ext.startswith(".") else f".{ext}" if ext else ""
    candidate = directory / f"{stem}{ext}"
    if not candidate.exists():
        return candidate
    for i in range(2, 1000):
        candidate = directory / f"{stem}-{i}{ext}"
        if not candidate.exists():
            return candidate
    raise DataError(
        "无法分配不重复的输出文件名。",
        hint="同名文件超过 999 个，请清理 out/ 目录或换一个版本目录。",
    )


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


# --------------------------------------------------------------------------
# ids
# --------------------------------------------------------------------------


def make_id(prefix: str, seed: str, *, length: int = 8) -> str:
    return f"{prefix}-{short_hash(seed, length)}"


class IdAllocator:
    """Monotonic ids inside one build pass (`F-0001`, `F-0002`, ...)."""

    def __init__(self, prefix: str, width: int = 4, start: int = 1):
        self.prefix = prefix
        self.width = width
        self._next = start
        self._used: set[str] = set()

    def next(self) -> str:
        while True:
            value = f"{self.prefix}-{self._next:0{self.width}d}"
            self._next += 1
            if value not in self._used:
                self._used.add(value)
                return value

    def reserve(self, value: str) -> None:
        self._used.add(value)
        m = re.search(r"(\d+)$", value)
        if m:
            self._next = max(self._next, int(m.group(1)) + 1)
