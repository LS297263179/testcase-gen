"""Step 10.3 验收测试 - V2 Web API（/api/v2/*）。

覆盖 5 条 P0：
  P0-1 /api/v2/health 免登录（ready→200 ok / degraded→503）
  P0-2 API 层不控 Run 状态（只 route→service→runtime；本测试不触发任何 web 侧 save_run）
  P0-3 错误分级 400/401/403/503/500；Runtime FAILED 回传 run_id/status/failed_step/error_message（不塌缩）
  P0-4 同步执行（POST /runs 直接返回 PipelineResult）
  P0-5 仅接已有能力（POST /runs 仅 JSON）
外加：V1 session→V2 user 自动映射（首建/复用）、12 端点、404、乐观锁 409、V1 零回归。

策略：POST /runs 用 monkeypatch 替换 v2_service.run_v2_pipeline（免真实 LLM）；GET 端点用真实播种数据。
"""

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
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestDimension,
    TestPoint,
    TestStep,
)
from core.v2 import repository as repo
from core.v2.runtime import PipelineResult
from web import v2_service

V2_USER = "01ARZ3NDEKTSV4RRFFQ69G5V2A"
TP_ID = "01ARZ3NDEKTSV4RRFFQ69G5TP1"
TP2_ID = "01ARZ3NDEKTSV4RRFFQ69G5TP2"
TC_ID = "01ARZ3NDEKTSV4RRFFQ69G5TC1"
TC2_ID = "01ARZ3NDEKTSV4RRFFQ69G5TC2"
FAKE_RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5RZ1"


@pytest.fixture
def v2_db_path(tmp_path):
    """临时 V2 库（不改 conftest）；测后恢复。"""
    from core.v2 import db as v2db
    from core.v2 import ddl

    old = v2db.get_v2_db_path()
    path = str(tmp_path / "web_v2.db")
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


def _seed_v2() -> SimpleNamespace:
    """播种完整 V2 数据：user/doc/version/item/cfg/run/testpoints/testcase(REVIEWED)/archived/review/revision。"""
    repo.save_user(V2_USER, "v2alice", "hash")
    doc = RequirementDoc(user_id=V2_USER, title="T", source_type=SourceType.TEXT)
    repo.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    repo.save_version(ver)
    item = RequirementItem(version_id=ver.id, seq=1, type=RequirementItemType.FUNCTION, module="m", statement="s")
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
    for tp_id in (TP_ID, TP2_ID):
        repo.save_test_point(
            TestPoint(
                id=tp_id,
                run_id=run.id,
                version_id=ver.id,
                item_ids=[item.id],
                module="m",
                subcategory="sc",
                title=f"TP-{tp_id[-1]}",
                description="d",
                dimension=TestDimension.FUNCTIONAL,
                priority=Priority.P1,
                provenance=Provenance.LLM,
                generation_scope=GenerationScope.ITEM,
            )
        )

    def _tc(tc_id, tp_id, display_id, status):
        return TestCase(
            id=tc_id,
            run_id=run.id,
            display_id=display_id,
            module="m",
            title="原标题",
            precondition="p",
            steps=[TestStep(seq=1, action="a", expected="e")],
            expected="exp",
            priority=Priority.P1,
            type=TestCaseType.FUNCTIONAL,
            provenance=Provenance.LLM,
            status=status,
            test_point_ids=[tp_id],
            fingerprint=f"fp_{tc_id[-1]}",
            content_hash=f"ch_{tc_id[-1]}",
        )

    repo.save_test_case(_tc(TC_ID, TP_ID, "TC_001", TestCaseStatus.REVIEWED))
    repo.save_test_case(_tc(TC2_ID, TP2_ID, "TC_002", TestCaseStatus.ARCHIVED))

    scores = ReviewScores(coverage=80, accuracy=75, executability=85, consistency=90, missing_risk=70, duplication=95)
    report = ReviewReport(
        run_id=run.id,
        revision=1,
        trigger_type=ReviewTriggerType.INITIAL,
        scores=scores,
        overall_score=scores.overall(),
        obligation_coverage=1.0,
        coverage_detail=CoverageDetail(strategy_obligation_coverage=1.0, requirement_item_coverage=0.8),
        review_target_ids=[TC_ID],
    )
    repo.save_review_report(report)
    # 局部导入：TestCaseRevision 以 Test* 命名但 reserved.py 未设 __test__=False，模块级导入会被 pytest 误当测试类收集
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
    return SimpleNamespace(run_id=run.id, tc_id=TC_ID, tc2_id=TC2_ID, item_id=item.id)


