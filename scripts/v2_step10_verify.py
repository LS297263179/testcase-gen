"""V2 Step 10.6 完整端到端验证脚本 —— 一键跑 + 22 条门槛报告表。

把 PROGRESS §9.5 的 22 条门槛汇总成一份「硬证据表」：每条门槛标注
  门槛编号 / 名称 / 证据来源 / 状态（PASS|FAIL|SKIP）

证据来源分三层：
  - 门槛 1-6,10-19,22(测试部分) → tests/test_step10_acceptance.py（mock，CI 友好）
  - 门槛 7-9                      → tests/test_step10_real_llm.py（real_llm marker，本地验收用）
  - 门槛 20                       → 全量 pytest returncode
  - 门槛 21                       → ruff check
  - 门槛 22                       → ruff format --check + 本脚本独立 api_key 静态扫描

★ 退出码：0 = 无 FAIL（SKIP 不算失败）；1 = 任一 FAIL；2 = 脚本自身异常。
★ 用户 P0：暂时不落 JSON 报告；真实运行脱敏证据由 Step 10.7 正式沉淀。
★ real_llm 门槛 7-9 默认 SKIP；加 --with-real-llm 才启用（需本地已有 10.5 真实产物）。

用法（PowerShell）：
  python scripts/v2_step10_verify.py                  # 默认：门槛 7-9 SKIP
  python scripts/v2_step10_verify.py --with-real-llm  # 启用 real_llm（读 10.5 本地产物）
  python scripts/v2_step10_verify.py --skip-pytest    # 只跑 ruff + 静态扫描（快速检查）
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Python 解释器（优先用当前 venv 的）
_PY = sys.executable or "python"

# ============================================================
# 22 条门槛定义（编号 / 名称 / 证据来源）
# ============================================================

_ACCEPTANCE = "tests/test_step10_acceptance.py"
_REAL_LLM = "tests/test_step10_real_llm.py"

# (门槛号, 名称, 证据来源描述, 证据类型)
# 证据类型：pytest:<nodeid 后缀> / full_pytest / ruff_check / ruff_format+scan
GATES: list[tuple[int, str, str, str]] = [
    (1, "DB 真初始化(tables>=15)", f"{_ACCEPTANCE}::test_01", "pytest:test_01_db_init_tables_ge_15"),
    (2, "schema_version=10", f"{_ACCEPTANCE}::test_02", "pytest:test_02_schema_version_10"),
    (3, "统一 Runtime 存在", f"{_ACCEPTANCE}::test_03", "pytest:test_03_runtime_exists_and_callable"),
    (4, "单一调用完成 Step 2→8", f"{_ACCEPTANCE}::test_04", "pytest:test_04_single_call_step2_to_8"),
    (5, "Web API 创建查询 Run", f"{_ACCEPTANCE}::test_05", "pytest:test_05_web_api_create_and_query_run"),
    (6, "Web 触发生成", f"{_ACCEPTANCE}::test_06", "pytest:test_06_web_api_triggers_generation"),
    (7, "真实 LLM 调用成功", f"{_REAL_LLM}::test_07", "pytest:test_07_real_llm_success"),
    (8, "真实数据入库", f"{_REAL_LLM}::test_08", "pytest:test_08_real_data_persisted"),
    (9, "Step 2→3→4→5→7→8 真实跑通", f"{_REAL_LLM}::test_09", "pytest:test_09_real_step_2_to_8_executed"),
    (10, "Human Edit 可用", f"{_ACCEPTANCE}::test_10", "pytest:test_10_human_edit_available"),
    (11, "Re-review 可用", f"{_ACCEPTANCE}::test_11", "pytest:test_11_re_review_available"),
    (12, "Revision 可查", f"{_ACCEPTANCE}::test_12", "pytest:test_12_revision_queryable"),
    (13, "Traceability 可查", f"{_ACCEPTANCE}::test_13", "pytest:test_13_traceability_queryable"),
    (14, "Coverage 可展示", f"{_ACCEPTANCE}::test_14", "pytest:test_14_coverage_displayable"),
    (15, "Review 可展示", f"{_ACCEPTANCE}::test_15", "pytest:test_15_review_displayable"),
    (16, "Optimizer 可展示", f"{_ACCEPTANCE}::test_16", "pytest:test_16_optimizer_displayable"),
    (17, "Run 状态唯一控制", f"{_ACCEPTANCE}::test_17", "pytest:test_17_runtime_sole_status_controller"),
    (18, "异常定位 failed_step", f"{_ACCEPTANCE}::test_18", "pytest:test_18_failed_step_localization"),
    (19, "V1 零回归", f"{_ACCEPTANCE}::test_19", "pytest:test_19_v1_zero_regression"),
    (20, "全量 pytest 绿", "subprocess: pytest（全量）", "full_pytest"),
    (21, "ruff check", "subprocess: ruff check .", "ruff_check"),
    (22, "ruff format + API Key 安全", "subprocess: ruff format --check . + 静态扫描", "ruff_format_scan"),
]

# ============================================================
# api_key 静态扫描（独立实现，与 test_22 同款正则；不依赖 pytest）
# ============================================================

_RE_SK_KEY = re.compile(r"sk-[A-Za-z0-9]{20,}")
_RE_HARDCODED_API_KEY = re.compile(r"""api_key\s*=\s*["']([^"']+)["']""")
_ALLOWED_SOURCES = ("os.getenv", "os.environ", "config.get", "get_model_config", "kwargs.get", ".get(")
_SENSITIVE_JSON_KEYS = ("api_key", "apikey", "authorization", "secret_key", "access_token", "auth_token", "bearer")


def scan_api_key_secrets() -> list[str]:
    """扫描生产源码 + output JSON，返回命中清单（空 = 干净）。"""
    hits: list[str] = []
    targets: list[Path] = [_PROJECT_ROOT / "scripts" / "v2_real_run.py"]
    targets += sorted((_PROJECT_ROOT / "web").glob("v2_*.py"))
    targets += sorted((_PROJECT_ROOT / "core" / "v2").glob("*.py"))
    for p in targets:
        if not p.exists():
            continue
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _RE_SK_KEY.search(line):
                hits.append(f"{p.name}:{lineno} 疑似 sk- 密钥")
            m = _RE_HARDCODED_API_KEY.search(line)
            if m and m.group(1).strip() and not any(s in line for s in _ALLOWED_SOURCES):
                hits.append(f"{p.name}:{lineno} 疑似硬编码 api_key")
    out_dir = _PROJECT_ROOT / "output"
    if out_dir.exists():
        for jf in out_dir.glob("v2_real_run_*.json"):
            lowered = jf.read_text(encoding="utf-8").lower()
            for kw in _SENSITIVE_JSON_KEYS:
                if kw in lowered:
                    hits.append(f"{jf.name} 命中敏感关键字 {kw}")
    return hits


# ============================================================
# 子进程执行
# ============================================================


def _run(cmd: list[str], *, env: dict | None = None) -> tuple[int, str]:
    """跑命令，返回 (returncode, 合并输出)。"""
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        cmd,
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_pytest(with_real_llm: bool) -> tuple[int, dict[str, str]]:
    """跑全量 pytest -v，解析每条 test 的状态。返回 (returncode, {test_name: STATUS})。"""
    env = {"V2_RUN_REAL_LLM": "1"} if with_real_llm else {"V2_RUN_REAL_LLM": ""}
    rc, out = _run([_PY, "-m", "pytest", "-v", "--tb=short"], env=env)
    statuses: dict[str, str] = {}
    # pytest -v 行：tests\xxx.py::test_name PASSED [ 50%]（Windows 用反斜杠）
    line_re = re.compile(r"^(tests[\\/]\S+\.py)::(\S+)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)")
    for line in out.splitlines():
        m = line_re.match(line.strip())
        if m:
            statuses[m.group(2)] = m.group(3)
    return rc, statuses


def run_ruff_check() -> int:
    rc, _ = _run([_PY, "-m", "ruff", "check", "."])
    return rc


def run_ruff_format_check() -> int:
    rc, _ = _run([_PY, "-m", "ruff", "format", "--check", "."])
    return rc


# ============================================================
# 显示宽度对齐（CJK 算 2 宽）
# ============================================================


def _disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _disp_width(s))


# ============================================================
# 主流程
# ============================================================


def evaluate(with_real_llm: bool, skip_pytest: bool) -> tuple[list[tuple[int, str, str, str]], int, int, int]:
    """评估 22 条门槛，返回 (rows, n_pass, n_fail, n_skip)。row = (号, 名, 证据, 状态)。"""
    rows: list[tuple[int, str, str, str]] = []

    pytest_rc = -1
    statuses: dict[str, str] = {}
    if not skip_pytest:
        print("[1/4] 跑全量 pytest -v（含 acceptance + real_llm）...")
        pytest_rc, statuses = run_pytest(with_real_llm)
    else:
        print("[1/4] --skip-pytest：跳过 pytest（门槛 1-20 标 SKIP）")

    print("[2/4] 跑 ruff check ...")
    ruff_check_rc = run_ruff_check()
    print("[3/4] 跑 ruff format --check ...")
    ruff_fmt_rc = run_ruff_format_check()
    print("[4/4] api_key 静态扫描 ...")
    scan_hits = scan_api_key_secrets()

    for num, name, evidence, kind in GATES:
        if kind.startswith("pytest:"):
            test_name = kind.split(":", 1)[1]
            if skip_pytest:
                status = "SKIP"
            else:
                raw = statuses.get(test_name)
                if raw is None:
                    status = "FAIL"  # 未采集到 = 测试缺失/崩溃
                elif raw == "PASSED":
                    status = "PASS"
                elif raw == "SKIPPED":
                    status = "SKIP"
                else:
                    status = "FAIL"
        elif kind == "full_pytest":
            if skip_pytest:
                status = "SKIP"
            else:
                status = "PASS" if pytest_rc == 0 else "FAIL"
        elif kind == "ruff_check":
            status = "PASS" if ruff_check_rc == 0 else "FAIL"
        elif kind == "ruff_format_scan":
            fmt_ok = ruff_fmt_rc == 0
            scan_ok = not scan_hits
            status = "PASS" if (fmt_ok and scan_ok) else "FAIL"
        else:
            status = "FAIL"
        rows.append((num, name, evidence, status))

    n_pass = sum(1 for r in rows if r[3] == "PASS")
    n_fail = sum(1 for r in rows if r[3] == "FAIL")
    n_skip = sum(1 for r in rows if r[3] == "SKIP")
    return rows, n_pass, n_fail, n_skip


def print_report(rows, n_pass: int, n_fail: int, n_skip: int, with_real_llm: bool, scan_hits: list[str]) -> None:
    line = "=" * 100
    print(f"\n{line}")
    print("V2 Step 10 完整端到端验证报告（22 条门槛）")
    print(line)
    header = f"{_pad('#', 4)}{_pad('门槛', 34)}{_pad('证据来源', 52)}状态"
    print(header)
    print(f"{_pad('---', 4)}{_pad('-' * 32, 34)}{_pad('-' * 50, 52)}------")
    for num, name, evidence, status in rows:
        print(f"{_pad(str(num), 4)}{_pad(name, 34)}{_pad(evidence, 52)}{status}")
    print(line)
    note = "real_llm 已启用" if with_real_llm else "real_llm 未启用（--with-real-llm 可启用门槛 7-9）"
    print(f"结果：{n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP（{note}）")
    if scan_hits:
        print("API Key 静态扫描命中：")
        for h in scan_hits:
            print(f"  - {h}")
    print(line)


def main(argv: list[str] | None = None) -> int:
    # Windows PowerShell 默认 GBK 控制台：重配 stdout 为 UTF-8，避免中文报告乱码
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="v2_step10_verify",
        description="V2 Step 10.6 完整端到端验证（22 条门槛报告表）",
    )
    parser.add_argument("--with-real-llm", action="store_true", help="启用门槛 7-9（需本地 10.5 真实产物）")
    parser.add_argument("--skip-pytest", action="store_true", help="跳过 pytest，只跑 ruff + 静态扫描")
    args = parser.parse_args(argv)

    try:
        rows, n_pass, n_fail, n_skip = evaluate(args.with_real_llm, args.skip_pytest)
        scan_hits = scan_api_key_secrets()
        print_report(rows, n_pass, n_fail, n_skip, args.with_real_llm, scan_hits)
    except Exception as e:  # 脚本自身异常
        print(f"[ERR] 验证脚本异常: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
