"""Step 10.2 验收测试 - V2 顶层 Runtime（run_v2_pipeline）。

覆盖 4 条 P0 约束：
  P0-1 Runtime 创建唯一 Run（底层复用 run_id，不二次创建）
  P0-2 Runtime 是唯一 Run.status 控制者（底层 skip_run_status_update=True）
  P0-3 失败原子记录 FAILED + failed_step + error_message 并停止后续
  P0-4 每阶段记录 started_at/finished_at/duration_ms/artifact_ids
外加：状态序列、stop_after、counts 聚合、schema 9→10 迁移、generate_test_points run_id 复用 vs 自建。

策略：用 monkeypatch 注入 6 个底层步骤的 fake（隔离 Runtime 编排逻辑，免真实 LLM JSON），
并对 repo.save_run 加 spy 捕获 Runtime 写入的完整 Run.status 序列。
"""

from types import SimpleNamespace

import pytest

from core.schemas import (
    GenerationConfig,
    Provenance,
    RequirementDoc,
    RequirementItem,
    RequirementItemType,
    RequirementVersion,
    Run,
    RunStatus,
    SourceType,
)
from core.v2 import repository as repo
from core.v2.db import v2_conn, v2_read_conn
from core.v2.ddl import create_v2_schema, get_schema_version
from core.v2.runtime import run_v2_pipeline
from core.v2.tp_orchestrator import generate_test_points

USER_ID = "01ARZ3NDEKTSV4RRFFQ69G5RT1"


class _EmptyTPClient:
    """真实 generate_test_points 用：返回空测试点，避免依赖复杂 LLM JSON。"""

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None):
        return '{"test_points": []}'


class _Recorder:
    def __init__(self):
        self.calls: list[str] = []
        self.run_ids: dict[str, str | None] = {}
        self.skip_flags: dict[str, bool] = {}
        self.statuses: list[RunStatus] = []


@pytest.fixture
def rt_env(v2_db):
    """临时 V2 库（v2_db fixture 已建表）+ 一个用户（Run/Doc 外键依赖）。"""
    v2_db.save_user(USER_ID, "alice", "hash", legacy_int_id=1)
    return SimpleNamespace(user_id=USER_ID, repo=v2_db)


def _count_runs() -> int:
    with v2_read_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def _seed_ir(user_id: str, n_items: int = 1):
    """在临时库创建 Doc + Version + n 个 Item（供真实 generate_test_points 用）。"""
    doc = RequirementDoc(user_id=user_id, title="T", source_type=SourceType.TEXT)
    repo.save_doc(doc)
    ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="需求", provenance=Provenance.LLM)
    repo.save_version(ver)
    for i in range(n_items):
        repo.save_item(
            RequirementItem(
                version_id=ver.id, seq=i + 1, type=RequirementItemType.FUNCTION, module="m", statement=f"s{i}"
            )
        )
    doc.latest_version_id = ver.id
    repo.save_doc(doc)
    return SimpleNamespace(doc_id=doc.id, version_id=ver.id)


def _install_fakes(monkeypatch, rec: _Recorder, *, fail_at: str | None = None, ir_items: int = 1):
    """把 runtime 依赖的 6 个底层步骤替换为记录型 fake；spy repo.save_run 捕获状态序列。"""
    import core.v2.runtime as rt

    orig_save_run = repo.save_run

    def spy_run(run):
        rec.statuses.append(run.status)
        return orig_save_run(run)

    monkeypatch.setattr(repo, "save_run", spy_run)

    def fake_ir(client, *, user_id, title, text=None, paths=None, source_type=None):
        rec.calls.append("ir")
        if fail_at == "ir":
            raise RuntimeError("ir boom")
        doc = RequirementDoc(user_id=user_id, title=title, source_type=source_type or SourceType.TEXT)
        repo.save_doc(doc)
        ver = RequirementVersion(doc_id=doc.id, version_no=1, raw_text=text or "", provenance=Provenance.LLM)
        repo.save_version(ver)
        items = []
        for i in range(ir_items):
            it = RequirementItem(
                version_id=ver.id, seq=i + 1, type=RequirementItemType.FUNCTION, module="m", statement=f"s{i}"
            )
            repo.save_item(it)
            items.append(it)
        doc.latest_version_id = ver.id
        repo.save_doc(doc)
        return SimpleNamespace(doc=doc, version=ver, items=items)

    def fake_tp(client, *, version_id, user_id=None, run_id=None, skip_run_status_update=False, **kw):
        rec.calls.append("testpoints")
        rec.run_ids["testpoints"] = run_id
        rec.skip_flags["testpoints"] = skip_run_status_update
        if fail_at == "testpoints":
            raise RuntimeError("tp boom")
        return SimpleNamespace(
            phase_a=SimpleNamespace(calls=2), phase_b=SimpleNamespace(calls=1), points=["a", "b", "c"], run_id=run_id
        )

    def fake_strategy(run_id, version_id, skip_run_status_update=False, **kw):
        rec.calls.append("strategy")
        rec.run_ids["strategy"] = run_id
        rec.skip_flags["strategy"] = skip_run_status_update
        if fail_at == "strategy":
            raise RuntimeError("st boom")
        return SimpleNamespace(points=["s1", "s2"], obligations=["o1"])

    def fake_tc(client, *, run_id, version_id=None, skip_run_status_update=False, **kw):
        rec.calls.append("testcases")
        rec.run_ids["testcases"] = run_id
        rec.skip_flags["testcases"] = skip_run_status_update
        if fail_at == "testcases":
            raise RuntimeError("tc boom")
        return SimpleNamespace(llm_calls=4, cases=["c1", "c2", "c3"], validated_count=2, failed_count=1)

    def fake_review(client, *, run_id, revision=None, trigger_type=None, skip_run_status_update=False, **kw):
        rec.calls.append("review")
        rec.run_ids["review"] = run_id
        rec.skip_flags["review"] = skip_run_status_update
        if fail_at == "review":
            raise RuntimeError("rv boom")
        return SimpleNamespace(reviewed_count=3, report=None)

    def fake_optimize(client, *, run_id, skip_run_status_update=False, **kw):
        rec.calls.append("optimizer")
        rec.run_ids["optimizer"] = run_id
        rec.skip_flags["optimizer"] = skip_run_status_update
        if fail_at == "optimizer":
            raise RuntimeError("op boom")
        return SimpleNamespace(optimizer_result=SimpleNamespace(archived_cases=1))

    monkeypatch.setattr(rt, "ingest_and_build_ir", fake_ir)
    monkeypatch.setattr(rt, "generate_test_points", fake_tp)
    monkeypatch.setattr(rt, "apply_strategy_engine", fake_strategy)
    monkeypatch.setattr(rt, "synthesize_test_cases", fake_tc)
    monkeypatch.setattr(rt, "review_test_cases", fake_review)
    monkeypatch.setattr(rt, "optimize_duplicates", fake_optimize)