def _mock_pipeline(monkeypatch, result: PipelineResult):
    monkeypatch.setattr(v2_service, "run_v2_pipeline", lambda **kw: result)


# ============================================================
# P0-1：health 免登录
# ============================================================


def test_01_health_public_ok(v2_anon):
    rv = v2_anon.get("/api/v2/health")  # 未登录也能访问
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["status"] == "ok" and body["v2_ready"] is True and body["schema_version"] == 10


def test_02_health_degraded_when_not_ready(v2_anon, monkeypatch):
    import web

    monkeypatch.setitem(web.app.config, "V2_READY", False)
    rv = v2_anon.get("/api/v2/health")
    assert rv.status_code == 503
    body = rv.get_json()
    assert body["status"] == "degraded" and body["v2_ready"] is False and body["schema_version"] is None


def test_03_gating_503_when_not_ready(v2_client, monkeypatch):
    import web

    monkeypatch.setitem(web.app.config, "V2_READY", False)
    rv = v2_client.get(f"/api/v2/runs/{FAKE_RUN_ID}")
    assert rv.status_code == 503
    assert rv.get_json()["v2_ready"] is False


# ============================================================
# P0-3：错误分级 401 / 403 / 400
# ============================================================


def test_04_get_run_requires_login(v2_anon):
    assert v2_anon.get(f"/api/v2/runs/{FAKE_RUN_ID}").status_code == 401


def test_05_post_runs_requires_login(v2_anon):
    assert v2_anon.post("/api/v2/runs", json={"title": "t", "text": "x"}).status_code == 401


def test_06_post_runs_csrf_required(v2_client):
    rv = v2_client.post("/api/v2/runs", json={"title": "t", "text": "x"})  # 无 X-CSRF-Token
    assert rv.status_code == 403


def test_07_post_runs_csrf_wrong(v2_client):
    rv = v2_client.post("/api/v2/runs", json={"title": "t", "text": "x"}, headers={"X-CSRF-Token": "bad"})
    assert rv.status_code == 403


def test_08_post_runs_400_missing_fields(v2_client):
    token = _csrf(v2_client)
    rv = v2_client.post("/api/v2/runs", json={"title": "", "text": ""}, headers={"X-CSRF-Token": token})
    assert rv.status_code == 400


# ============================================================
# POST /runs：成功 / Runtime FAILED / 500（P0-3 P0-4）
# ============================================================


def test_09_post_runs_success(v2_client, monkeypatch):
    _mock_pipeline(monkeypatch, PipelineResult(run_id=FAKE_RUN_ID, success=True, test_point_count=3, test_case_count=3))
    token = _csrf(v2_client)
    rv = v2_client.post("/api/v2/runs", json={"title": "T", "text": "需求"}, headers={"X-CSRF-Token": token})
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["run_id"] == FAKE_RUN_ID and body["success"] is True


def test_10_post_runs_runtime_failed_surfaces_fields(v2_client, monkeypatch):
    """P0-3：Runtime FAILED 不塌缩，回传 run_id/failed_step/error_message。"""
    _mock_pipeline(
        monkeypatch,
        PipelineResult(run_id=FAKE_RUN_ID, success=False, failed_step="testcases", error_message="RuntimeError: boom"),
    )
    token = _csrf(v2_client)
    rv = v2_client.post("/api/v2/runs", json={"title": "T", "text": "需求"}, headers={"X-CSRF-Token": token})
    assert rv.status_code == 200  # 请求本身成功；业务失败在 body
    body = rv.get_json()
    assert body["success"] is False
    assert body["run_id"] == FAKE_RUN_ID
    assert body["failed_step"] == "testcases"
    assert "boom" in body["error_message"]


