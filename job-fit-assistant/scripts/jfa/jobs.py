# -*- coding: utf-8 -*-
"""Job posting capture: verbatim text, provenance, structure, dedupe.

The browser work itself belongs to the host agent. This module never fetches
anything: it accepts what the host actually read, records how much of the page
was read, and refuses to pretend a partial read is a full one.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from . import salary as salary_mod
from .errors import PausedError, UsageError, ValidationError
from .util import (
    append_jsonl,
    make_id,
    now_iso,
    read_json,
    read_jsonl,
    squash,
    truncate,
    write_json,
    write_jsonl,
)

SCHEMA_JOB = "jfa.job_record/1"

SOURCE_HOST_BROWSER = "host_browser"
SOURCE_USER_PASTE = "user_paste"
SOURCE_FILE = "file"
SOURCE_VALUES = (SOURCE_HOST_BROWSER, SOURCE_USER_PASTE, SOURCE_FILE)

SCOPE_FULL = "full"
SCOPE_PARTIAL = "partial"
SCOPE_UNKNOWN = "unknown"
SCOPE_VALUES = (SCOPE_FULL, SCOPE_PARTIAL, SCOPE_UNKNOWN)

SECTION_KEYS = (
    "responsibilities", "must_have", "nice_to_have",
    "education_experience", "city", "work_mode", "salary_raw",
)

#: Posting headings are short standalone labels. Matching a keyword anywhere in
#: a line would treat "1. 负责订单系统开发" as a heading and delete the duty.
HEADING_LABELS: dict[str, tuple[str, ...]] = {
    "responsibilities": (
        "岗位职责", "工作职责", "职位描述", "工作内容", "职责描述", "你要做什么",
        "你将要做", "主要职责", "responsibilities", "job description", "what you'll do",
    ),
    "must_have": (
        "任职要求", "岗位要求", "任职资格", "职位要求", "必备条件", "要求",
        "我们希望你", "qualifications", "requirements", "must have",
    ),
    "nice_to_have": (
        "加分项", "优先条件", "优先", "nice to have", "preferred", "bonus points",
    ),
    "education_experience": (
        "学历要求", "教育与经验", "学历与经验", "经验要求", "education",
    ),
}

#: lines that describe the location, not the duties
_LOCATION_LINE = re.compile(r"^(工作地点|办公地点|地点|base|location)\s*[:：]", re.I)
_EDU_LINE = re.compile(r"(学历|本科|硕士|大专|博士|统招|985|211|工作经验|年以上|degree|bachelor|master)", re.I)

_RESP = re.compile(r"(岗位职责|工作职责|职位描述|工作内容|你将|负责|职责描述|responsibilit|what you.ll do|job description)", re.I)
_MUST = re.compile(r"(任职要求|岗位要求|任职资格|必备|职位要求|要求|qualification|requirement|must have)", re.I)
_NICE = re.compile(r"(加分项|优先|nice to have|preferred|bonus points|plus)", re.I)
_EDU = re.compile(r"(学历|本科|硕士|大专|博士|统招|985|211|工作经验|年限|年以上|degree|bachelor|master)", re.I)
_SALARY = re.compile(r"(\d[\d.,]*\s*[kK千萬万]?\s*[-~—–至]\s*\d[\d.,]*\s*[kK千萬万]?\s*(?:元)?(?:/月|/年)?|[·•]\s*\d{2}\s*薪|面议|薪资面谈|月薪|年薪)", re.I)
_REMOTE = re.compile(r"(远程|居家办公|remote|work from home)", re.I)
_HYBRID = re.compile(r"(混合办公|弹性办公|hybrid)", re.I)
_ONSITE = re.compile(r"(坐班|线下|到岗| onsite)", re.I)

_LEGAL_SUFFIX = [
    "股份有限公司", "有限责任公司", "有限公司", "集团有限公司", "集团", "科技", "网络",
    "信息技术", "技术", "（中国）", "(中国)", "中国", "公司",
]

BULLET_RE = re.compile(r"^\s*(?:[-*•·●○▪◆]|\d+[.、)）]|[（(]\d+[)）])\s*")


def capture(
    ws,
    *,
    company: str,
    title: str,
    raw_text: str,
    location: str = "",
    url: str = "",
    source_kind: str = SOURCE_USER_PASTE,
    host_tool: str = "",
    read_scope: str = SCOPE_FULL,
    session_id: str = "",
    index: int = 0,
    sections: dict | None = None,
    notes: str = "",
    job_ref: str = "",
    read_at: str = "",
    detail_region: str = "",
    identity_override: dict | None = None,
) -> dict:
    """Persist one captured posting. Returns the record (existing one on dup)."""
    if source_kind not in SOURCE_VALUES:
        raise ValidationError(f"未知的抓取来源：{source_kind}", detail={"allowed": list(SOURCE_VALUES)})
    if read_scope not in SCOPE_VALUES:
        raise ValidationError(f"未知的读取范围：{read_scope}", detail={"allowed": list(SCOPE_VALUES)})
    if not squash(raw_text):
        raise ValidationError(
            "职位原文为空。",
            hint="如果页面没有读到内容（登录失效/验证码/懒加载），请如实记录为失败，不要写入空岗位。",
        )
    if not squash(company) or not squash(title):
        raise ValidationError(
            "缺少公司名或职位名：无法在导出文件名与匹配报告中准确标识该岗位。",
            hint="从页面读取后请核对职位、公司、地点三项再记录（不得靠猜测补齐）。",
        )

    # ---- identity verification (auxiliary, never a hard reject) ----------
    # The header-region match is evidence, not proof: a job board can render the
    # title in an iframe, require login, or reuse a generic header. So a mismatch
    # marks the record `pending_review` and blocks export — it does not throw the
    # capture away, because the text may still be the right posting.
    identity = check_identity(company, title, raw_text, location=location)
    identity["status"] = "verified" if identity["verified"] else "pending_review"
    identity["evidence_kind"] = "header_12_lines+exact_core_match"
    identity["note"] = "" if identity["verified"] else (
        "公司/职位未出现在页头（前 12 行）：标为待核对，导出前需要用户确认"
    )
    if identity_override:
        statement = squash(identity_override.get("statement", ""))
        evidence = identity_override.get("evidence", {})
        if len(statement) < 4 or not evidence:
            raise ValidationError(
                "身份覆盖需要用户原话与页面证据，二者缺一不可。",
                hint=("--statement 填用户本人说的话；--evidence 填页面证据"
                      "（URL、页面标题、截图路径或岗位编号之一）。"),
            )
        identity["override"] = {
            "actor": "user",
            "statement": statement,
            "evidence": evidence,
            "at": read_at or now_iso(),
            "human_authorised": False,
            "note": "这是用户陈述的记录，不构成系统核对，也不是人类授权凭据",
        }

    ws.require()
    cfg = ws.load_config()
    limit = int(cfg.get("screening", {}).get("max_jobs_per_run") or 8)
    existing = ws.load_jobs()
    if session_id and index and index > limit:
        raise PausedError(
            f"本次会话已读取 {limit} 个岗位（上限），已暂停。",
            hint="这是防止后台无限采集的硬限制；请分次运行或提高 config.screening.max_jobs_per_run。",
            detail={"limit": limit, "index": index},
        )

    norm_company = normalize_company(company)
    raw_hash = _sha(raw_text)
    dedupe_key = f"{norm_company}|{squash(title).lower()}|{squash(location).lower()}"

    for job in existing:
        if job.get("dedupe_key") == dedupe_key and job.get("raw_sha256") == raw_hash:
            return {**job, "_duplicate": True, "_duplicate_kind": "exact"}
        if job.get("dedupe_key") == dedupe_key:
            # same role, edited posting: keep both but flag the relationship
            pass

    parsed = parse_sections(raw_text, override=sections)
    salary = salary_mod.parse_salary(parsed.get("salary_raw", "") or _first_salary_line(raw_text))
    completeness = assess_completeness(parsed, read_scope)

    job_id = make_id("job", f"{dedupe_key}|{raw_hash}")
    record = {
        "schema": SCHEMA_JOB,
        "job_id": job_id,
        "company": squash(company),
        "company_normalized": norm_company,
        "title": squash(title),
        "location": squash(location),
        "dedupe_key": dedupe_key,
        "raw_sha256": raw_hash,
        "raw_chars": len(raw_text),
        "source": {
            "kind": source_kind,
            "url": url,
            "host_tool": host_tool,
            "read_at": read_at or now_iso(),
            "read_scope": read_scope,
            "job_ref": job_ref,
            "detail_region": detail_region,
            "session_id": session_id,
            "index": index,
        },
        "sections": parsed,
        "identity": identity,
        "salary": salary.to_dict(),
        "completeness": completeness,
        "notes": notes,
        "created_at": now_iso(),
    }

    raw_path = ws.job_raw(job_id)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_text, encoding="utf-8", newline="\n")
    record["raw_path"] = str(raw_path.relative_to(ws.root))

    duplicates = [j["job_id"] for j in existing if j.get("job_id") != job_id
                  and _similar(j, record)]
    if duplicates:
        record["possible_duplicates"] = duplicates

    rows = existing + [record]
    write_jsonl(ws.jobs_file, rows)
    _touch_cursor(ws, session_id, job_id, index)
    _update_duplicate_index(ws)
    return record


def _similar(a: dict, b: dict) -> bool:
    if a.get("company_normalized") != b.get("company_normalized"):
        return False
    ta, tb = squash(a.get("title", "")).lower(), squash(b.get("title", "")).lower()
    if ta == tb:
        return True
    # token overlap on titles like "Java开发工程师（中级）"
    sa = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]+", ta))
    sb = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]+", tb))
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= 0.6


def confirm_identity(ws, job_id: str, *, statement: str, evidence: str,
                     actor: str = "user") -> dict:
    """Record the user's own confirmation of a pending job identity.

    This unblocks export, but it is explicitly **not** system verification:
    `human_authorised` stays false and every artifact carries the caveat.
    """
    job = ws.find_job(job_id)
    statement = squash(statement)
    evidence = squash(evidence)
    if len(statement) < 4:
        raise ValidationError(
            "需要用户本人的确认原话（至少 4 个字）。",
            hint="请把用户的原话贴进来；不要由助手代写。",
        )
    if not evidence:
        raise ValidationError(
            "需要页面证据：URL、页面标题、截图路径或岗位编号之一。",
            hint="没有证据的确认无法复核。",
        )
    identity = dict(job.get("identity") or {})
    identity["status"] = "user_confirmed"
    identity["user_claim"] = {
        "actor": actor,
        "statement": statement,
        "evidence": evidence,
        "at": now_iso(),
        "human_authorised": False,
        "note": "用户陈述，非系统核对；导出物会带此说明",
    }
    job["identity"] = identity
    jobs = ws.load_jobs()
    for row in jobs:
        if row.get("job_id") == job_id:
            row["identity"] = identity
            break
    write_jsonl(ws.jobs_file, jobs)
    return job


def export_allowed(job: dict) -> tuple[bool, str]:
    """Export gate for identity state."""
    status = (job.get("identity") or {}).get("status", "pending_review")
    if status == "verified":
        return True, ""
    if status == "user_confirmed":
        return True, "身份由用户确认，未经系统核对"
    return False, ("职位身份待核对：公司/职位未出现在页头。请先让用户确认"
                   "（jfa job confirm-identity）或重新读取该页面后再导出。")


def _update_duplicate_index(ws) -> None:
    jobs = ws.load_jobs()
    groups: dict[str, list[str]] = {}
    for job in jobs:
        groups.setdefault(job.get("dedupe_key", ""), []).append(job["job_id"])
    index = {k: v for k, v in groups.items() if len(v) > 1}
    write_json(ws.jobs_dir / "duplicates.json", {"schema": "jfa.dup_index/1",
                                                 "generated_at": now_iso(),
                                                 "groups": index})


def _touch_cursor(ws, session_id: str, job_id: str, index: int) -> None:
    path = ws.jobs_dir / "session.json"
    state = read_json(path, {"schema": "jfa.capture_session/1", "sessions": {}})
    sid = session_id or "default"
    sess = state["sessions"].setdefault(sid, {"status": "running", "captured": [],
                                              "paused_reason": "", "updated_at": ""})
    sess["captured"].append(job_id)
    sess["last_index"] = index
    sess["updated_at"] = now_iso()
    write_json(path, state)


def mark_session(ws, session_id: str, *, status: str, reason: str = "") -> dict:
    """Record pause/cancel so a later run can resume instead of re-reading."""
    if status not in {"running", "paused", "cancelled", "done"}:
        raise UsageError(f"未知的会话状态：{status}")
    path = ws.jobs_dir / "session.json"
    state = read_json(path, {"schema": "jfa.capture_session/1", "sessions": {}})
    sid = session_id or "default"
    sess = state["sessions"].setdefault(sid, {"captured": []})
    sess["status"] = status
    sess["paused_reason"] = reason
    sess["updated_at"] = now_iso()
    write_json(path, state)
    return sess


def session_state(ws, session_id: str = "") -> dict:
    path = ws.jobs_dir / "session.json"
    state = read_json(path, {"schema": "jfa.capture_session/1", "sessions": {}})
    sid = session_id or "default"
    return state["sessions"].get(sid, {"status": "not_started", "captured": []})


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------


def parse_sections(raw_text: str, *, override: dict | None = None) -> dict:
    """Split a posting into the buckets the matcher needs.

    Heuristic, and honest about it: anything the heuristic is unsure of stays in
    the bucket it was already in rather than being force-fitted elsewhere. A line
    only counts as a heading when it *is* the label, not when it contains the word.
    """
    lines = [ln.rstrip() for ln in raw_text.replace("\r\n", "\n").split("\n")]
    buckets: dict[str, list[str]] = {k: [] for k in SECTION_KEYS
                                     if k not in {"city", "work_mode"}}
    current = "responsibilities"
    seen_heading = False
    pre_heading: list[str] = []

    for line in lines:
        text = line.strip()
        if not text:
            continue
        if _LOCATION_LINE.match(text):
            continue  # captured by _detect_city, not a duty/requirement

        heading = _heading_bucket(text)
        if heading:
            current = heading
            seen_heading = True
            continue

        if not seen_heading:
            # Title / company / salary line above the first heading. It is not a
            # requirement; if the posting has no headings at all we fall back to
            # treating these as responsibilities at the end of the loop.
            if _SALARY.search(text) and len(text) <= 80:
                buckets["salary_raw"].append(text)
            pre_heading.append(text)
            continue

        if _SALARY.search(text) and len(text) <= 80 and not _EDU_LINE.search(text):
            buckets["salary_raw"].append(text)
            continue
        cleaned = _strip_bullet(text)
        if _EDU_LINE.search(text) and len(text) <= 60 and current == "must_have":
            # mirror, don't move: a degree requirement is still a requirement and
            # must stay in the must-have coverage count
            buckets["education_experience"].append(cleaned)
            buckets["must_have"].append(cleaned)
            continue
        buckets.setdefault(current, []).append(cleaned)

    if not seen_heading and pre_heading:
        buckets["responsibilities"] = pre_heading

    parsed: dict[str, Any] = {}
    for key in ("responsibilities", "must_have", "nice_to_have", "education_experience"):
        items = [_strip_bullet(x) for x in buckets.get(key, []) if squash(x)]
        parsed[key] = _dedupe_keep_order(items)

    parsed["salary_raw"] = "\n".join(_dedupe_keep_order(buckets.get("salary_raw", [])))
    parsed["city"] = _detect_city(raw_text)
    parsed["work_mode"] = _detect_work_mode(raw_text)
    parsed["heading_detected"] = seen_heading
    parsed["line_count"] = len(lines)
    if override:
        for key, value in override.items():
            if key in SECTION_KEYS or key in {"heading_detected"}:
                parsed[key] = value
    return parsed


def _strip_bullet(text: str) -> str:
    return BULLET_RE.sub("", text).strip()


def _heading_bucket(text: str) -> str:
    """`岗位职责` / `任职要求：` open a bucket; `1. 负责 X` does not."""
    cleaned = _strip_bullet(text).rstrip("：: 　")
    cleaned = re.sub(r"\s+", "", cleaned).lower()
    if not cleaned or len(cleaned) > 14:
        return ""
    for bucket, labels in HEADING_LABELS.items():
        for label in labels:
            if cleaned == re.sub(r"\s+", "", label).lower():
                return bucket
    return ""


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    out, seen = [], set()
    for item in items:
        key = squash(item).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _first_salary_line(raw: str) -> str:
    for line in raw.split("\n"):
        if _SALARY.search(line):
            return line.strip()
    return ""


def _detect_city(raw: str) -> str:
    m = re.search(r"(工作地点|办公地点|地点|base|location)[：:\s]*([^\n，,；;]{2,20})", raw, re.I)
    if m:
        return squash(m.group(2))
    for city in ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安",
                 "苏州", "天津", "重庆", "长沙", "郑州", "青岛", "合肥", "厦门", "福州",
                 "济南", "大连", "宁波", "无锡", "东莞", "佛山", "珠海", "香港", "新加坡"):
        if city in raw:
            return city
    if _REMOTE.search(raw):
        return "远程"
    return ""


def _detect_work_mode(raw: str) -> str:
    if _REMOTE.search(raw):
        return "远程"
    if _HYBRID.search(raw):
        return "混合办公"
    if _ONSITE.search(raw):
        return "坐班"
    return "未说明"


def assess_completeness(sections: dict, read_scope: str) -> dict:
    """What the user is allowed to be told about this posting's completeness."""
    missing: list[str] = []
    if not sections.get("responsibilities"):
        missing.append("responsibilities")
    if not sections.get("must_have"):
        missing.append("must_have")
    if not sections.get("salary_raw"):
        missing.append("salary_raw")
    if not sections.get("city"):
        missing.append("city")
    if read_scope != SCOPE_FULL:
        missing.append("read_scope_not_full")
    return {
        "is_complete": not missing,
        "missing": missing,
        "read_scope": read_scope,
        "heading_detected": bool(sections.get("heading_detected")),
        "warning": "" if not missing else (
            "职位详情未完整读取或缺少关键栏目，不能声称已完成全面定制；"
            "缺口见 missing 字段。"
        ),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def normalize_company(name: str) -> str:
    value = squash(name)
    for suffix in _LEGAL_SUFFIX:
        value = value.replace(suffix, "")
    return value.strip(" ·-()（）").lower()


_CJK = re.compile(r"[\u4e00-\u9fff]")
_ALNUM = re.compile(r"[a-z0-9]")


def _core(text: str) -> str:
    """Comparable core of a name: drop legal suffixes, spaces and punctuation."""
    from .textnorm import fold_fullwidth

    value = squash(fold_fullwidth(text)).lower()
    for suffix in _LEGAL_SUFFIX:
        value = value.replace(suffix.lower(), "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", value)


#: 页面抬头区域的行数：公司/职位必须出现在这里，而不是页面任意位置
HEADER_ZONE_LINES = 12


def _identity_found(core: str, haystack_core: str, minimum: int) -> bool:
    if len(core) < minimum:
        return False
    if core in haystack_core:
        return True
    head = core[: max(4, len(core) // 2)]
    return len(head) >= minimum and head in haystack_core


def check_identity(company: str, title: str, raw_text: str, *, location: str = "") -> dict:
    """Verify that the text we read really is this company's posting.

    A substring hit anywhere in the page is not evidence: job boards put other
    companies in "similar jobs", banners and footers. Company and title must both
    appear in the **header region** (first `HEADER_ZONE_LINES` non-empty lines),
    which is where the posting's own title/company block lives.
    """
    lines = [ln.strip() for ln in (raw_text or "").split("\n") if ln.strip()]
    header_core = _core("\n".join(lines[:HEADER_ZONE_LINES]))
    whole_core = _core("\n".join(lines))

    company_core = _core(company)
    title_core = _core(title)
    location_core = _core(location) if location else ""

    company_in_header = _identity_found(company_core, header_core, minimum=2)
    title_in_header = _identity_found(title_core, header_core, minimum=3)
    # diagnostics only — these must NOT be enough to pass
    company_in_body = _identity_found(company_core, whole_core, minimum=2)
    title_in_body = _identity_found(title_core, whole_core, minimum=3)
    location_in_text = bool(location_core) and location_core in whole_core

    verified = company_in_header and title_in_header
    reasons = []
    if not company_in_header:
        reasons.append("公司名未出现在页头区域" + ("（仅出现在正文其他位置）"
                                                 if company_in_body else ""))
    if not title_in_header:
        reasons.append("职位名未出现在页头区域" + ("（仅出现在正文其他位置）"
                                                 if title_in_body else ""))
    if location and not location_in_text:
        reasons.append("地点未在原文中出现（仅作提示，不单独否决）")

    return {
        "verified": verified,
        "company_in_header": company_in_header,
        "title_in_header": title_in_header,
        "company_in_text": company_in_body,
        "title_in_text": title_in_body,
        "location_in_text": location_in_text,
        "company_core": company_core,
        "title_core": title_core,
        "header_lines": min(len(lines), HEADER_ZONE_LINES),
        "reasons": reasons,
        "note": "" if verified else "；".join(reasons),
    }


def _sha(text: str) -> str:
    from .util import sha256_text

    return sha256_text(text)


def list_jobs(ws, *, limit: int = 20) -> list[dict]:
    jobs = ws.load_jobs()
    return jobs[-limit:]


def validate(record: dict) -> list[dict]:
    issues: list[dict] = []
    if record.get("schema") != SCHEMA_JOB:
        issues.append({"code": "schema", "severity": "warn", "message": "职位记录 schema 非当前版本"})
    if not record.get("source", {}).get("url") and record.get("source", {}).get("kind") == SOURCE_HOST_BROWSER:
        issues.append({"code": "no_url", "severity": "warn",
                       "message": "来自浏览器的职位缺少来源 URL"})
    if record.get("source", {}).get("read_at", "") == "":
        issues.append({"code": "no_read_at", "severity": "warn", "message": "缺少读取时间"})
    return issues


def load_raw(ws, job_id: str) -> str:
    path = ws.job_raw(job_id)
    if not path.exists():
        raise ValidationError(f"职位原文缺失：{job_id}",
                              hint="重新抓取该岗位；不要用其他岗位的文本代替。")
    return path.read_text(encoding="utf-8")