# ============================================================
# P0-1 / P0-2：单一 Run + 唯一状态控制者 + 完整状态序列
# ============================================================


def test_01_full_pipeline_single_run_and_status_sequence(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="需求", client=object())

    assert res.success is True
    assert res.run_id is not None
    assert _count_runs() == 1  # P0-1：全链只有 1 个 Run
    # P0-2：Run.status 序列全部且仅由 Runtime 写
    assert rec.statuses == [
        RunStatus.PARSING,
        RunStatus.GENERATING,
        RunStatus.STRATEGIZING,
        RunStatus.GENERATING,
        RunStatus.REVIEWING,
        RunStatus.OPTIMIZING,
        RunStatus.DONE,
    ]
    assert [s.step for s in res.steps] == ["ir", "testpoints", "strategy", "testcases", "review", "optimizer"]
    assert all(s.success for s in res.steps)
    assert rt_env.repo.get_run(res.run_id).status == RunStatus.DONE


def test_02_runtime_sole_creator_and_all_skip(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object())

    # P0-1：5 个底层 orchestrator 收到同一个 run_id（Runtime 的唯一 Run）
    orchestrator_steps = ("testpoints", "strategy", "testcases", "review", "optimizer")
    assert {rec.run_ids[k] for k in orchestrator_steps} == {res.run_id}
    # P0-2：全部以 skip_run_status_update=True 调用
    assert all(rec.skip_flags[k] is True for k in orchestrator_steps)


# ============================================================
# P0-3：失败原子记录 + 停止后续
# ============================================================


def test_03_failure_marks_run_failed_and_stops(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec, fail_at="testcases")
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object())

    assert res.success is False
    assert res.failed_step == "testcases"
    assert res.error_message and "tc boom" in res.error_message
    run = rt_env.repo.get_run(res.run_id)
    assert run.status == RunStatus.FAILED
    assert run.failed_step == "testcases"
    assert run.error_message and "tc boom" in run.error_message
    # 后续步骤未执行
    assert rec.calls == ["ir", "testpoints", "strategy", "testcases"]
    assert rec.statuses[-1] == RunStatus.FAILED


def test_04_ir_failure_no_run(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec, fail_at="ir")
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object())

    assert res.success is False
    assert res.failed_step == "ir"
    assert res.run_id is None  # ir 阶段 Run 未出生
    assert _count_runs() == 0


def test_05_ir_no_items_fails(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec, ir_items=0)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object())

    assert res.success is False
    assert res.failed_step == "ir"
    assert res.run_id is None
    assert _count_runs() == 0


def test_06_failure_at_each_step_pinpoints(rt_env, monkeypatch):
    """逐个步骤注入失败，failed_step 必须精确对应。"""
    for step in ("testpoints", "strategy", "testcases", "review", "optimizer"):
        rec = _Recorder()
        _install_fakes(monkeypatch, rec, fail_at=step)
        res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object())
        assert res.success is False and res.failed_step == step
        assert rt_env.repo.get_run(res.run_id).status == RunStatus.FAILED


# ============================================================
# P0-4：每阶段 metrics
# ============================================================


