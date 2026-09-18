"""V2 Step 10.6 验收门槛测试 - 门槛 7-9（真实 LLM，real_llm marker）。

★ 定位（用户 P0 补充）：本文件是「本地验收用」测试，**非永久 CI 测试资产**。
  真实运行证据（data/data_v2.db + output/v2_real_run_*.json）被 .gitignore 排除，
  不随仓库分发；最终可提交仓库的脱敏证据由 Step 10.7 正式沉淀（docs/v2/step10-real-run-record.md）。
  因此：
    - 默认 skip（未设 V2_RUN_REAL_LLM）；
    - 启用后若本地无 10.5 真实产物，仍 skip（reason 明确），绝不 fail；
    - 本文件直接读生产库 data/data_v2.db（real_llm 特权），mock 测试绝不碰生产库。

覆盖门槛（PROGRESS §9.5）：
  7  真实 LLM 调用成功
  8  真实数据入库
  9  Step 2→3→4→5→7→8 真实跑通

启用方式（PowerShell）：
  $env:V2_RUN_REAL_LLM=1; pytest tests/test_step10_real_llm.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT_DIR = _PROJECT_ROOT / "output"
_PROD_V2_DB = _PROJECT_ROOT / "data" / "data_v2.db"

# 6 阶段严格顺序（与 core.v2.runtime.STEP_ORDER 对齐）
_EXPECTED_STEP_ORDER = ["ir", "testpoints", "strategy", "testcases", "review", "optimizer"]

pytestmark = [
    pytest.mark.real_llm,
    pytest.mark.skipif(
        not os.getenv("V2_RUN_REAL_LLM"),
        reason="本地验收用：需设 V2_RUN_REAL_LLM=1 且本地已有 10.5 真实产物（非永久 CI 资产）",
    ),
]


def _find_latest_real_run_json() -> Path:
    """从 output/v2_real_run_*.json 通配符取最新一个；找不到 → skip（不 fail）。"""
    if not _OUTPUT_DIR.exists():
        pytest.skip("10.5 真实产物不存在（output/ 目录缺失）")
    candidates = sorted(_OUTPUT_DIR.glob("v2_real_run_*.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        pytest.skip("10.5 真实产物不存在（output/v2_real_run_*.json 为空）")
    return candidates[-1]


def _load_payload() -> dict:
    path = _find_latest_real_run_json()
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================
# 门槛 7：真实 LLM 调用成功
# ============================================================


def test_07_real_llm_success():
    """门槛 7：10.5 真实运行产物 success=true / 无 failed_step / LLM 调用数 > 0 / run_status=done。"""
    payload = _load_payload()
    assert payload["success"] is True
    assert payload["failed_step"] is None
    assert payload["error_message"] is None
    assert payload["total_llm_calls"] > 0
    assert payload.get("run_status") == "done"
    # 配置快照白名单：绝不含 api_key（双保险，与门槛 22 呼应）
    assert "api_key" not in json.dumps(payload).lower()


# ============================================================
# 门槛 8：真实数据入库
# ============================================================


def test_08_real_data_persisted():
    """门槛 8：用 JSON 的 run_id 查生产库 data_v2.db，计数与 JSON counts 一致。"""
    payload = _load_payload()
    run_id = payload["run_id"]
    if not _PROD_V2_DB.exists():
        pytest.skip("生产库 data/data_v2.db 不存在（10.5 未在本机跑过）")

    conn = sqlite3.connect(str(_PROD_V2_DB))
    try:
        row = conn.execute("SELECT status, failed_step FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            pytest.skip(f"生产库无 run_id={run_id}（10.5 产物与本机 DB 不匹配）")
        assert row[0] == "done"
        assert row[1] is None

        counts = payload.get("counts") or {}
        # 交叉验证：DB 实际计数 >= JSON 记录（optimizer 可能归档部分用例，但行仍在）
        tp_db = conn.execute("SELECT COUNT(*) FROM test_points WHERE run_id = ?", (run_id,)).fetchone()[0]
        tc_db = conn.execute("SELECT COUNT(*) FROM test_cases WHERE run_id = ?", (run_id,)).fetchone()[0]
        ob_db = conn.execute("SELECT COUNT(*) FROM coverage_obligations WHERE run_id = ?", (run_id,)).fetchone()[0]
        rr_db = conn.execute("SELECT COUNT(*) FROM review_reports WHERE run_id = ?", (run_id,)).fetchone()[0]

        assert tp_db == counts.get("points", tp_db)
        assert tc_db == counts.get("cases", tc_db)
        assert ob_db == counts.get("obligations", ob_db)
        assert rr_db >= 1  # 至少一轮评审报告入库
    finally:
        conn.close()


# ============================================================
# 门槛 9：Step 2→3→4→5→7→8 真实跑通
# ============================================================


def test_09_real_step_2_to_8_executed():
    """门槛 9：JSON steps[] 六阶段齐全、顺序正确、每步成功且有时耗；review/optimizer 摘要完整。"""
    payload = _load_payload()
    steps = payload["steps"]
    assert len(steps) == 6
    assert [s["step"] for s in steps] == _EXPECTED_STEP_ORDER
    for s in steps:
        assert s["success"] is True, f"阶段 {s['step']} 未成功"
        assert s["duration_ms"] > 0, f"阶段 {s['step']} 时耗异常"
        assert isinstance(s.get("artifact_ids"), dict) and s["artifact_ids"], f"阶段 {s['step']} 无 artifact_ids"

    # Review 6 维评分齐全
    review = payload.get("review_summary") or {}
    scores = review.get("scores") or {}
    for dim in ("coverage", "accuracy", "executability", "consistency", "missing_risk", "duplication"):
        assert dim in scores, f"review 缺少维度 {dim}"
    assert review.get("coverage_detail") is not None

    # Optimizer 摘要存在且归档数非负
    opt = payload.get("optimizer_summary") or {}
    assert opt.get("archived_cases", 0) >= 0
    assert "processed_findings" in opt
