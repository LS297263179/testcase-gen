"""V2 Web 服务层 - HTTP 请求 ↔ core/v2 适配（Step 10.3）。

职责边界（P0-2）：只做"参数 → core/v2 调用 → JSON 安全序列化"，不含业务流程控制、绝不改 Run 状态。
业务流程全在 core/v2/runtime.py（Runtime 是 Run 状态唯一控制者）；本层只当"翻译官"。

★ MVP 限制（P0-4）：同步执行，不实现 Celery / Redis / 消息队列 / 后台 Job。
★ 范围（P0-5）：只把已有 core/v2 能力接到 Web，不扩 Runtime、不新增 AI 能力。
★ 端点（Step 10.3.1 + Step 11 UI 重构）：共 14 个 /api/v2/* 端点
        （1 健康检查 + 1 POST /runs + 8 Run/资产查询 + 4 TestCase 人工/追溯）。
"""

from __future__ import annotations

import logging
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum

from flask import current_app
from pydantic import BaseModel

from core.schemas import SourceType, new_ulid
from core.v2 import repository as repo
from core.v2 import traceability
from core.v2.client_factory import build_llm_client
from core.v2.ddl import get_schema_version
from core.v2.human_editor import edit_test_case as _edit_test_case
from core.v2.human_editor_orchestrator import re_review_test_cases as _re_review_cases
from core.v2.runtime import run_v2_pipeline

logger = logging.getLogger("web.v2_service")


# ============================================================
# 序列化：core/v2 产物（Pydantic / dataclass / enum / datetime）→ JSON 安全结构
# ============================================================


def _jsonable(obj):
    """递归把任意 core/v2 产物转成可 jsonify 的结构。"""
    if isinstance(obj, Enum):  # StrEnum 也是 str 子类，先取 .value
        return obj.value
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    return str(obj)


def _iso(dt) -> str:
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _normalize_lock_ts(ts: str | None) -> str | None:
    """归一乐观锁时间戳为 DB 存储格式（datetime.isoformat() → ...+00:00）。

    ★ 坑：API 序列化用 Pydantic model_dump(mode="json")，UTC datetime 输出为 "...Z"；
      而 DB 列与 _iso() 用 "...+00:00"。前端把 API 返回的 updated_at 原样回传时，
      两种写法字符串直接比对必然不等 → 每次人工编辑都误报 409。故比对/入库前先归一。
    """
    if not ts:
        return ts
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return ts


# ============================================================
# V1 session → V2 user 自动映射（链路：session user_id → 查/建 V2 user）
# ============================================================


def _resolve_v2_user_id(session_user_id: int, username: str) -> str:
    """V1 session 用户（整型 id）→ V2 ULID 用户：有则复用，无则自动开通。

    V2 不做认证（认证仍走 V1 session）；V2 user 行仅为满足 Run.user_id NOT NULL 外键，
    故 password_hash 用占位空串。
    """
    row = repo.get_user_by_legacy_id(session_user_id)
    if row is not None:
        return row["id"]
    uid = new_ulid()
    repo.save_user(uid, username or f"v2_user_{session_user_id}", "", legacy_int_id=session_user_id)
    logger.info("自动开通 V2 用户: legacy_int_id=%s -> %s", session_user_id, uid)
    return uid


# ============================================================
# 健康检查（P0-1：免登录）
# ============================================================


def health() -> tuple[dict, int]:
    """V2 健康检查。ready→(200, ok)；否则→(503, degraded)。schema_version 安全读，异常→None。"""
    v2_ready = bool(current_app.config.get("V2_READY", False))
    schema_version = None
    if v2_ready:
        try:
            schema_version = get_schema_version()
        except Exception:
            schema_version = None
    ok = v2_ready and schema_version is not None
    body = {"status": "ok" if ok else "degraded", "v2_ready": v2_ready, "schema_version": schema_version}
    return body, (200 if ok else 503)


# ============================================================
# 生成链路（POST /runs）
# ============================================================


def create_run_and_generate(
    session_user_id: int, username: str, title: str, text: str, source_type: str = "text"
) -> dict:
    """解析 V2 user → run_v2_pipeline（同步）→ 序列化 PipelineResult + Run 最终 status。

    ★ P0-3：Runtime FAILED 时 payload 保留 run_id / status / failed_step / error_message，
      绝不塌缩成 {"success": false}，让前端能定位挂在哪一步。
    """
    v2_uid = _resolve_v2_user_id(session_user_id, username)
    try:
        st = SourceType(source_type)
    except ValueError:
        st = SourceType.TEXT
    result = run_v2_pipeline(user_id=v2_uid, title=title, text=text, source_type=st)
    payload = _jsonable(result)
    if result.run_id:
        run = repo.get_run(result.run_id)
        if run is not None:
            payload["status"] = run.status.value  # DONE / FAILED（P0-3 前端据此判断）
    return payload


# ============================================================
# 查询接口（Run / TestPoint / TestCase / Review / Optimizer / Coverage）
# ============================================================


def run_exists(run_id: str) -> bool:
    return repo.get_run(run_id) is not None


def test_case_exists(tc_id: str) -> bool:
    return repo.get_test_case(tc_id) is not None


def get_run_detail(run_id: str) -> dict | None:
    run = repo.get_run(run_id)
    return _jsonable(run) if run is not None else None