def test_11_post_runs_500_on_exception(v2_client, monkeypatch):
    def _raise(**kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(v2_service, "run_v2_pipeline", _raise)
    token = _csrf(v2_client)
    rv = v2_client.post("/api/v2/runs", json={"title": "T", "text": "需求"}, headers={"X-CSRF-Token": token})
    assert rv.status_code == 500


# ============================================================
# V1 session → V2 user 自动映射（首建 + 复用）
# ============================================================


def test_12_v2_user_auto_provisioned_and_reused(v2_client, monkeypatch):
    _mock_pipeline(monkeypatch, PipelineResult(run_id=FAKE_RUN_ID, success=True))
    v1_uid = v2_client.get("/api/me").get_json()["user"]["id"]
    token = _csrf(v2_client)
    v2_client.post("/api/v2/runs", json={"title": "T", "text": "x"}, headers={"X-CSRF-Token": token})
    v2_client.post("/api/v2/runs", json={"title": "T2", "text": "y"}, headers={"X-CSRF-Token": token})

    row = repo.get_user_by_legacy_id(v1_uid)
    assert row is not None  # 自动开通
    with repo.v2_read_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM users WHERE legacy_int_id = ?", (v1_uid,)).fetchone()[0]
    assert n == 1  # 两次调用复用同一 V2 user，不重复建


# ============================================================
# GET 查询端点（真实播种数据）
# ============================================================


def test_13_get_run_detail(v2_client):
    seed = _seed_v2()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}")
    assert rv.status_code == 200
    assert rv.get_json()["run"]["status"] == "done"


def test_14_get_run_404(v2_client):
    _seed_v2()
    assert v2_client.get(f"/api/v2/runs/{FAKE_RUN_ID}").status_code == 404


def test_15_list_test_points(v2_client):
    seed = _seed_v2()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/test-points")
    assert rv.status_code == 200
    pts = rv.get_json()["test_points"]
    assert len(pts) == 2
    # provenance 过滤
    rv2 = v2_client.get(f"/api/v2/runs/{seed.run_id}/test-points?provenance=strategy")
    assert rv2.get_json()["test_points"] == []


def test_16_list_test_cases_with_status_filter(v2_client):
    seed = _seed_v2()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/test-cases")
    assert len(rv.get_json()["test_cases"]) == 2
    rv2 = v2_client.get(f"/api/v2/runs/{seed.run_id}/test-cases?status=archived")
    cases = rv2.get_json()["test_cases"]
    assert len(cases) == 1 and cases[0]["id"] == TC2_ID


def test_17_get_review(v2_client):
    seed = _seed_v2()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/review")
    assert rv.status_code == 200
    review = rv.get_json()["review"]
    assert review["revision"] == 1
    assert review["scores"]["coverage"] == 80


def test_18_get_optimizer_archived(v2_client):
    seed = _seed_v2()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/optimizer")
    assert rv.status_code == 200
    opt = rv.get_json()["optimizer"]
    assert opt["archived_count"] == 1 and opt["archived_cases"][0]["id"] == TC2_ID


def test_19_get_coverage(v2_client):
    seed = _seed_v2()
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/coverage")
    assert rv.status_code == 200
    cov = rv.get_json()["coverage"]
    assert cov["strategy_obligation_coverage"] == 1.0 and cov["requirement_item_coverage"] == 0.8


def test_20_list_revisions(v2_client):
    seed = _seed_v2()
    rv = v2_client.get(f"/api/v2/test-cases/{seed.tc_id}/revisions")
    assert rv.status_code == 200
    assert len(rv.get_json()["revisions"]) == 1


