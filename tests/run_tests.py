# -*- coding: utf-8 -*-
"""Run every automated check in one go.

    python tests/run_tests.py            # 单元 + 自检 + 端到端
    python tests/run_tests.py --fast     # 跳过端到端

Exit code 0 only when all three layers pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "job-fit-assistant" / "scripts"
FAST = "--fast" in sys.argv


def run_stage(name: str, argv: list[str], timeout: int = 600) -> dict:
    started = time.time()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    elapsed = time.time() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-6:])
    return {
        "stage": name,
        "rc": proc.returncode,
        "seconds": round(elapsed, 1),
        "tail": tail,
        "ok": proc.returncode == 0,
    }


def main() -> int:
    stages: list[dict] = []

    fixtures = ROOT / "tests" / "fixtures" / "resume.docx"
    if not fixtures.exists():
        stages.append(run_stage("生成测试夹具", [sys.executable, "tests/make_fixtures.py"]))

    stages.append(run_stage("单元测试", [sys.executable, "tests/test_units.py"]))
    stages.append(run_stage("内置自检", [sys.executable, str(SCRIPTS / "jfa.py"), "selftest"]))

    # every subcommand must at least parse and print help
    help_ok = True
    for args in (["--help"], ["scan", "--help"], ["job", "--help"], ["export", "--help"],
                 ["schema", "list"]):
        result = run_stage(f"cli:{' '.join(args)}",
                           [sys.executable, str(SCRIPTS / "jfa.py"), *args], timeout=120)
        if not result["ok"]:
            help_ok = False
            stages.append(result)
            break
    if help_ok:
        stages.append({"stage": "CLI 入口", "rc": 0, "seconds": 0, "tail": "所有子命令可用", "ok": True})

    if not FAST:
        stages.append(run_stage("端到端", [sys.executable, "tests/run_e2e.py"], timeout=900))

    print("=" * 62)
    print("测试汇总")
    print("=" * 62)
    for stage in stages:
        mark = "PASS" if stage["ok"] else "FAIL"
        print(f"[{mark}] {stage['stage']:<28s} {stage['seconds']:>6.1f}s")
        if not stage["ok"]:
            for line in stage["tail"].splitlines():
                print(f"       {line}")

    failed = [s for s in stages if not s["ok"]]
    total_seconds = sum(s["seconds"] for s in stages)
    print("-" * 62)
    print(f"{len(stages)} 个阶段，通过 {len(stages) - len(failed)}，失败 {len(failed)}，"
          f"总耗时 {total_seconds:.0f}s")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