def list_runs(session_user_id: int, limit: int = 50) -> list[dict]:
    """列出当前登录用户自己的 Run（Step 10.3.1，MVP：最近 limit 条，默认 50）。

    ★ GET 不自动开通 V2 user（避免读操作产生写副作用）：无 V2 user → 返回 []。
    ★ Run 无 title 字段，title 从其 doc（repo.get_doc(run.doc_id).title）取，缺失兜底 "(无标题)"。
    ★ 严格 MVP：不做高级筛选/多条件搜索/分页体系/排序配置/项目维度/团队维度。
    """
    row = repo.get_user_by_legacy_id(session_user_id)
    if row is None:
        return []
    runs = repo.list_runs_by_user(row["id"], limit)
    items: list[dict] = []
    for run in runs:
        doc = repo.get_doc(run.doc_id)
        title = doc.title if (doc is not None and doc.title) else "(无标题)"
        items.append(
            {
                "run_id": run.id,
                "title": title,
                "status": _jsonable(run.status),
                "created_at": _jsonable(run.created_at),
                "failed_step": run.failed_step,
            }
        )
    return items


def list_test_points(run_id: str, provenance: str | None = None) -> list[dict]:
    points = repo.list_test_points_by_run(run_id)
    if provenance:
        points = [p for p in points if p.provenance.value == provenance]
    return [_jsonable(p) for p in points]


def list_test_cases(run_id: str, status: str | None = None) -> list[dict]:
    cases = repo.list_test_cases(run_id)
    if status:
        cases = [c for c in cases if c.status.value == status]
    return [_jsonable(c) for c in cases]


def get_review_report(run_id: str) -> dict | None:
    report = repo.get_latest_review_report(run_id)
    return _jsonable(report) if report is not None else None


def get_optimizer_result(run_id: str) -> dict:
    """OptimizerResult 内存不持久化（Step 8 决策）；此处返回其持久化效果 = ARCHIVED 用例（诚实替代）。"""
    cases = repo.list_test_cases(run_id)
    archived = [c for c in cases if c.status.value == "archived"]
    return {
        "run_id": run_id,
        "archived_count": len(archived),
        "archived_cases": [_jsonable(c) for c in archived],
        "note": "OptimizerResult 不持久化（Step 8）；返回其持久化效果=归档用例",
    }


def get_coverage(run_id: str) -> dict:
    """Coverage 双指标：优先取最新 ReviewReport.coverage_detail（含双指标 + uncovered）；否则退回硬指标。"""
    report = repo.get_latest_review_report(run_id)
    if report is not None and report.coverage_detail is not None:
        return _jsonable(report.coverage_detail)
    return {
        "strategy_obligation_coverage": repo.obligation_coverage_ratio(run_id),
        "requirement_item_coverage": None,
        "uncovered_item_ids": [],
    }


def get_requirements_context(run_id: str) -> dict | None:
    """需求与 AI 分析上下文（只读，Step 11 UI 重构新增）：Doc + Version + Item 列表。

    仅用于 V2 前端「需求与 AI 分析」页展示 Requirement IR；
    复用已有 repository 查询，不改 schema / Runtime / Run 数据结构。Run 不存在→None（上层 404）。
    """
    run = repo.get_run(run_id)
    if run is None:
        return None
    doc = repo.get_doc(run.doc_id)
    version = repo.get_version(run.requirement_version_id)
    items = repo.list_items(run.requirement_version_id)
    return {
        "run_id": run.id,
        "status": _jsonable(run.status),
        "doc": _jsonable(doc) if doc is not None else None,
        "version": _jsonable(version) if version is not None else None,
        "items": [_jsonable(i) for i in items],
        "item_count": len(items),
        "field_count": sum(len(i.fields) for i in items),
        "rule_count": sum(len(i.rules) for i in items),
        "permission_count": sum(len(i.permissions) for i in items),
        "modules": sorted({i.module for i in items}),
    }


# ============================================================
# 人工编辑 / 重评审 / 修订 / 追溯（Step 9 + Step 6，用户后续主动操作）
# ============================================================


def edit_test_case(tc_id: str, updates: dict, expected_updated_at: str | None = None) -> dict | None:
    """人工编辑（Step 9）。TestCase 不存在→None（上层 404）；乐观锁冲突→raise ConcurrentModificationError（上层 409）。"""
    tc = repo.get_test_case(tc_id)
    if tc is None:
        return None
    # 归一前端回传的时间戳（可能是 API 序列化的 ...Z），与 DB 存储格式（...+00:00）对齐后再比对/入库
    expected = _normalize_lock_ts(expected_updated_at)
    # 乐观锁预检：expected_updated_at 与当前不符 → 冲突（edit_test_case 内部会吞成 success=False，此处显式抛给上层 409）
    if expected is not None and _iso(tc.updated_at) != expected:
        raise repo.ConcurrentModificationError(f"用例已被更新（当前 updated_at={_iso(tc.updated_at)}），请刷新后重试")
    return _jsonable(_edit_test_case(tc_id, updates, expected_updated_at=expected))


def re_review(run_id: str, tc_ids: list[str] | None = None) -> dict:
    """用户主动触发 AFTER_HUMAN_EDIT 重评审（Step 9）。评审客户端按 review 用途构建（未启用回退 generate）。"""
    client = build_llm_client("review")
    return _jsonable(_re_review_cases(client, run_id=run_id, test_case_ids=tc_ids))


def list_revisions(tc_id: str) -> list[dict]:
    return [_jsonable(r) for r in repo.get_test_case_revisions(tc_id)]


def trace_test_case(tc_id: str) -> dict:
    return _jsonable(traceability.trace_backward_from_case(tc_id))
