# -*- coding: utf-8 -*-
"""The job-independent personal fact archive.

Rules enforced here (see references/fact-archive.md):

* A fact is either literally present in a source block (`source_explicit`), added
  by the user and confirmed (`user_confirmed`), still open (`unconfirmed`), or in
  conflict with another fact (`conflict`).
* Confirmation means *the user said so*. It is never third-party verification —
  every fact therefore carries `verification_basis: "self_attested"`.
* Tenure is computed as a union of intervals. Two concurrent jobs never add up.
* Every tailored resume is generated from this archive, never from a previous
  tailored resume.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from . import blocks as B
from .dates import YearMonth, format_span, month_span, parse_month, parse_range
from .errors import ValidationError
from .util import IdAllocator, now_iso, squash, truncate

SCHEMA_FACTS = "jfa.fact_archive/1"

STATUS_SOURCE_EXPLICIT = "source_explicit"
STATUS_USER_CONFIRMED = "user_confirmed"
STATUS_UNCONFIRMED = "unconfirmed"
STATUS_CONFLICT = "conflict"
STATUS_VALUES = (
    STATUS_SOURCE_EXPLICIT, STATUS_USER_CONFIRMED, STATUS_UNCONFIRMED, STATUS_CONFLICT,
)
#: statuses that may be presented as fact on a resume
USABLE = {STATUS_SOURCE_EXPLICIT, STATUS_USER_CONFIRMED}

KIND_IDENTITY = "identity"
KIND_CONTACT = "contact"
KIND_EDUCATION = "education"
KIND_EMPLOYMENT = "employment"
KIND_PROJECT = "project"
KIND_SKILL = "skill"
KIND_ACHIEVEMENT = "achievement"
KIND_PREFERENCE = "preference"
KIND_OTHER = "other"
KIND_VALUES = (
    KIND_IDENTITY, KIND_CONTACT, KIND_EDUCATION, KIND_EMPLOYMENT,
    KIND_PROJECT, KIND_SKILL, KIND_ACHIEVEMENT, KIND_PREFERENCE, KIND_OTHER,
)

#: Fields a resume rewrite may never change. Enforced by rewrite.validate().
PROTECTED_FIELDS = (
    "姓名", "学历", "学位", "毕业院校", "专业", "公司", "职位", "任职时间",
    "技能熟练度", "个人职责", "业绩数字", "证书",
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_CN = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_PHONE_INTL = re.compile(r"(?<!\d)\+\d{1,3}[-\s]?\d{6,14}(?!\d)")
_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)")
_URL = re.compile(r"(?:https?://|www\.)[^\s，。；、）)]+", re.I)
_CITY = re.compile(r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|苏州|天津|重庆|长沙|郑州|青岛|合肥|厦门|福州|济南|大连|宁波|无锡|东莞|佛山|珠海|香港|新加坡|远程)")
_YEARS_EXP = re.compile(r"(\d{1,2})\s*年(?:以上)?(?:工作)?(?:经验|经历)")

SECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("education", re.compile(r"^(教育|学历|学习经历|教育背景|education)", re.I)),
    ("employment", re.compile(r"^(工作经历|工作经验|职业经历|任职经历|实习经历|工作履历|experience|employment|work history)", re.I)),
    ("project", re.compile(r"^(项目|项目经历|项目经验|projects?)", re.I)),
    ("skill", re.compile(r"^(技能|专业技能|技术栈|技能清单|skills?|technical skills)", re.I)),
    ("achievement", re.compile(r"^(荣誉|获奖|奖项|证书|资质|认证|awards?|certificat)", re.I)),
    ("preference", re.compile(r"^(求职意向|期望|求职目标|objective|career objective)", re.I)),
    ("summary", re.compile(r"^(自我评价|个人简介|个人总结|简介|summary|profile|about)", re.I)),
]

_DEGREE = re.compile(r"(博士|硕士|本科|学士|大专|专科|高中|中专|MBA|EMBA|Ph\.?D|Master|Bachelor)", re.I)


def new_archive(person_name: str = "") -> dict:
    return {
        "schema": SCHEMA_FACTS,
        "generated_at": now_iso(),
        "person": {"display_name": person_name, "name_fact_id": ""},
        "facts": [],
        "tenure": {},
        "notes": [],
    }


# ---------------------------------------------------------------------------
# build candidate facts from blocks
# ---------------------------------------------------------------------------


def build_from_blocks(
    archive: dict,
    blocks: list[B.Block],
    *,
    source_file: str = "",
    source_id: str = "",
    source_map: dict[str, str] | None = None,
) -> dict:
    """Add candidate facts derived from extracted blocks.

    Everything added here is a *candidate*: strings copied verbatim from a block
    become `source_explicit`, anything this code had to infer (parsed dates,
    company/title split) is flagged `needs_review` and left `unconfirmed`.

    `source_map` maps source_id -> original filename. Each fact records the
    source of *its own* blocks, so a fact built from three ingested files still
    points at the right file (an empty source_id used to make the evidence chain
    unverifiable).
    """
    archive.setdefault("facts", [])
    alloc = IdAllocator("F", width=4)
    for fact in archive["facts"]:
        alloc.reserve(fact["fact_id"])

    ctx = {
        "source_file": source_file,
        "source_id": source_id,
        "source_map": source_map or {},
    }
    sections = _segment_sections(blocks)
    used_blocks: set[str] = set()

    _add_contact_and_identity(archive, alloc, blocks, sections, ctx, used_blocks)
    for section, section_blocks in sections.items():
        if section == "employment":
            _add_employment(archive, alloc, section_blocks, blocks, ctx, used_blocks)
        elif section == "education":
            _add_education(archive, alloc, section_blocks, blocks, ctx, used_blocks)
        elif section == "project":
            _add_textual(archive, alloc, section_blocks, KIND_PROJECT, "项目经历", ctx, used_blocks)
        elif section == "skill":
            _add_skills(archive, alloc, section_blocks, ctx, used_blocks)
        elif section in {"achievement", "preference", "summary"}:
            kind = {"achievement": KIND_ACHIEVEMENT, "preference": KIND_PREFERENCE,
                    "summary": KIND_OTHER}[section]
            _add_textual(archive, alloc, section_blocks, kind, section, ctx, used_blocks)

    archive["generated_at"] = now_iso()
    archive = dedupe(archive)
    archive["tenure"] = tenure_summary(archive)
    return archive


def dedupe(archive: dict) -> dict:
    """Collapse the same material ingested twice (e.g. a DOCX and its PDF).

    Without this, uploading one resume in two formats doubles every employment
    entry and inflates the computed tenure. Employment is keyed on the parsed
    identity (company/title/start) because PDF extraction reflows text; other
    kinds are keyed on their normalized content.
    """
    seen: dict[tuple, str] = {}
    kept: list[dict] = []
    dropped: list[dict] = []
    for fact in archive.get("facts", []):
        st = fact.get("structured") or {}
        if fact.get("kind") == KIND_EMPLOYMENT and st.get("company"):
            key = ("employment", st.get("company", ""), st.get("title", ""), st.get("start", ""))
        elif fact.get("kind") == KIND_EDUCATION and st.get("school"):
            key = ("education", st.get("school", ""), st.get("degree", ""),
                   st.get("date_raw", ""))
        elif fact.get("kind") in {KIND_ACHIEVEMENT, KIND_PREFERENCE}:
            # "证书：软件设计师（中级）" and "软件设计师（中级）" are the same fact
            body = re.sub(r"^[^：:]{1,6}[：:]\s*", "", fact.get("content", "")).strip()
            key = (fact.get("kind", ""), B_normalize(body))
        else:
            key = (fact.get("kind", ""), B_normalize(fact.get("content", "")))
        if key in seen:
            dropped.append({"fact_id": fact["fact_id"], "duplicate_of": seen[key],
                            "kind": fact.get("kind")})
            continue
        seen[key] = fact["fact_id"]
        kept.append(fact)
    if dropped:
        archive["facts"] = kept
        archive.setdefault("deduped", []).extend(dropped)
        archive.setdefault("notes", []).append(
            f"去重合并了 {len(dropped)} 条重复事实（同一份材料的多种格式），"
            "工作年限与经历不会重复计数。"
        )
    return archive


def _segment_sections(blocks: list[B.Block]) -> dict[str, list[B.Block]]:
    sections: dict[str, list[B.Block]] = {}
    current = "header"
    current_source = None
    for block in blocks:
        if block.source_id != current_source:
            # never let one file's last section swallow another file's header
            current_source = block.source_id
            current = "header"
        locator = block.locator or {}
        if "docx_header" in locator or "docx_footer" in locator:
            # header/footer text belongs to the header block, never to whatever
            # body section happened to be open when it was read
            sections.setdefault("header", []).append(block)
            continue

        text = squash(block.text)
        if not text:
            continue

        matched = _match_section(text)
        if matched:
            name, remainder = matched
            current = name
            sections.setdefault(current, [])
            if remainder:
                sections[current].append(block)
            continue
        sections.setdefault(current, []).append(block)
    return sections


#: A heading is a label, not a sentence: short, and either bare or `标签：内容`.
_MAX_HEADING_LEN = 14
_HEADING_SPLIT = re.compile(r"^(.{2,14}?)\s*[:：]\s*(.+)$")


def _match_section(text: str) -> tuple[str, str] | None:
    """Return (section, inline_content) when this line opens a section."""
    body = text
    remainder = ""
    split = _HEADING_SPLIT.match(text)
    if split:
        body = split.group(1).strip()
        remainder = split.group(2).strip()
    if len(body) > _MAX_HEADING_LEN:
        return None
    for name, pattern in SECTION_PATTERNS:
        if pattern.search(body):
            return name, remainder
    return None


def _verbatim(content: str, blocks: Iterable[B.Block]) -> bool:
    """True when the content is copied literally out of the cited blocks.

    Multi-block facts (an employment entry = header line + bullets) are checked
    against the concatenation, so "verbatim" means nothing was paraphrased or
    invented — not that the text fits in one line.
    """
    key = B_normalize(content)
    if not key:
        return False
    haystack = B_normalize("\n".join(b.text for b in blocks))
    return key in haystack


def B_normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _make_fact(
    alloc: IdAllocator,
    *,
    kind: str,
    content: str,
    label: str,
    source_blocks: list[B.Block],
    ctx: dict,
    structured: dict | None = None,
    status: str | None = None,
    provenance: str = "resume_extract",
    needs_review: bool = False,
) -> dict:
    first = source_blocks[0] if source_blocks else None
    # provenance comes from the fact's own blocks, never from the CLI invocation
    resolved_sid = (first.source_id if first else "") or ctx.get("source_id", "")
    resolved_file = (ctx.get("source_map") or {}).get(resolved_sid) or ctx.get("source_file", "")
    if status is None:
        status = STATUS_SOURCE_EXPLICIT if _verbatim(content, source_blocks) and not needs_review \
            else STATUS_UNCONFIRMED
    fact = {
        "fact_id": alloc.next(),
        "kind": kind,
        "label": label,
        "content": content,
        "structured": structured or {},
        "source": {
            "block_ids": [b.block_id for b in source_blocks],
            "file": resolved_file,
            "source_id": resolved_sid,
            "locator": dict(first.locator) if first else {},
            "page": first.page if first else None,
            "method": first.method if first else "",
        },
        "confirm_status": status,
        "verification_basis": "self_attested",
        "provenance": provenance,
        "needs_review": bool(needs_review or status == STATUS_UNCONFIRMED),
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "revisions": [],
    }
    return fact


def _add_contact_and_identity(archive, alloc, blocks, sections, ctx, used):
    header = sections.get("header", [])[:12]
    joined = "\n".join(b.text for b in header)

    email = _EMAIL.search(joined)
    if email:
        fact = _make_fact(alloc, kind=KIND_CONTACT, content=email.group(0), label="邮箱",
                          source_blocks=_blocks_containing(header, email.group(0)),
                          ctx=ctx,
                          structured={"type": "email", "value": email.group(0)})
        archive["facts"].append(fact)
        used.update(fact["source"]["block_ids"])

    phone = _PHONE_CN.search(joined) or _PHONE_INTL.search(joined) or _LANDLINE.search(joined)
    if phone:
        fact = _make_fact(alloc, kind=KIND_CONTACT, content=phone.group(0), label="电话",
                          source_blocks=_blocks_containing(header, phone.group(0)),
                          ctx=ctx,
                          structured={"type": "phone", "value": phone.group(0)})
        archive["facts"].append(fact)
        used.update(fact["source"]["block_ids"])

    for url in _URL.findall(joined)[:3]:
        fact = _make_fact(alloc, kind=KIND_CONTACT, content=url, label="链接",
                          source_blocks=_blocks_containing(header, url),
                          ctx=ctx,
                          structured={"type": "url", "value": url})
        archive["facts"].append(fact)
        used.update(fact["source"]["block_ids"])

    # name: short header line with no digits/@ and not itself a contact value
    name_candidates = []
    for block in header:
        text = squash(block.text)
        if not text or len(text) > 20:
            continue
        if _EMAIL.search(text) or re.search(r"\d{4,}", text):
            continue
        if any(k in text for k in ("简历", "求职", "个人", "RESUME", "Resume")):
            continue
        if _CITY.search(text) and len(text) <= 6:
            continue
        name_candidates.append((block, text))
    if name_candidates:
        block, text = name_candidates[0]
        fact = _make_fact(alloc, kind=KIND_IDENTITY, content=text, label="姓名",
                          source_blocks=[block], ctx=ctx,
                          structured={"field": "name", "value": text})
        archive["facts"].append(fact)
        archive["person"]["display_name"] = text
        archive["person"]["name_fact_id"] = fact["fact_id"]
        used.add(block.block_id)

    city = _CITY.search(joined)
    if city:
        fact = _make_fact(alloc, kind=KIND_CONTACT, content=city.group(1), label="所在城市",
                          source_blocks=_blocks_containing(header, city.group(1)),
                          ctx=ctx,
                          structured={"type": "city", "value": city.group(1)})
        archive["facts"].append(fact)
        used.update(fact["source"]["block_ids"])


def _blocks_containing(blocks: list[B.Block], needle: str) -> list[B.Block]:
    key = B_normalize(needle)
    hits = [b for b in blocks if key and key in B_normalize(b.text)]
    return hits or blocks[:1]


def _add_employment(archive, alloc, section, all_blocks, ctx, used):
    """Split an employment section into entries with dates.

    A new entry starts at a block that carries a date range, or at a line that
    looks like `公司 | 职位`. Header lines are kept verbatim; parsed fields are
    always `needs_review` because the split is a machine inference.
    """
    entries: list[list[B.Block]] = []
    current: list[B.Block] = []
    for block in section:
        text = squash(block.text)
        if not text:
            continue
        starts_entry = bool(parse_range(text)[0]) or bool(re.search(r"[|｜]", text))
        if starts_entry and current:
            entries.append(current)
            current = [block]
        else:
            current.append(block)
    if current:
        entries.append(current)

    for entry_blocks in entries:
        content = "\n".join(b.text for b in entry_blocks if squash(b.text))
        if len(squash(content)) < 4:
            continue
        head = squash(entry_blocks[0].text)
        start, end, ongoing = parse_range("\n".join(squash(b.text) for b in entry_blocks[:2]))
        company, title, split_method = _split_company_title(entry_blocks)
        structured = {
            "company": company,
            "title": title,
            "start": start.iso() if start else "",
            "end": "present" if ongoing else (end.iso() if end else ""),
            "ongoing": ongoing,
            "date_raw": head if parse_range(head)[0] else "",
            "split_method": split_method,
            "responsive_for": ["resume", "screening"],
        }
        # A split on an explicit delimiter with a parseable date is extraction,
        # not guessing. Anything else stays unconfirmed for the user to fix.
        deterministic = (split_method == "explicit_delimiter" and company and title and start)
        needs_review = not deterministic
        fact = _make_fact(
            alloc, kind=KIND_EMPLOYMENT, content=content,
            label=_employment_label(company, title, start, end, ongoing),
            source_blocks=entry_blocks, ctx=ctx,
            structured=structured,
            status=STATUS_UNCONFIRMED if needs_review else None,
            needs_review=needs_review,
        )
        if deterministic:
            fact["parse_method"] = "explicit_delimiter"
            fact["parse_note"] = "按原文分隔符逐字拆分，日期取自原文；未经第三方核实"
        else:
            fact["parse_method"] = "inferred"
            fact["parse_note"] = "公司与职位由机器推断，需人工确认"
        archive["facts"].append(fact)
        used.update(b.block_id for b in entry_blocks)


def _split_company_title(entry_blocks: list[B.Block]) -> tuple[str, str, str]:
    """Return (company, title, method). Only an explicit delimiter counts."""
    for block in entry_blocks[:3]:
        text = squash(block.text)
        parts = [p.strip() for p in re.split(r"[|｜·•\t]", text) if p.strip()]
        if len(parts) >= 2:
            company, title = parts[0], parts[1]
            if not parse_month(company) and not parse_month(title):
                # every returned field must exist verbatim in the source line
                if company in text and title in text:
                    return company, title, "explicit_delimiter"
    first = squash(entry_blocks[0].text) if entry_blocks else ""
    return (first, "", "inferred") if first else ("", "", "inferred")


def _employment_label(company, title, start, end, ongoing) -> str:
    period = "时间未识别"
    if start:
        end_txt = "至今" if ongoing else (end.iso() if end else "?")
        period = f"{start.iso()}–{end_txt}"
    return "｜".join(x for x in (company, title, period) if x)


def _add_education(archive, alloc, section, all_blocks, ctx, used):
    for block in section:
        text = squash(block.text)
        if len(text) < 4:
            continue
        school, degree, major, date_raw = _parse_education_line(block.text.strip())
        fact = _make_fact(
            alloc, kind=KIND_EDUCATION, content=block.text.strip(),
            label=school or truncate(text, 24),
            source_blocks=[block], ctx=ctx,
            structured={
                "school": school,
                "degree": degree,
                "major": major,
                "date_raw": date_raw,
            },
            needs_review=not (school or degree),
        )
        archive["facts"].append(fact)
        used.add(block.block_id)


def _parse_education_line(text: str) -> tuple[str, str, str, str]:
    """Pull school / degree / major / date out of one education line.

    Works for both `大学 | 专业 | 学历 | 2015.09 - 2019.06` and the space-separated
    form. The date is stored as just the date, never the whole line.
    """
    from .dates import extract_date_span

    date_raw = extract_date_span(text)
    body = text.replace(date_raw, " ") if date_raw else text
    tokens = [t.strip() for t in re.split(r"[|｜·•\t]", body) if t.strip()]

    school = degree = major = ""
    school_pattern = re.compile(
        r"([\u4e00-\u9fff]{2,12}(?:大学|学院|学校|中学|高中))"
        r"|([A-Za-z][A-Za-z\s.&\-]{3,40}(?:University|College|Institute))",
        re.I,
    )
    for token in tokens:
        if not degree:
            found = _DEGREE.search(token)
            if found:
                degree = found.group(0)
                leftover = (token[: found.start()] + token[found.end():]).strip()
                token = leftover
                if not token:
                    continue
        if not school:
            found = school_pattern.search(token)
            if found:
                school = found.group(1) or found.group(2) or ""
                leftover = (token[: found.start()] + token[found.end():]).strip()
                token = leftover
                if not token:
                    continue
        if not major:
            major = token

    if not school:
        found = school_pattern.search(body)
        school = (found.group(1) or found.group(2) or "") if found else ""
    if not degree:
        found = _DEGREE.search(body)
        degree = found.group(0) if found else ""
    if not major:
        major = _major_from(body)
    major = re.sub(r"\s{2,}", " ", major).strip()
    if major in {school, degree, date_raw} or len(squash(major)) < 2:
        major = ""
    return school, degree, major, date_raw


def _school_from(text: str) -> str:
    m = re.search(r"([\u4e00-\u9fff]{2,12}(?:大学|学院|学校|中学|高中|University|College))", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"^([A-Za-z][A-Za-z\s.&\-]{3,40}(?:University|College|Institute))", text, re.I)
    return m.group(1).strip() if m else ""


def _major_from(text: str) -> str:
    m = re.search(r"(?:专业|主修)[：:\s]*([^\s，。；、]{2,20})", text)
    if m:
        return m.group(1)
    m = re.search(r"(?:大学|学院)\s*([^\s，。；、]{2,20})", text)
    return m.group(1) if m else ""


def _add_textual(archive, alloc, section, kind, label, ctx, used):
    for block in section:
        text = block.text.strip()
        if len(squash(text)) < 4:
            continue
        fact = _make_fact(
            alloc, kind=kind, content=text, label=f"{label}·{truncate(text, 16)}",
            source_blocks=[block], ctx=ctx,
        )
        archive["facts"].append(fact)
        used.add(block.block_id)


def _add_skills(archive, alloc, section, ctx, used):
    """A skill token copied verbatim from the resume is usable evidence.

    Only the *level* is an inference, so it is stored separately with
    `level_confirmed` telling the rewrite validator whether a level may be
    claimed at all.
    """
    for block in section:
        text = block.text.strip()
        parts = [p.strip() for p in re.split(r"[,，、;；|｜/]", text) if p.strip()]
        targets = parts if len(parts) >= 2 else ([text] if squash(text) else [])
        for part in targets:
            if not squash(part):
                continue
            level, confirmed = _skill_level(part)
            fact = _make_fact(
                alloc, kind=KIND_SKILL, content=part, label=truncate(part, 20),
                source_blocks=[block], ctx=ctx,
                structured={"level": level, "level_source": "resume_text",
                            "level_confirmed": confirmed},
            )
            archive["facts"].append(fact)
        used.add(block.block_id)


_LEVEL_WORDS = [
    ("精通", "expert"), ("熟练", "proficient"), ("掌握", "working"),
    ("熟悉", "familiar"), ("了解", "aware"), ("学习过", "studied"),
    ("入门", "beginner"),
]


def _skill_level(text: str) -> tuple[str, bool]:
    """Return (level_token, was_the_level_actually_stated)."""
    for word, level in _LEVEL_WORDS:
        if word in text:
            return f"{word}|{level}", True
    return "|unspecified", False


# ---------------------------------------------------------------------------
# tenure: union of intervals, never a naive sum
# ---------------------------------------------------------------------------


def tenure_summary(archive: dict) -> dict:
    intervals: list[tuple[int, int, str, str]] = []
    for fact in archive.get("facts", []):
        if fact.get("kind") != KIND_EMPLOYMENT:
            continue
        st = fact.get("structured") or {}
        start_raw = st.get("start") or ""
        if not start_raw:
            continue
        start = parse_month(start_raw)
        if not start:
            continue
        if st.get("ongoing"):
            end = None
            ongoing = True
        else:
            end = parse_month(st.get("end") or "")
            ongoing = False
        if start and end is None and not ongoing:
            # no end and not marked ongoing -> cannot count as a closed span
            continue
        s_idx = start.months_index()
        if ongoing:
            from .dates import year_month_now

            e_idx = year_month_now().months_index() + 1
        else:
            assert end is not None
            e_idx = end.months_index() + 1
        if e_idx <= s_idx:
            continue
        intervals.append((s_idx, e_idx, fact["fact_id"], fact.get("label", "")))

    if not intervals:
        return {
            "total_months": 0, "total_text": "0 个月", "raw_sum_months": 0,
            "concurrent_detected": False, "segments": [],
            "note": "没有可计算任职区间的事实（日期未识别或未经确认）。",
        }

    intervals.sort()
    merged: list[list[Any]] = []
    for s, e, fid, label in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
            merged[-1][2].append(fid)
        else:
            merged.append([s, e, [fid], label])

    raw_sum = sum(e - s for s, e, _, _ in intervals)
    total = sum(e - s for s, e, _, _ in merged)
    segments = [
        {"start_index": s, "end_index": e, "months": e - s, "fact_ids": fids}
        for s, e, fids, _ in merged
    ]
    concurrent = len(merged) < len(intervals)
    return {
        "total_months": total,
        "total_text": format_span(total),
        "raw_sum_months": raw_sum,
        "raw_sum_text": format_span(raw_sum),
        "concurrent_detected": concurrent,
        "segments": segments,
        "note": (
            "存在并行任职：总年限按区间合并计算，未简单累加。"
            if concurrent else "总年限按任职区间合并计算。"
        ),
    }


# ---------------------------------------------------------------------------
# mutation: confirm / correct / add, always versioned
# ---------------------------------------------------------------------------


def find_fact(archive: dict, fact_id: str) -> dict:
    for fact in archive.get("facts", []):
        if fact.get("fact_id") == fact_id:
            return fact
    raise ValidationError(f"事实档案中不存在 {fact_id}", detail={"fact_id": fact_id})


def revise_fact(
    archive: dict,
    fact_id: str,
    *,
    content: str | None = None,
    confirm_status: str | None = None,
    structured_patch: dict | None = None,
    reason: str = "",
    actor: str = "user",
) -> dict:
    """Apply one revision and append an immutable history entry."""
    fact = find_fact(archive, fact_id)
    before = {
        "content": fact.get("content", ""),
        "confirm_status": fact.get("confirm_status", ""),
        "structured": dict(fact.get("structured") or {}),
    }
    if content is not None and content != fact.get("content"):
        fact["content"] = content
        fact["needs_review"] = False
    if structured_patch:
        structured = dict(fact.get("structured") or {})
        structured.update(structured_patch)
        fact["structured"] = structured
    if confirm_status is not None:
        if confirm_status not in STATUS_VALUES:
            raise ValidationError(f"未知的确认状态：{confirm_status}",
                                  detail={"allowed": list(STATUS_VALUES)})
        fact["confirm_status"] = confirm_status
        if confirm_status in USABLE:
            fact["needs_review"] = False
    fact["version"] = int(fact.get("version", 1)) + 1
    fact["updated_at"] = now_iso()
    fact.setdefault("revisions", []).append({
        "version": fact["version"],
        "at": fact["updated_at"],
        "actor": actor,
        "reason": reason,
        "before": before,
        "after": {
            "content": fact.get("content", ""),
            "confirm_status": fact.get("confirm_status", ""),
            "structured": dict(fact.get("structured") or {}),
        },
    })
    archive["tenure"] = tenure_summary(archive)
    return fact


def add_user_fact(
    archive: dict,
    *,
    kind: str,
    content: str,
    label: str = "",
    structured: dict | None = None,
    confirmed: bool = True,
) -> dict:
    """A fact the user supplied in conversation. Confirmed only if they said so."""
    if kind not in KIND_VALUES:
        raise ValidationError(f"未知的事实类型：{kind}", detail={"allowed": list(KIND_VALUES)})
    alloc = IdAllocator("F", width=4)
    for fact in archive.get("facts", []):
        alloc.reserve(fact["fact_id"])
    fact = {
        "fact_id": alloc.next(),
        "kind": kind,
        "label": label or truncate(content, 24),
        "content": content,
        "structured": structured or {},
        "source": {"block_ids": [], "file": "", "source_id": "", "locator": {}, "page": None,
                   "method": "conversation"},
        "confirm_status": STATUS_USER_CONFIRMED if confirmed else STATUS_UNCONFIRMED,
        "verification_basis": "self_attested",
        "provenance": "user_statement",
        "needs_review": not confirmed,
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "revisions": [],
    }
    archive.setdefault("facts", []).append(fact)
    archive["tenure"] = tenure_summary(archive)
    return fact


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate(archive: dict) -> list[dict]:
    """Return issues. `block` severity means a resume must not be exported."""
    issues: list[dict] = []

    def add(code: str, severity: str, message: str, **extra):
        issues.append({"code": code, "severity": severity, "message": message, **extra})

    if archive.get("schema") != SCHEMA_FACTS:
        add("schema", "block", f"事实档案 schema 应为 {SCHEMA_FACTS}")
    facts = archive.get("facts")
    if not isinstance(facts, list) or not facts:
        add("empty", "block", "事实档案为空，无法生成任何简历版本。")
        return issues

    seen: set[str] = set()
    for fact in facts:
        fid = fact.get("fact_id", "")
        if not fid:
            add("missing_id", "block", "存在缺少 fact_id 的事实", fact=fact.get("label", ""))
            continue
        if fid in seen:
            add("duplicate_id", "block", f"fact_id 重复：{fid}", fact_id=fid)
        seen.add(fid)
        if fact.get("kind") not in KIND_VALUES:
            add("bad_kind", "block", f"{fid} 的 kind 非法：{fact.get('kind')}", fact_id=fid)
        status = fact.get("confirm_status")
        if status not in STATUS_VALUES:
            add("bad_status", "block", f"{fid} 的 confirm_status 非法：{status}", fact_id=fid)
        if not squash(fact.get("content", "")):
            add("empty_content", "block", f"{fid} 内容为空", fact_id=fid)
        if fact.get("verification_basis") != "self_attested":
            add("bad_basis", "warn",
                f"{fid} 的 verification_basis 应为 self_attested（用户确认 ≠ 第三方核实）",
                fact_id=fid)
        if status == STATUS_CONFLICT and not fact.get("conflicts_with"):
            add("conflict_without_ref", "warn",
                f"{fid} 标记为冲突但未列出冲突对象", fact_id=fid)
        if fact.get("kind") == KIND_EMPLOYMENT:
            st = fact.get("structured") or {}
            if not st.get("company"):
                add("employment_no_company", "warn",
                    f"{fid} 缺少公司名，需人工确认后使用", fact_id=fid)
            if not st.get("start"):
                add("employment_no_start", "warn",
                    f"{fid} 缺少起始时间，不计入总年限", fact_id=fid)

    ids = {f.get("fact_id") for f in facts}
    for fact in facts:
        for other in fact.get("conflicts_with", []) or []:
            if other not in ids:
                add("dangling_conflict", "warn",
                    f"{fact.get('fact_id')} 指向不存在的事实 {other}", fact_id=fact.get("fact_id"))

    if not archive.get("person", {}).get("display_name"):
        add("no_name", "warn", "事实档案没有姓名，简历抬头将留空。")

    unresolved = [f["fact_id"] for f in facts
                  if f.get("confirm_status") == STATUS_UNCONFIRMED]
    if unresolved:
        add("unconfirmed_facts", "warn",
            f"{len(unresolved)} 条事实尚未确认，只能写入保守草稿并标注待确认",
            fact_ids=unresolved)
    return issues


def blocking_issues(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("severity") == "block"]


def usable_facts(archive: dict) -> list[dict]:
    return [f for f in archive.get("facts", []) if f.get("confirm_status") in USABLE]


def fact_index(archive: dict) -> dict[str, dict]:
    return {f["fact_id"]: f for f in archive.get("facts", []) if f.get("fact_id")}


def summarize(archive: dict) -> dict:
    facts = archive.get("facts", [])
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for f in facts:
        by_status[f.get("confirm_status", "?")] = by_status.get(f.get("confirm_status", "?"), 0) + 1
        by_kind[f.get("kind", "?")] = by_kind.get(f.get("kind", "?"), 0) + 1
    return {
        "total": len(facts),
        "by_status": by_status,
        "by_kind": by_kind,
        "tenure": archive.get("tenure", {}),
        "unconfirmed": [f["fact_id"] for f in facts
                        if f.get("confirm_status") == STATUS_UNCONFIRMED],
    }
