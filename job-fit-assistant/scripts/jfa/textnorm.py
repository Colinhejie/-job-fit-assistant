# -*- coding: utf-8 -*-
"""OCR text normalisation.

OCR engines working on CJK documents frequently return **full-width** Latin
letters and digits (`１３５１１１１２２２２`, `ｌｉｎｚｈｏｕ`). Those look identical in a
resume but break every downstream comparison: phone numbers, dates, email
addresses and English skill names all fail to match.

`fold_fullwidth` maps only full-width alphanumerics and a few ASCII-ish marks.
It deliberately leaves Chinese punctuation (，。、；：（）) untouched so the text
still reads like Chinese.

The original string is kept: callers record `raw_text` when folding changed
anything, so the recognised original is never silently lost.
"""

from __future__ import annotations

#: full-width marks that should become their ASCII twin
_MARK_MAP = {
    "．": ".", "＠": "@", "－": "-", "＿": "_", "／": "/",
    "＋": "+", "％": "%", "＆": "&", "＃": "#", "＊": "*",
}


def fold_fullwidth(text: str) -> str:
    """Convert full-width A-Z / a-z / 0-9 (and a few marks) to ASCII."""
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
            out.append(chr(code - 0xFEE0))
        elif ch in _MARK_MAP:
            out.append(_MARK_MAP[ch])
        else:
            out.append(ch)
    return "".join(out)


def fold_with_raw(text: str) -> tuple[str, str]:
    """Return (folded, raw). `raw` is empty when nothing changed."""
    folded = fold_fullwidth(text)
    return folded, (text if folded != text else "")
