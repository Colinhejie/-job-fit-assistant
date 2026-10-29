# -*- coding: utf-8 -*-
"""Word COM access, isolated in a child process with hard timeouts.

Word can block forever on a first-run dialog or a repair prompt. A startup
capability check must never hang the user's session, so every COM call runs in a
separate process that we can kill. Child output goes to a file rather than a piped
stdio handle, which also keeps it working under restricted sandboxes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROBE_TIMEOUT = 12
CONVERT_TIMEOUT = 180
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

PROBE_CODE = r"""
import sys
try:
    import pythoncom, win32com.client
except Exception as exc:
    print("ERR import", repr(exc))
    sys.exit(3)
try:
    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("Word.Application")
    print("OK", app.Version)
    app.Quit()
except Exception as exc:
    print("ERR", repr(exc))
    sys.exit(1)
"""

CONVERT_CODE = r"""
import sys
src, dst = sys.argv[1], sys.argv[2]
try:
    import pythoncom, win32com.client
except Exception as exc:
    print("ERR import", repr(exc))
    sys.exit(3)
pythoncom.CoInitialize()
app = doc = None
try:
    app = win32com.client.DispatchEx("Word.Application")
    app.Visible = False
    app.DisplayAlerts = 0
    doc = app.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False)
    doc.ExportAsFixedFormat(
        OutputFileName=dst,
        ExportFormat=17,
        OpenAfterExport=False,
        OptimizeFor=1,
        CreateBookmarks=0,
        DocStructureTags=True,
        BitmapMissingFonts=True,
        UseISO19005_1=False,
    )
    print("OK")
except Exception as exc:
    print("ERR", repr(exc))
    sys.exit(1)
finally:
    try:
        if doc is not None:
            doc.Close(SaveChanges=0)
    except Exception:
        pass
    try:
        if app is not None:
            app.Quit()
    except Exception:
        pass
"""


def _run(code: str, argv: list[str], *, timeout: int, log_path: Path | None) -> tuple[int, str]:
    """Run `code` in a child interpreter. Returns (returncode, captured text)."""
    args = [sys.executable, "-c", code, *argv]
    log_path = Path(log_path) if log_path else Path(tempfile.gettempdir()) / "jfa-com.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "w", encoding="utf-8", errors="replace")
    except OSError:
        log_path = Path(tempfile.mkstemp(suffix=".log")[1])
        handle = open(log_path, "w", encoding="utf-8", errors="replace")
    try:
        with handle:
            proc = subprocess.run(
                args, stdout=handle, stderr=subprocess.STDOUT,
                timeout=timeout, creationflags=_CREATE_NO_WINDOW,
            )
        code_rc = proc.returncode
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except FileNotFoundError:
        return 127, "python interpreter not found"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        text = ""
    return code_rc, text


def probe(*, timeout: int = PROBE_TIMEOUT, log_dir: Path | None = None) -> dict[str, Any]:
    """Is Word COM usable? Never blocks longer than `timeout`."""
    if sys.platform != "win32":
        return {"available": False, "version": "", "reason": "Word COM 仅 Windows 可用。"}
    if os.environ.get("JFA_SKIP_WORD_PROBE") == "1":
        return {"available": False, "version": "",
                "reason": "已通过 JFA_SKIP_WORD_PROBE=1 跳过 Word 探测"}
    rc, out = _run(PROBE_CODE, [], timeout=timeout,
                   log_path=(log_dir / "word-probe.log") if log_dir else None)
    if rc == 0 and out.startswith("OK"):
        return {"available": True, "version": out.split(maxsplit=1)[-1], "reason": ""}
    if rc == 124:
        return {"available": False, "version": "",
                "reason": f"Word COM 探测超时（{timeout}s），可能弹出了对话框；按不可用处理"}
    return {"available": False, "version": "", "reason": out[:400] or f"exit {rc}"}


def convert_docx_to_pdf(
    docx_path: Path,
    pdf_path: Path,
    *,
    timeout: int = CONVERT_TIMEOUT,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """Convert in a child process. Raises RuntimeError on failure/timeout."""
    if sys.platform != "win32":
        raise RuntimeError("Word COM 仅 Windows 可用")
    rc, out = _run(
        CONVERT_CODE, [str(docx_path), str(pdf_path)], timeout=timeout,
        log_path=(log_dir / "word-convert.log") if log_dir else None,
    )
    if rc == 124:
        raise RuntimeError(f"Word 转换超时（{timeout}s）")
    if rc != 0:
        raise RuntimeError(f"Word 转换失败：{out[:400] or f'exit {rc}'}")
    if not Path(pdf_path).exists():
        raise RuntimeError("Word 报告成功但没有生成 PDF")
    return {"ok": True, "output": out[:200]}