def test_21_trace(v2_client):
    seed = _seed_v2()
    rv = v2_client.get(f"/api/v2/test-cases/{seed.tc_id}/trace")
    assert rv.status_code == 200
    trace = rv.get_json()["trace"]
    assert trace["case_id"] == seed.tc_id


def test_22_test_case_404(v2_client):
    _seed_v2()
    assert v2_client.get(f"/api/v2/test-cases/{FAKE_RUN_ID}/revisions").status_code == 404


# ============================================================
# 人工编辑（乐观锁 200/409）+ 重评审
# ============================================================


def test_23_edit_test_case_success(v2_client):
    seed = _seed_v2()
    tc = repo.get_test_case(seed.tc_id)
    token = _csrf(v2_client)
    rv = v2_client.post(
        f"/api/v2/test-cases/{seed.tc_id}/edit",
        json={"updates": {"title": "新标题"}, "expected_updated_at": tc.updated_at.isoformat()},
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 200
    assert rv.get_json()["success"] is True


def test_24_edit_conflict_409(v2_client):
    seed = _seed_v2()
    token = _csrf(v2_client)
    rv = v2_client.post(
        f"/api/v2/test-cases/{seed.tc_id}/edit",
        json={"updates": {"title": "新标题"}, "expected_updated_at": "2000-01-01T00:00:00+00:00"},
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 409


def test_25_edit_404(v2_client):
    _seed_v2()
    token = _csrf(v2_client)
    rv = v2_client.post(
        f"/api/v2/test-cases/{FAKE_RUN_ID}/edit",
        json={"updates": {"title": "x"}},
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 404


def test_26_re_review(v2_client, monkeypatch):
    from core.v2.human_editor_orchestrator import ReReviewResult

    seed = _seed_v2()
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


# ============================================================
# V1 零回归
# ============================================================


def test_27_v1_unaffected(v2_client):
    rv = v2_client.get("/api/me")
    assert rv.status_code == 200 and rv.get_json()["logged_in"] is True


# ============================================================
# GET /api/v2/runs 列表（Step 10.3.1，第 13 端点）
# ============================================================


def _session_v2_uid(client) -> str:
    """返回当前 V1 session 用户映射的 V2 user id（无则创建带 legacy_int_id 的映射）。"""
    from core.schemas import new_ulid

    v1_uid = client.get("/api/me").get_json()["user"]["id"]
    row = repo.get_user_by_legacy_id(v1_uid)
    if row is not None:
        return row["id"]
    uid = new_ulid()
    repo.save_user(uid, "v2sess", "", legacy_int_id=v1_uid)
    return uid


def _mk_run(uid, title, *, status=RunStatus.DONE, failed_step=None, created_at=None):
    """在指定 V2 user 下播种一条完整合法 Run（doc/version/cfg/run 均真实入库），返回 run_id。

    created_at 可显式传入（tz-aware datetime），用于确定性验证 created_at DESC 排序。
    """
    doc = RequirementDoc(user_id=uid, title=title, source_type=SourceType.TEXT)
    repo.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    repo.save_version(ver)
    cfg = GenerationConfig(
        model_provider="t", model_name="m", temperature=0.3, prompt_version="p", generator_version="g"
    )
    repo.save_generation_config(cfg)
    run = Run(
        user_id=uid,
        doc_id=doc.id,
        requirement_version_id=ver.id,
        generation_config_id=cfg.id,
        status=status,
        failed_step=failed_step,
    )
    if created_at is not None:
        run.created_at = created_at
    repo.save_run(run)
    return run.id


def test_28_list_runs_empty_for_new_user(v2_client):
    """新登录用户无 V2 user / 无 Run → items:[]（GET 不自动开通，无写副作用）。"""
    rv = v2_client.get("/api/v2/runs")
    assert rv.status_code == 200
    assert rv.get_json() == {"success": True, "items": []}
    # GET 不应自动开通 V2 user
    v1_uid = v2_client.get("/api/me").get_json()["user"]["id"]
    assert repo.get_user_by_legacy_id(v1_uid) is None


def test_29_list_runs_returns_own_with_fields(v2_client):
    from datetime import UTC, datetime

    uid = _session_v2_uid(v2_client)
    rid = _mk_run(uid, "登录流程需求", status=RunStatus.DONE, created_at=datetime(2026, 5, 1, tzinfo=UTC))
    rv = v2_client.get("/api/v2/runs")
    body = rv.get_json()
    assert rv.status_code == 200 and body["success"] is True
    items = body["items"]
    assert len(items) == 1
    it = items[0]
    assert set(it.keys()) == {"run_id", "title", "status", "created_at", "failed_step"}
    assert it["run_id"] == rid
    assert it["title"] == "登录流程需求"  # title 来自 Doc
    assert it["status"] == "done"
    assert it["created_at"]  # 非空
    assert it["failed_step"] is None


def test_30_list_runs_only_own_not_others(v2_client):
    from core.schemas import new_ulid

    uid = _session_v2_uid(v2_client)
    own = _mk_run(uid, "我的需求")
    other_uid = new_ulid()
    repo.save_user(other_uid, "other", "", legacy_int_id=987654)
    other = _mk_run(other_uid, "别人的需求")
    rv = v2_client.get("/api/v2/runs")
    run_ids = [i["run_id"] for i in rv.get_json()["items"]]
    assert own in run_ids
    assert other not in run_ids  # 只返回本人 Run


def test_31_list_runs_desc_order_and_failed_step(v2_client):
    from datetime import UTC, datetime

    uid = _session_v2_uid(v2_client)
    _mk_run(uid, "旧需求", status=RunStatus.DONE, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    failed = _mk_run(
        uid,
        "失败需求",
        status=RunStatus.FAILED,
        failed_step="testcases",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    items = v2_client.get("/api/v2/runs").get_json()["items"]
    assert items[0]["run_id"] == failed  # created_at DESC：最近的排第一
    assert items[0]["status"] == "failed" and items[0]["failed_step"] == "testcases"
    assert len(items) == 2


def test_32_list_runs_title_fallback(v2_client):
    uid = _session_v2_uid(v2_client)
    rid = _mk_run(uid, "")  # 空标题 → 兜底 "(无标题)"
    items = v2_client.get("/api/v2/runs").get_json()["items"]
    it = next(i for i in items if i["run_id"] == rid)
    assert it["title"] == "(无标题)"


def test_33_list_runs_requires_login(v2_anon):
    assert v2_anon.get("/api/v2/runs").status_code == 401


# ============================================================
# GET /v2 页面路由（Step 10.4，决策2：不重造登录，未登录跳回 V1）
# ============================================================


def test_34_v2_page_renders_when_logged_in(v2_client):
    rv = v2_client.get("/v2")
    assert rv.status_code == 200
    html = rv.get_data(as_text=True)
    # Step 11 产品重构：/v2 已升级为 React SPA 壳（构建产物 static/v2/v2_react.*）
    assert "v2_react.js" in html
    assert "v2_react.css" in html
    assert 'id="root"' in html


def test_35_v2_page_redirects_when_anonymous(v2_anon):
    rv = v2_anon.get("/v2")
    assert rv.status_code == 302  # 未登录 → 302
    loc = rv.headers["Location"]
    assert loc == "/" or loc.endswith("/")  # 跳回 V1 登录页
    assert "v2" not in loc


# ============================================================
# 乐观锁时间戳格式回归（前端回传 API 序列化的 ...Z 不得误报 409）
# ============================================================


def test_36_edit_roundtrips_api_updated_at(v2_client):
    """回归：前端把 API 返回的 updated_at（Pydantic JSON 模式 = ...Z）原样回传，必须能保存成功。

    修复前：服务端用 _iso()（...+00:00）与回传的 ...Z 字符串直比 → 必不等 → 每次编辑误报 409。
    """
    seed = _seed_v2()
    cases = v2_client.get(f"/api/v2/runs/{seed.run_id}/test-cases").get_json()["test_cases"]
    api_updated_at = next(c for c in cases if c["id"] == seed.tc_id)["updated_at"]
    token = _csrf(v2_client)
    rv = v2_client.post(
        f"/api/v2/test-cases/{seed.tc_id}/edit",
        json={"updates": {"title": "回传 API 时间戳保存"}, "expected_updated_at": api_updated_at},
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 200, rv.get_json()
    assert rv.get_json()["success"] is True


def test_37_edit_accepts_zulu_format(v2_client):
    """回归：显式用 ...Z 格式（API 输出格式）作为乐观锁基准 → 归一后应保存成功。"""
    seed = _seed_v2()
    tc = repo.get_test_case(seed.tc_id)
    zulu = tc.updated_at.isoformat().replace("+00:00", "Z")  # 模拟前端拿到的 Z 格式
    token = _csrf(v2_client)
    rv = v2_client.post(
        f"/api/v2/test-cases/{seed.tc_id}/edit",
        json={"updates": {"title": "Z 格式保存"}, "expected_updated_at": zulu},
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 200, rv.get_json()


def test_38_edit_stale_zulu_still_409(v2_client):
    """回归：归一不得削弱乐观锁——真正过期的 Z 格式时间戳仍应 409。"""
    seed = _seed_v2()
    token = _csrf(v2_client)
    rv = v2_client.post(
        f"/api/v2/test-cases/{seed.tc_id}/edit",
        json={"updates": {"title": "x"}, "expected_updated_at": "2000-01-01T00:00:00Z"},
        headers={"X-CSRF-Token": token},
    )
    assert rv.status_code == 409


# ============================================================
# GET /api/v2/runs/<id>/requirements（只读，Step 11 UI 重构第 14 端点）
# ============================================================


def test_39_requirements_context_with_items(v2_client):
    """返回 Doc + Version + Item 列表及 fields/permissions 计数（复用播种数据，不改 schema）。"""
    from core.schemas import DataType, FieldSpec, PermissionRule

    seed = _seed_v2()
    run = repo.get_run(seed.run_id)
    repo.save_item(
        RequirementItem(
            version_id=run.requirement_version_id,
            seq=2,
            type=RequirementItemType.DATA_FIELD,
            module="m2",
            statement="手机号",
            fields=[
                FieldSpec(
                    name="phone", label="手机号", data_type=DataType.STRING, required=True, min_length=11, max_length=11
                )
            ],
            permissions=[PermissionRule(role="admin", resource="order", action="refund", allowed=True)],
        )
    )
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/requirements")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["success"] is True
    ctx = body["requirements"]
    assert ctx["run_id"] == seed.run_id
    assert ctx["item_count"] == 2
    assert ctx["field_count"] == 1 and ctx["permission_count"] == 1 and ctx["rule_count"] == 0
    assert ctx["modules"] == ["m", "m2"]
    assert ctx["doc"]["title"] == "T"
    assert ctx["version"]["version_no"] == 1
    assert sorted(i["statement"] for i in ctx["items"]) == ["s", "手机号"]


def test_40_requirements_404_for_missing_run(v2_client):
    rv = v2_client.get(f"/api/v2/runs/{FAKE_RUN_ID}/requirements")
    assert rv.status_code == 404


def test_41_requirements_requires_login(v2_anon):
    assert v2_anon.get(f"/api/v2/runs/{FAKE_RUN_ID}/requirements").status_code == 401


def test_42_requirements_gating_503_when_not_ready(v2_client, monkeypatch):
    """新端点纳入现有 V2_READY gating（before_request 自动覆盖）。"""
    import web

    seed = _seed_v2()
    monkeypatch.setitem(web.app.config, "V2_READY", False)
    rv = v2_client.get(f"/api/v2/runs/{seed.run_id}/requirements")
    assert rv.status_code == 503
    assert rv.get_json()["v2_ready"] is False