def test_07_step_metrics_recorded(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object())

    for s in res.steps:
        assert s.started_at is not None and s.finished_at is not None
        assert s.duration_ms >= 0
        assert isinstance(s.artifact_ids, dict)
    ir_step = next(s for s in res.steps if s.step == "ir")
    assert "doc_id" in ir_step.artifact_ids and "version_id" in ir_step.artifact_ids
    assert res.doc_id and res.version_id
    assert res.total_duration_ms >= 0


# ============================================================
# stop_after
# ============================================================


def test_08_stop_after_ir(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object(), stop_after="ir")

    assert res.success is True
    assert res.run_id is None  # IR 阶段 Run 未出生
    assert [s.step for s in res.steps] == ["ir"]
    assert rec.calls == ["ir"]
    assert rec.statuses == []  # 未创建 Run，无状态写入


def test_09_stop_after_testpoints(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object(), stop_after="testpoints")

    assert res.success is True and res.run_id is not None
    assert rec.calls == ["ir", "testpoints"]
    assert rec.statuses == [RunStatus.PARSING, RunStatus.GENERATING, RunStatus.DONE]
    assert rt_env.repo.get_run(res.run_id).status == RunStatus.DONE


def test_10_stop_after_review(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object(), stop_after="review")

    assert res.success is True
    assert rec.calls == ["ir", "testpoints", "strategy", "testcases", "review"]
    assert "optimizer" not in rec.calls
    assert rt_env.repo.get_run(res.run_id).status == RunStatus.DONE


# ============================================================
# counts 聚合 + llm 调用统计
# ============================================================


def test_11_counts_aggregated_on_done(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec, ir_items=2)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object())

    run = rt_env.repo.get_run(res.run_id)
    assert run.counts.items == 2  # fake ir 建了 2 个 item
    # fake orchestrator 不落真实产物，故 points/cases/obligations = 0（Runtime 从 DB 聚合）
    assert (run.counts.points, run.counts.cases, run.counts.obligations) == (0, 0, 0)


def test_12_total_llm_calls(rt_env, monkeypatch):
    rec = _Recorder()
    _install_fakes(monkeypatch, rec)
    res = run_v2_pipeline(user_id=rt_env.user_id, title="T", text="x", client=object())
    # testpoints fake: phase_a 2 + phase_b 1 = 3；testcases fake: 4 → 合计 7
    assert res.total_llm_calls == 7


# ============================================================
# schema 9 → 10 迁移
# ============================================================


def test_13_schema_9_to_10_migration(rt_env):
    # 模拟 v9 旧库：删掉两列 + schema_meta 置 9（sqlite >= 3.35 支持 DROP COLUMN）
    with v2_conn() as conn:
        conn.execute("ALTER TABLE runs DROP COLUMN failed_step")
        conn.execute("ALTER TABLE runs DROP COLUMN error_message")
        conn.execute("UPDATE schema_meta SET value = '9' WHERE key = 'schema_version'")
    assert get_schema_version() == 9

    create_v2_schema()  # 触发 _migrate_v9_to_v10

    assert get_schema_version() == 10
    with v2_read_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "failed_step" in cols and "error_message" in cols
    # 幂等：再跑一次不报错、仍为 10
    create_v2_schema()
    assert get_schema_version() == 10


# ============================================================
# generate_test_points：run_id 复用 vs 自建（P0-1 + 向后兼容）
# ============================================================


def test_14_generate_test_points_reuses_run_id(rt_env):
    """run_id 给定 + skip=True：复用 Run、不新建、不改状态。"""
    ir = _seed_ir(rt_env.user_id, n_items=1)
    cfg = GenerationConfig(
        model_provider="t", model_name="m", temperature=0.3, prompt_version="p", generator_version="g"
    )
    repo.save_generation_config(cfg)
    run = Run(
        user_id=rt_env.user_id,
        doc_id=ir.doc_id,
        requirement_version_id=ir.version_id,
        generation_config_id=cfg.id,
        status=RunStatus.GENERATING,
    )
    repo.save_run(run)
    assert _count_runs() == 1

    res = generate_test_points(
        _EmptyTPClient(), version_id=ir.version_id, user_id=rt_env.user_id, run_id=run.id, skip_run_status_update=True
    )
    assert _count_runs() == 1  # P0-1：未创建第二个 Run
    assert res.run_id == run.id
    assert repo.get_run(run.id).status == RunStatus.GENERATING  # P0-2：skip → 状态未变


def test_15_generate_test_points_standalone_creates_run(rt_env):
    """run_id=None（默认）：保持原独立调用行为——自建 Run + 自走状态流转到 DONE。"""
    ir = _seed_ir(rt_env.user_id, n_items=1)
    assert _count_runs() == 0

    res = generate_test_points(_EmptyTPClient(), version_id=ir.version_id, user_id=rt_env.user_id)
    assert _count_runs() == 1  # 自建了 Run
    assert res.run_id
    assert repo.get_run(res.run_id).status == RunStatus.DONE  # 原状态流转未被 skip
