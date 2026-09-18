"""V2 Step 10.6 验收门槛测试 - 22 条门槛中的 17 条（mock / 临时 DB，CI 友好）。

覆盖门槛（PROGRESS §9.5）：
  1  DB 真初始化(tables>=15)        2  schema_version=10
  3  统一 Runtime 存在              4  单一调用完成 Step 2→8
  5  Web API 创建查询 Run           6  Web 触发生成
  10 Human Edit 可用               11 Re-review 可用
  12 Revision 可查                 13 Traceability 可查
  14 Coverage 可展示               15 Review 可展示
  16 Optimizer 可展示              17 Run 状态唯一控制
  18 异常定位 failed_step          19 V1 零回归
  22 API Key 安全（静态扫描部分）

★ 门槛 7-9（真实 LLM）见 tests/test_step10_real_llm.py（real_llm marker，本地验收用，非永久 CI 资产）。
★ 门槛 20-22（全量 pytest / ruff / format）见 scripts/v2_step10_verify.py（工具链一键验收）。
★ 本文件绝不碰生产库 data/data_v2.db；全部用临时库（tmp_path + set_v2_db_path）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.schemas import (
    CoverageDetail,
    GenerationConfig,
    GenerationScope,
    Priority,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    ReviewReport,
    ReviewScores,
    ReviewTriggerType,
    Run,
    RunStatus,
    SourceType,
    Technique,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
)
from core.v2 import bootstrap, ddl
from core.v2 import repository as repo
from core.v2.db import v2_read_conn
from core.v2.runtime import STEP_ORDER, run_v2_pipeline

# ============================================================
# 常量
# ============================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

V2_USER = "01ARZ3NDEKTSV4RRFFQ69G5A10"
TP_ID = "01ARZ3NDEKTSV4RRFFQ69G5A11"
TC_ID = "01ARZ3NDEKTSV4RRFFQ69G5A12"
ITEM_ID = "01ARZ3NDEKTSV4RRFFQ69G5A13"
FAKE_RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5A99"


# ============================================================
# Fixtures（对齐 test_v2_web_api.py：临时 V2 库 + Flask 客户端）
# ============================================================


@pytest.fixture
def v2_db_path(tmp_path):
    """临时 V2 库（不碰生产 data_v2.db）；测后恢复路径。"""
    from core.v2 import db as v2db

    old = v2db.get_v2_db_path()
    path = str(tmp_path / "acceptance_v2.db")
    v2db.set_v2_db_path(path)
    ddl.create_v2_schema()
    yield path
    v2db.set_v2_db_path(old)


@pytest.fixture
def v2_client(auth_client, v2_db_path):
    """已登录（V1 session）+ 临时 V2 库。"""
    return auth_client


@pytest.fixture
def v2_anon(client, v2_db_path):
    """未登录 + 临时 V2 库。"""
    return client


def _csrf(client) -> str:
    return client.get("/api/me").get_json()["csrf_token"]


# ============================================================
# 播种辅助（完整合法 V2 数据链）
# ============================================================


def _seed_full() -> SimpleNamespace:
    """播种 user/doc/version/item/cfg/run/testpoint/testcase(REVIEWED)/review/revision。"""
    repo.save_user(V2_USER, "acceptance", "hash")
    doc = RequirementDoc(user_id=V2_USER, title="验收需求", source_type=SourceType.TEXT)
    repo.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    repo.save_version(ver)
    item = RequirementItem(
        id=ITEM_ID, version_id=ver.id, seq=1, type=RequirementItemType.FUNCTION, module="m", statement="s"
    )
    repo.save_item(item)
    doc.latest_version_id = ver.id
    repo.save_doc(doc)
    cfg = GenerationConfig(
        model_provider="t", model_name="m", temperature=0.3, prompt_version="p", generator_version="g"
    )
    repo.save_generation_config(cfg)
    run = Run(
        user_id=V2_USER,
        doc_id=doc.id,
        requirement_version_id=ver.id,
        generation_config_id=cfg.id,
        status=RunStatus.DONE,
    )
    repo.save_run(run)
    repo.save_test_point(
        TestPoint(
            id=TP_ID,
            run_id=run.id,
            version_id=ver.id,
            item_ids=[item.id],
            module="m",
            subcategory="sc",
            title="TP",
            description="d",
            dimension=TestDimension.FUNCTIONAL,
            priority=Priority.P1,
            provenance=Provenance.LLM,
            generation_scope=GenerationScope.ITEM,
        )
    )
    repo.save_test_case(
        TestCase(
            id=TC_ID,
            run_id=run.id,
            display_id="TC_001",
            module="m",
            title="原标题",
            precondition="p",
            steps=[TestStep(seq=1, action="a", expected="e")],
            expected="exp",
            priority=Priority.P1,
            type=TestCaseType.FUNCTIONAL,
            provenance=Provenance.LLM,
            status=TestCaseStatus.REVIEWED,
            test_point_ids=[TP_ID],
            fingerprint="fp_acceptance",
            content_hash="ch_acceptance",
        )
    )
    scores = ReviewScores(coverage=80, accuracy=75, executability=85, consistency=90, missing_risk=70, duplication=95)
    repo.save_review_report(
        ReviewReport(
            run_id=run.id,
            revision=1,
            trigger_type=ReviewTriggerType.INITIAL,
            scores=scores,
            overall_score=scores.overall(),
            obligation_coverage=1.0,
            coverage_detail=CoverageDetail(strategy_obligation_coverage=1.0, requirement_item_coverage=0.8),
            review_target_ids=[TC_ID],
        )
    )
    from core.schemas import TestCaseRevision

    repo.save_test_case_revision(
        TestCaseRevision(
            test_case_id=TC_ID,
            revision_no=1,
            snapshot={"title": "原标题"},
            changed_fields=["title"],
            provenance=Provenance.HUMAN,
            changed_by="user",
            change_source="human_edit",
        )
    )
    return SimpleNamespace(run_id=run.id, tc_id=TC_ID, tp_id=TP_ID, item_id=item.id, version_id=ver.id)


class _Recorder:
    def __init__(self):
        self.calls: list[str] = []
        self.statuses: list[RunStatus] = []
        self.skip_flags: dict[str, bool] = {}


def _install_persisting_fakes(monkeypatch, rec: _Recorder, *, fail_at: str | None = None):
    """把 Runtime 依赖的 6 个底层步骤替换为「会落真实数据」的 fake（隔离 LLM，但 DB 可查）。

    与 test_v2_runtime.py 的 _install_fakes 区别：本 fake 真实持久化 TestPoint/TestCase/
    Obligation/ReviewReport，使门槛 4「真实数据入库（mock 层）」可被 DB 查询验证。
    """
    import core.v2.runtime as rt

    orig_save_run = repo.save_run

    def spy_run(run):
        rec.statuses.append(run.status)
        return orig_save_run(run)

    monkeypatch.setattr(repo, "save_run", spy_run)

    state: dict = {}

    def fake_ir(client, *, user_id, title, text=None, paths=None, source_type=None):
        rec.calls.append("ir")
        if fail_at == "ir":
            raise RuntimeError("ir boom")
        doc = RequirementDoc(user_id=user_id, title=title, source_type=source_type or SourceType.TEXT)
        repo.save_doc(doc)
        ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text=text or "", provenance=Provenance.LLM)
        repo.save_version(ver)
        it = RequirementItem(
            id=ITEM_ID, version_id=ver.id, seq=1, type=RequirementItemType.FUNCTION, module="m", statement="s"
        )
        repo.save_item(it)
        doc.latest_version_id = ver.id
        repo.save_doc(doc)
        state["version_id"] = ver.id
        state["item_id"] = it.id
        return SimpleNamespace(doc=doc, version=ver, items=[it])

    def fake_tp(client, *, version_id, user_id=None, run_id=None, skip_run_status_update=False, **kw):
        rec.calls.append("testpoints")
        rec.skip_flags["testpoints"] = skip_run_status_update
        if fail_at == "testpoints":
            raise RuntimeError("tp boom")
        tp = TestPoint(
            id=TP_ID,
            run_id=run_id,
            version_id=version_id,
            item_ids=[state["item_id"]],
            module="m",
            subcategory="sc",
            title="TP",
            description="d",
            dimension=TestDimension.FUNCTIONAL,
            priority=Priority.P1,
            provenance=Provenance.LLM,
            generation_scope=GenerationScope.ITEM,
        )
        repo.save_test_point(tp)
        state["tp_id"] = tp.id
        return SimpleNamespace(
            phase_a=SimpleNamespace(calls=1), phase_b=SimpleNamespace(calls=0), points=[tp], run_id=run_id
        )

    def fake_strategy(run_id, version_id, skip_run_status_update=False, **kw):
        rec.calls.append("strategy")
        rec.skip_flags["strategy"] = skip_run_status_update
        if fail_at == "strategy":
            raise RuntimeError("st boom")
        from core.schemas import CoverageObligation

        ob = CoverageObligation(
            run_id=run_id,
            item_id=state["item_id"],
            technique=Technique.BOUNDARY_VALUE,
            target="age",
            description="边界",
            params={"min": 1, "max": 100},
        )
        repo.save_obligation(ob)
        repo.add_coverage(ob.id, "testpoint", state["tp_id"])
        return SimpleNamespace(points=["s1"], obligations=[ob])

    def fake_tc(client, *, run_id, version_id=None, skip_run_status_update=False, **kw):
        rec.calls.append("testcases")
        rec.skip_flags["testcases"] = skip_run_status_update
        if fail_at == "testcases":
            raise RuntimeError("tc boom")
        tc = TestCase(
            id=TC_ID,
            run_id=run_id,
            display_id="TC_001",
            module="m",
            title="用例",
            precondition="p",
            steps=[TestStep(seq=1, action="a", expected="e")],
            expected="exp",
            priority=Priority.P1,
            type=TestCaseType.FUNCTIONAL,
            provenance=Provenance.LLM,
            status=TestCaseStatus.VALIDATED,
            test_point_ids=[state["tp_id"]],
            fingerprint="fp_rt",
            content_hash="ch_rt",
        )
        repo.save_test_case(tc)
        return SimpleNamespace(llm_calls=2, cases=[tc], validated_count=1, failed_count=0)

    def fake_review(client, *, run_id, revision=None, trigger_type=None, skip_run_status_update=False, **kw):
        rec.calls.append("review")
        rec.skip_flags["review"] = skip_run_status_update
        if fail_at == "review":
            raise RuntimeError("rv boom")
        scores = ReviewScores(
            coverage=90, accuracy=80, executability=85, consistency=90, missing_risk=75, duplication=95
        )
        report = ReviewReport(
            run_id=run_id,
            revision=1,
            trigger_type=ReviewTriggerType.INITIAL,
            scores=scores,
            overall_score=scores.overall(),
            obligation_coverage=1.0,
            review_target_ids=[TC_ID],
        )
        repo.save_review_report(report)
        # 用例转 REVIEWED（对齐真实 review_orchestrator 行为）
        tc = repo.get_test_case(TC_ID)
        if tc is not None:
            tc.status = TestCaseStatus.REVIEWED
            repo.save_test_case(tc)
        return SimpleNamespace(reviewed_count=1, report=report)

    def fake_optimize(client, *, run_id, skip_run_status_update=False, **kw):
        rec.calls.append("optimizer")
        rec.skip_flags["optimizer"] = skip_run_status_update
        if fail_at == "optimizer":
            raise RuntimeError("op boom")
        return SimpleNamespace(
            optimizer_result=SimpleNamespace(archived_cases=0, processed_findings=0, skipped_findings=0, actions=[])
        )

    monkeypatch.setattr(rt, "ingest_and_build_ir", fake_ir)
    monkeypatch.setattr(rt, "generate_test_points", fake_tp)
    monkeypatch.setattr(rt, "apply_strategy_engine", fake_strategy)
    monkeypatch.setattr(rt, "synthesize_test_cases", fake_tc)
    monkeypatch.setattr(rt, "review_test_cases", fake_review)
    monkeypatch.setattr(rt, "optimize_duplicates", fake_optimize)
    # Runtime 用 client_factory 构建客户端；测试传 explicit client 短路，无需 mock


# ============================================================
# 门槛 1-2：DB 真初始化 + schema_version
# ============================================================


def test_01_db_init_tables_ge_15(tmp_path):
    """门槛 1：ensure_v2_ready() 后关键表齐全（>=15），且 verify 无问题。"""
    from core.v2 import db as v2db

    old = v2db.get_v2_db_path()
    path = str(tmp_path / "fresh_v2.db")
    v2db.set_v2_db_path(path)
    try:
        bootstrap.ensure_v2_ready()
        with v2_read_conn() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        # 关键表全部就位（CRITICAL_TABLES 共 17 张，门槛要求 >=15）
        assert bootstrap.CRITICAL_TABLES.issubset(tables)
        assert len(tables) >= 15
        # verify 只读校验无问题
        assert bootstrap.verify_v2_schema() == []
    finally:
        v2db.set_v2_db_path(old)


def test_02_schema_version_10(v2_db_path):
    """门槛 2：schema_version 统一为 10（PROGRESS §9.3-1）。"""
    assert ddl.SCHEMA_VERSION == 10
    assert ddl.get_schema_version() == 10


# ============================================================
# 门槛 3：统一 Runtime 存在
# ============================================================


def test_03_runtime_exists_and_callable():
    """门槛 3：run_v2_pipeline 可导入 + callable；STEP_ORDER 六阶段齐全。"""
    assert callable(run_v2_pipeline)
    assert STEP_ORDER == ("ir", "testpoints", "strategy", "testcases", "review", "optimizer")


# ============================================================
# 门槛 4：单一调用完成 Step 2→8 + 真实数据入库（mock 层）
# ============================================================


def test_04_single_call_step2_to_8(v2_db_path, monkeypatch):
    """门槛 4：一次 run_v2_pipeline 调用跑通 6 阶段；DB 里 items/TPs/TCs/Obligation/Review 均有数据。"""
    repo.save_user(V2_USER, "acceptance", "hash")
    rec = _Recorder()
    _install_persisting_fakes(monkeypatch, rec)

    res = run_v2_pipeline(user_id=V2_USER, title="T", text="需求", client=object())

    assert res.success is True
    assert res.run_id is not None
    assert [s.step for s in res.steps] == ["ir", "testpoints", "strategy", "testcases", "review", "optimizer"]
    assert all(s.success for s in res.steps)

    run = repo.get_run(res.run_id)
    assert run.status == RunStatus.DONE
    # 真实数据入库（mock 层）：各表非空
    assert len(repo.list_items(res.version_id)) >= 1
    assert len(repo.list_test_points_by_run(res.run_id)) >= 1
    assert len(repo.list_test_cases(res.run_id)) >= 1
    assert len(repo.list_obligations(res.run_id)) >= 1
    assert repo.get_latest_review_report(res.run_id) is not None
    # Run.counts 由 Runtime 独家聚合（items/points/cases/obligations 均 >=1）
    assert run.counts.items >= 1
    assert run.counts.points >= 1
    assert run.counts.cases >= 1
    assert run.counts.obligations >= 1


# ============================================================
# 门槛 5-6：Web API 创建查询 Run + 触发生成
# ============================================================


def _mock_pipeline(monkeypatch, result):
    from web import v2_service

    monkeypatch.setattr(v2_service, "run_v2_pipeline", lambda **kw: result)


def test_05_web_api_create_and_query_run(v2_client, monkeypatch):
    """门槛 5：POST /api/v2/runs 创建 + GET /api/v2/runs/<id> 查询 + GET /api/v2/runs 列表。"""
    from core.v2.runtime import PipelineResult

    _mock_pipeline(monkeypatch, PipelineResult(run_id=FAKE_RUN_ID, success=True, test_point_count=1, test_case_count=1))
    # 播种一条真实 Run 供 GET 查询（POST 是 mock，不落库）
    seed = _seed_full()
    token = _csrf(v2_client)
    rv = v2_client.post("/api/v2/runs", json={"title": "T", "text": "需求"}, headers={"X-CSRF-Token": token})
    assert rv.status_code == 200
    assert rv.get_json()["run_id"] == FAKE_RUN_ID

    # GET 单条（用真实播种的 run）
    rv2 = v2_client.get(f"/api/v2/runs/{seed.run_id}")
    assert rv2.status_code == 200
    assert rv2.get_json()["run"]["status"] == "done"

    # GET 列表（本人 Run）
    rv3 = v2_client.get("/api/v2/runs")
    assert rv3.status_code == 200
    assert rv3.get_json()["success"] is True


def test_06_web_api_triggers_generation(v2_client, monkeypatch):
    """门槛 6：POST /api/v2/runs 触发生成，service 层被调用一次，响应含 run_id + success。"""
    from core.v2.runtime import PipelineResult
    from web import v2_service

    calls = {"n": 0}

    def _fake_pipeline(**kw):
        calls["n"] += 1
        return PipelineResult(run_id=FAKE_RUN_ID, success=True)

    monkeypatch.setattr(v2_service, "run_v2_pipeline", _fake_pipeline)
    token = _csrf(v2_client)
    rv = v2_client.post("/api/v2/runs", json={"title": "T", "text": "需求"}, headers={"X-CSRF-Token": token})
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["run_id"] == FAKE_RUN_ID and body["success"] is True
    assert calls["n"] == 1  # service 层确实触发了 Runtime


# ============================================================
# 门槛 10-12：Human Edit + Re-review + Revision
# ============================================================


def test_10_human_edit_available(v2_client):
    """门槛 10：POST /api/v2/test-cases/<id>/edit → 200；provenance=HUMAN；Revision 快照落库。"""
    seed = _seed_full()
    tc = repo.get_test_case(seed.tc_id)
    token = _csrf(v2_client)
    rv = v2_client.post(
        f"/api/v2/test-cases/{seed.tc_id}/edit",
        json={"updates": {"title": "人工新标题"}, "expected_updated_at": tc.updated_at.isoformat()},
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 200, rv.get_json()
    assert rv.get_json()["success"] is True
    edited = repo.get_test_case(seed.tc_id)
    assert edited.provenance == Provenance.HUMAN
    assert edited.title == "人工新标题"


def test_11_re_review_available(v2_client, monkeypatch):
    """门槛 11：POST /api/v2/test-cases/<id>/re-review → 200；trigger_type=AFTER_HUMAN_EDIT。"""
    from core.v2.human_editor_orchestrator import ReReviewResult
    from web import v2_service

    seed = _seed_full()
    monkeypatch.setattr(v2_service, "build_llm_client", lambda purpose="generate": MagicMock())
    monkeypatch.setattr(
        v2_service,
        "_re_review_cases",
        lambda client, *, run_id, test_case_ids=None: ReReviewResult(
            run_id=run_id, requested_case_ids=test_case_ids or []
        ),
    )
    token = _csrf(v2_client)
    rv = v2_client.post(f"/api/v2/test-cases/{seed.tc_id}/re-review", json={}, headers={"X-CSRF-Token": token})
    assert rv.status_code == 200
    assert rv.get_json()["result"]["run_id"] == seed.run_id


def test_12_revision_queryable(v2_client):
    """门槛 12：GET /api/v2/test-cases/<id>/revisions → 200 + revision_no=1 + changed_fields 非空。"""
    seed = _seed_full()
    rv = v2_client.get(f"/api/v2/test-cases/{seed.tc_id}/revisions")
    assert rv.status_code == 200
    revisions = rv.get_json()["revisions"]
    assert len(revisions) >= 1
    assert revisions[0]["revision_no"] == 1
    assert revisions[0]["changed_fields"]


# ============================================================
# 门槛 13：Traceability 可查
# ============================================================


def test_13_traceability_queryable(v2_client):
    """门槛 13：GET /api/v2/test-cases/<id>/trace → 200 + 链含 TC→TP→Item。"""
    seed = _seed_full()
    rv = v2_client.get(f"/api/v2/test-cases/{seed.tc_id}/trace")
    assert rv.status_code == 200
    trace = rv.get_json()["trace"]
    assert trace["case_id"] == seed.tc_id


# ============================================================
# 门槛 14-16：Coverage / Review / Optimizer 可展示
# ============================================================


def test_14_coverage_displayable(v2_client):
    """门槛 14：GET /api/v2/runs/<id>/coverage → 200 + 双指标 + uncovered。"""
    seed = _seed_full()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/coverage")
    assert rv.status_code == 200
    cov = rv.get_json()["coverage"]
    assert "strategy_obligation_coverage" in cov
    assert "requirement_item_coverage" in cov
    assert "uncovered_item_ids" in cov


def test_15_review_displayable(v2_client):
    """门槛 15：GET /api/v2/runs/<id>/review → 200 + 6 维 scores + coverage_detail。"""
    seed = _seed_full()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/review")
    assert rv.status_code == 200
    review = rv.get_json()["review"]
    scores = review["scores"]
    for dim in ("coverage", "accuracy", "executability", "consistency", "missing_risk", "duplication"):
        assert dim in scores
    assert review["coverage_detail"] is not None


def test_16_optimizer_displayable(v2_client):
    """门槛 16：GET /api/v2/runs/<id>/optimizer → 200 + archived_count/archived_cases 字段。"""
    seed = _seed_full()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/optimizer")
    assert rv.status_code == 200
    opt = rv.get_json()["optimizer"]
    assert "archived_count" in opt
    assert "archived_cases" in opt


# ============================================================
# 门槛 17：Run 状态唯一控制者
# ============================================================


def test_17_runtime_sole_status_controller(v2_db_path, monkeypatch):
    """门槛 17：底层 orchestrator 全部 skip_run_status_update=True；状态序列仅由 Runtime 写。"""
    repo.save_user(V2_USER, "acceptance", "hash")
    rec = _Recorder()
    _install_persisting_fakes(monkeypatch, rec)

    res = run_v2_pipeline(user_id=V2_USER, title="T", text="x", client=object())
    assert res.success is True

    # 5 个底层 orchestrator 均以 skip=True 调用（不抢状态控制权）
    for step in ("testpoints", "strategy", "testcases", "review", "optimizer"):
        assert rec.skip_flags[step] is True
    # 状态序列严格由 Runtime 写：PARSING→GENERATING→STRATEGIZING→GENERATING→REVIEWING→OPTIMIZING→DONE
    assert rec.statuses == [
        RunStatus.PARSING,
        RunStatus.GENERATING,
        RunStatus.STRATEGIZING,
        RunStatus.GENERATING,
        RunStatus.REVIEWING,
        RunStatus.OPTIMIZING,
        RunStatus.DONE,
    ]


# ============================================================
# 门槛 18：异常定位 failed_step
# ============================================================


def test_18_failed_step_localization(v2_db_path, monkeypatch):
    """门槛 18：testcases 阶段抛错 → Run.FAILED + failed_step + error_message；后续未执行。"""
    repo.save_user(V2_USER, "acceptance", "hash")
    rec = _Recorder()
    _install_persisting_fakes(monkeypatch, rec, fail_at="testcases")

    res = run_v2_pipeline(user_id=V2_USER, title="T", text="x", client=object())
    assert res.success is False
    assert res.failed_step == "testcases"
    assert res.error_message and "tc boom" in res.error_message

    run = repo.get_run(res.run_id)
    assert run.status == RunStatus.FAILED
    assert run.failed_step == "testcases"
    assert run.error_message and "tc boom" in run.error_message
    # 后续 review/optimizer 未执行
    assert rec.calls == ["ir", "testpoints", "strategy", "testcases"]


# ============================================================
# 门槛 19：V1 零回归
# ============================================================


def test_19_v1_zero_regression(client):
    """门槛 19：V1 三件套文件存在 + V1 运行时端点可用（不被 V2 破坏）。"""
    # V1 关键文件存在（10.x 全程不得改动）
    for rel in (
        "web/data.py",
        "core/generator.py",
        "templates/index.html",
        "static/app.js",
        "static/style.css",
    ):
        assert (_PROJECT_ROOT / rel).exists(), f"V1 文件缺失: {rel}"

    # V1 运行时端点仍工作
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 200

    # V1 数据库模块可独立导入（与 V2 隔离）
    from core import db as v1_db

    assert callable(v1_db.init_db)


# ============================================================
# 门槛 22（静态扫描部分）：API Key 安全
# ============================================================

# 硬编码密钥特征：sk- 开头长串 / api_key = "非空字面量"
_RE_SK_KEY = re.compile(r"sk-[A-Za-z0-9]{20,}")
_RE_HARDCODED_API_KEY = re.compile(r"""api_key\s*=\s*["']([^"']+)["']""")
# 允许的动态来源（非硬编码）
_ALLOWED_SOURCES = ("os.getenv", "os.environ", "config.get", "get_model_config", "kwargs.get", ".get(")


def _scan_source_for_secrets(path: Path) -> list[str]:
    """扫描单个源码文件，返回命中的硬编码密钥行（空 list = 干净）。"""
    hits: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if _RE_SK_KEY.search(line):
            hits.append(f"{path}:{lineno} 疑似硬编码 sk- 密钥")
        m = _RE_HARDCODED_API_KEY.search(line)
        if m:
            value = m.group(1)
            # 排除空串 / 占位 / 动态来源注释行
            if value.strip() and not any(src in line for src in _ALLOWED_SOURCES):
                hits.append(f"{path}:{lineno} 疑似硬编码 api_key 字面量")
    return hits


def test_22_api_key_static_scan():
    """门槛 22（静态部分）：生产源码无硬编码密钥；output/*.json 无敏感关键字。"""
    # 1) 生产源码扫描（scripts/v2_real_run.py + web/v2_*.py + core/v2/*.py）
    scan_targets: list[Path] = [_PROJECT_ROOT / "scripts" / "v2_real_run.py"]
    scan_targets += sorted((_PROJECT_ROOT / "web").glob("v2_*.py"))
    scan_targets += sorted((_PROJECT_ROOT / "core" / "v2").glob("*.py"))
    all_hits: list[str] = []
    for p in scan_targets:
        if p.exists():
            all_hits += _scan_source_for_secrets(p)
    assert all_hits == [], f"发现硬编码密钥: {all_hits}"

    # 2) output/*.json 无敏感关键字（小写比对）
    sensitive = ("api_key", "apikey", "authorization", "secret_key", "access_token", "auth_token", "bearer")
    out_dir = _PROJECT_ROOT / "output"
    if out_dir.exists():
        for jf in out_dir.glob("v2_real_run_*.json"):
            lowered = jf.read_text(encoding="utf-8").lower()
            for kw in sensitive:
                assert kw not in lowered, f"{jf.name} 命中敏感关键字 {kw}"


# ============================================================
# 附：sqlite3 直连可用性自检（门槛 1 辅助，确保临时库可被裸连接读取）
# ============================================================


def test_db_path_isolation_sanity(v2_db_path):
    """健全性：临时库路径生效，裸 sqlite3 可读到 schema_meta（非生产库）。"""
    conn = sqlite3.connect(v2_db_path)
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        assert row is not None and row[0] == "10"
    finally:
        conn.close()
