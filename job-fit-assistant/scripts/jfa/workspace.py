# -*- coding: utf-8 -*-
"""Workspace layout, initialisation and version-preserving writes.

Layout (all paths relative to the workspace root the user chose):

    workspace.json          schema marker + creation info
    config.json             user settings (ocr / export / host declaration)
    sources/                immutable copies of every input file
    blocks/<sid>.jsonl      extracted text blocks per source
    blocks/index.json       source registry
    review/queue.json       OCR review items awaiting confirmation
    review/corrections.jsonl
    facts/facts.json        the job-independent fact archive
    jobs/jobs.jsonl         captured job postings
    jobs/raw/<jid>.txt      verbatim posting text
    matches/<jid>/vN.json   requirement <-> evidence maps
    rewrites/<jid>/vN.json  change plans per resume version
    out/<jid>/vN/           exported deliverables
    logs/run.log
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .errors import DataError, NotFoundError, SafetyViolation, UsageError
from .util import now_iso, read_json, write_json

SCHEMA_WORKSPACE = "jfa.workspace/1"
SCHEMA_CONFIG = "jfa.config/1"
CORE_VERSION = "1.0.0"

DEFAULT_CONFIG: dict[str, Any] = {
    "schema": SCHEMA_CONFIG,
    "ocr": {
        "provider": "auto",
        "lang": "ch",
        "structure": False,
        "max_pages_per_run": 20,
        "dpi": 220,
    },
    "export": {
        "style": "default-zh-single-column",
        "max_pages": 2,
        "min_font_pt": 9.5,
        "include_photo": False,
        "pdf_backends": ["libreoffice", "word", "builtin"],
    },
    "screening": {
        "max_jobs_per_run": 8,
        "unknown_is_fail": False,
    },
    "host": {
        # Filled in only from a declaration made by the host agent, never
        # guessed by this program. Empty means "capability not verified".
        "declared": False,
        "browser_kind": "",
        "browser_tool_names": [],
        "login_scope": "",
        "declared_at": "",
        "notes": "",
    },
    "privacy": {
        "log_raw_text": False,
        "retain_sources": True,
    },
}


class Workspace:
    """Handle for one user's private job-hunt data directory."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    # ---------------------------------------------------------------- paths
    @property
    def meta_file(self) -> Path:
        return self.root / "workspace.json"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def blocks_dir(self) -> Path:
        return self.root / "blocks"

    @property
    def review_dir(self) -> Path:
        return self.root / "review"

    @property
    def facts_file(self) -> Path:
        return self.root / "facts" / "facts.json"

    @property
    def jobs_dir(self) -> Path:
        return self.root / "jobs"

    @property
    def jobs_file(self) -> Path:
        return self.jobs_dir / "jobs.jsonl"

    @property
    def matches_dir(self) -> Path:
        return self.root / "matches"

    @property
    def rewrites_dir(self) -> Path:
        return self.root / "rewrites"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def screen_file(self) -> Path:
        return self.root / "screening" / "latest.json"

    @property
    def prefs_file(self) -> Path:
        return self.root / "preferences.json"

    def job_raw(self, job_id: str) -> Path:
        return self.jobs_dir / "raw" / f"{job_id}.txt"

    def match_file(self, job_id: str) -> Path:
        return self.matches_dir / job_id / "latest.json"

    def match_version(self, job_id: str, version: int) -> Path:
        return self.matches_dir / job_id / f"v{version}.json"

    def rewrite_file(self, job_id: str) -> Path:
        return self.rewrites_dir / job_id / "latest.json"

    def rewrite_version(self, job_id: str, version: int) -> Path:
        return self.rewrites_dir / job_id / f"v{version}.json"

    def out_version_dir(self, job_id: str, version: int) -> Path:
        return self.out_dir / job_id / f"v{version}"

    # ------------------------------------------------------------- lifecycle
    def exists(self) -> bool:
        return self.meta_file.exists()

    def init(self, *, force: bool = False) -> dict:
        if self.meta_file.exists() and not force:
            raise UsageError(
                "工作区已经初始化。",
                hint="如需重建请显式使用 --force（不会删除已有资料）。",
                detail={"root": str(self.root)},
            )
        for d in (
            self.sources_dir, self.blocks_dir, self.review_dir,
            self.facts_file.parent, self.jobs_dir / "raw",
            self.matches_dir, self.rewrites_dir, self.out_dir, self.logs_dir,
            self.screen_file.parent,
        ):
            d.mkdir(parents=True, exist_ok=True)
        meta = {
            "schema": SCHEMA_WORKSPACE,
            "core_version": CORE_VERSION,
            "created_at": now_iso(),
            "root": str(self.root),
        }
        write_json(self.meta_file, meta)
        if not self.config_file.exists():
            write_json(self.config_file, DEFAULT_CONFIG)
        write_json(self.blocks_dir / "index.json", {"schema": "jfa.block_index/1", "sources": []})
        return meta

    def require(self) -> "Workspace":
        if not self.exists():
            raise NotFoundError(
                f"未找到工作区：{self.root}",
                hint="先运行 `jfa init --home <目录>` 创建工作区。",
            )
        return self

    # ---------------------------------------------------------------- config
    def load_config(self) -> dict:
        cfg = read_json(self.config_file, None)
        if cfg is None:
            return json.loads(json.dumps(DEFAULT_CONFIG))
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        _deep_merge(merged, cfg)
        return merged

    def save_config(self, cfg: dict) -> None:
        write_json(self.config_file, cfg)

    def patch_config(self, dotted: str, value: Any) -> dict:
        cfg = self.load_config()
        node = cfg
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        self.save_config(cfg)
        return cfg

    # ------------------------------------------------------------- guards
    def guard(self, path: Path) -> Path:
        """Ensure `path` stays inside the workspace before any write/delete."""
        root = os.path.abspath(self.root)
        target = os.path.abspath(path)
        if os.path.commonpath([root, target]) != root:
            raise SafetyViolation(
                "拒绝访问工作区之外的路径。",
                detail={"root": root, "target": target},
            )
        return Path(target)

    # ------------------------------------------------------------ versions
    def next_version(self, directory: Path, pattern: str = "v*.json") -> int:
        directory.mkdir(parents=True, exist_ok=True)
        highest = 0
        for p in directory.glob(pattern):
            stem = p.stem.lstrip("v")
            if stem.isdigit():
                highest = max(highest, int(stem))
        return highest + 1

    def save_versioned(self, directory: Path, payload: dict, *, prefix: str = "v") -> tuple[int, Path]:
        """Write `<prefix>N.json` and refresh `latest.json`. Never overwrites."""
        directory.mkdir(parents=True, exist_ok=True)
        version = self.next_version(directory, pattern=f"{prefix}*.json")
        target = directory / f"{prefix}{version}.json"
        payload = dict(payload)
        payload.setdefault("version", version)
        payload["saved_at"] = now_iso()
        write_json(target, payload)
        write_json(directory / "latest.json", payload)
        return version, target

    # ---------------------------------------------------------------- misc
    def load_facts(self) -> dict:
        data = read_json(self.facts_file, None)
        if data is None:
            raise NotFoundError(
                "事实档案不存在。",
                hint="先运行 `jfa facts build`，或在 SKILL.md 的追问流程后写入 facts.json。",
            )
        if not isinstance(data, dict) or "facts" not in data:
            raise DataError("事实档案结构不正确：缺少 `facts` 数组。")
        return data

    def load_jobs(self) -> list[dict]:
        from .util import read_jsonl

        return read_jsonl(self.jobs_file)

    def find_job(self, job_id: str) -> dict:
        for job in self.load_jobs():
            if job.get("job_id") == job_id:
                return job
        raise NotFoundError(f"未找到职位记录：{job_id}", hint="运行 `jfa job list` 查看已抓取职位。")

    def log(self, message: str) -> None:
        """Append one line. Never writes resume or posting body text."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        from .util import truncate

        line = f"{now_iso()}\t{truncate(message, 500)}\n"
        with open(self.logs_dir / "run.log", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)

    def purge(self) -> dict:
        """Delete this tool's local data. Never claims to erase third parties."""
        self.require()
        removed: list[str] = []
        failed: list[str] = []
        targets = [
            self.sources_dir, self.blocks_dir, self.review_dir,
            self.facts_file.parent, self.jobs_dir, self.matches_dir,
            self.rewrites_dir, self.out_dir, self.logs_dir,
            self.screen_file.parent,
        ]
        for d in targets:
            if not d.exists():
                continue
            self.guard(d)
            # A file open in another program must not turn a purge into a crash:
            # delete what we can and report the rest honestly.
            shutil.rmtree(d, ignore_errors=True)
            if d.exists():
                failed.append(str(d.relative_to(self.root)))
            else:
                removed.append(str(d.relative_to(self.root)))
        for f in (self.config_file, self.prefs_file):
            if f.exists():
                self.guard(f)
                try:
                    f.unlink()
                    removed.append(f.name)
                except OSError:
                    failed.append(f.name)
        return {
            "removed": removed,
            "failed": failed,
            "workspace_root": str(self.root),
            "still_exists": ["workspace.json (以便再次初始化)"]
            + ([f"未能删除：{', '.join(failed)}（可能被其他程序占用，请关闭后重试）"] if failed else []),
            "third_party_notice": (
                "本工具只删除上述本地文件。浏览器页面、招聘平台、宿主云端会话、"
                "以及任何第三方服务已经处理或留存的数据不在本工具控制范围内，无法代为删除。"
            ),
        }


def _deep_merge(base: dict, override: dict) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def resolve_workspace(home: str | None) -> Workspace:
    """`--home` wins; otherwise `JFA_HOME`; otherwise `./job-fit-workspace`."""
    raw = home or os.environ.get("JFA_HOME") or "job-fit-workspace"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return Workspace(path)
