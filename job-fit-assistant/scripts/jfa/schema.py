# -*- coding: utf-8 -*-
"""A small JSON-Schema subset validator for the shipped schemas.

The schemas in `assets/schemas` are the contract the host agent writes against.
Shipping schemas that nothing checks would be documentation pretending to be
code, so this validates them with the subset they actually use: type, required,
properties, items, enum, const, minItems, minimum/maximum and patterns.

Deliberately dependency-free (no `jsonschema`), because the local core must run
on a bare interpreter.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import DataError, NotFoundError, UsageError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "assets" / "schemas"

#: name -> file
KNOWN = {
    "block": "block.schema.json",
    "fact_archive": "fact_archive.schema.json",
    "job_record": "job_record.schema.json",
    "preferences": "preferences.schema.json",
    "requirement_map": "requirement_map.schema.json",
    "change_plan": "change_plan.schema.json",
    "resume_doc": "resume_doc.schema.json",
}

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def load(name: str) -> dict:
    if name not in KNOWN:
        raise UsageError(
            f"未知的 schema：{name}",
            hint=f"可选：{', '.join(sorted(KNOWN))}",
        )
    path = SCHEMA_DIR / KNOWN[name]
    if not path.exists():
        raise NotFoundError(
            f"schema 文件缺失：{path}",
            hint="发布包不完整；请重新获取 job-fit-assistant。",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def list_schemas() -> list[dict]:
    out = []
    for name, filename in KNOWN.items():
        path = SCHEMA_DIR / filename
        schema = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        out.append({
            "name": name,
            "file": filename,
            "id": schema.get("$id", ""),
            "title": schema.get("title", ""),
            "exists": path.exists(),
        })
    return out


def validate(instance: Any, schema: dict, *, path: str = "$") -> list[dict]:
    """Return a list of `{path, message}` problems. Empty means valid."""
    problems: list[dict] = []
    _validate(instance, schema, path, problems)
    return problems


def _fail(problems: list[dict], path: str, message: str) -> None:
    problems.append({"path": path, "message": message})


def _validate(value: Any, schema: dict, path: str, problems: list[dict]) -> None:
    if not isinstance(schema, dict):
        return

    if "const" in schema and value != schema["const"]:
        _fail(problems, path, f"应为常量 {schema['const']!r}，实际 {value!r}")
        return

    if "enum" in schema and value not in schema["enum"]:
        _fail(problems, path, f"取值 {value!r} 不在允许范围 {schema['enum']}")
        return

    declared = schema.get("type")
    if declared is not None:
        allowed = declared if isinstance(declared, list) else [declared]
        if not any(_type_ok(value, t) for t in allowed):
            _fail(problems, path, f"类型应为 {allowed}，实际 {type(value).__name__}")
            return

    if isinstance(value, bool):
        pass  # bool is an int subclass; never treat it as a number here
    elif isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            _fail(problems, path, f"数值 {value} 小于最小值 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            _fail(problems, path, f"数值 {value} 大于最大值 {schema['maximum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            _fail(problems, path, f"长度 {len(value)} 小于 {schema['minLength']}")
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, value):
            _fail(problems, path, f"字符串 {value!r} 不匹配 {pattern}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            _fail(problems, path, f"至少需要 {schema['minItems']} 项，实际 {len(value)}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]", problems)

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                _fail(problems, path, f"缺少必填字段 `{key}`")
        properties = schema.get("properties") or {}
        for key, sub in properties.items():
            if key in value:
                _validate(value[key], sub, f"{path}.{key}", problems)
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                _fail(problems, path, f"存在未定义字段 {sorted(extra)}")


def _type_ok(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPES.get(name)
    if expected is None:
        return True
    return isinstance(value, expected)


def validate_file(kind: str, path: Path) -> list[dict]:
    instance = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate(instance, load(kind))


def self_check() -> list[dict]:
    """Every shipped schema must at least be valid JSON with the expected id."""
    checks = []
    for row in list_schemas():
        path = SCHEMA_DIR / row["file"]
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            ok = bool(schema.get("$id")) and schema.get("type") == "object"
            detail = schema.get("$id", "")
        except Exception as exc:  # pragma: no cover
            ok, detail = False, str(exc)
        checks.append({"name": row["name"], "ok": ok, "detail": detail})
    return checks


def render_list(rows: list[dict]) -> str:
    lines = ["可用 schema（用 `jfa schema check <名称> <文件>` 校验 JSON）：", ""]
    for row in rows:
        lines.append(f"- {row['name']:16s} {row['id'] or '(缺失)':24s} {row['title']}")
    return "\n".join(lines)
