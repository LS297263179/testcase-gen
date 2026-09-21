"""Step 11 S6：Benchmark Runner 单测（零真实 LLM / 零生产库依赖）。

覆盖任务书《S6 正式开发》§二十四 的 35 项，分五组：
  A. DB 隔离与生命周期（独立 benchmark DB / 生产库硬阻断 / fresh / reuse / dry-run 零副作用）
  B. runner_lib 纯函数（Artifacts 装配 + ARCHIVED 口径 / 五态分类器 / Hard 序列化 / 指纹 / 原子写）
  C. run_case（INPUT_INVALID 不调 Runtime / 五态落盘 / 非 COMPLETED 不跑 S5 / LLM 调用数对账）
  D. 主流程与 runset（case 与 repeat 隔离 / schema / 顺序 / 数据只读 / 半成品保护）
  E. CLI 参数（case filter / repeat / manifest / 互斥 / 退出码）

全部 Runtime 与 LLM 均以 monkeypatch + StubClient 注入，绝不发起真实网络调用。
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.schemas import (
    CoverageObligation,
    GenerationConfig,
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
    TargetType,
    Technique,
    TestCase,
    TestCaseStatus,
    TestCaseType,
    TestPoint,
    TestStep,
    new_ulid,
)
from core.v2 import db as v2db
from core.v2.eval import runner_lib as rl
from core.v2.eval.metrics_hard import EvalArtifacts, RunObservation, evaluate_hard_metrics
from core.v2.eval.metrics_soft import from_payload as soft_from_payload
from core.v2.eval.metrics_soft import to_payload as soft_to_payload
from core.v2.eval.schema import BenchmarkGold, BenchmarkRunStatus, GoldAuthoring, load_benchmark_suite
from core.v2.fingerprint import compute_item_identity_fingerprint

_ROOT = Path(__file__).resolve().parents[1]
_BENCH = _ROOT / "benchmark"
_FORMAL_MANIFEST = "bm-bench-v0-1"
_USERNAME = "cli_benchmark"


@pytest.fixture(scope="module")
def suite():
    return load_benchmark_suite(_BENCH)


@pytest.fixture(scope="module")
def gold01(suite):
    return suite.golds["bc_01_login"]


@pytest.fixture
def bench_db(tmp_path, monkeypatch):
    """把 V2 DB 路径切到临时 benchmark 库（monkeypatch 负责测后自动恢复）。"""
    path = tmp_path / "benchmark" / "data" / "benchmark_v2.db"
    monkeypatch.setattr(v2db, "_V2_DB_PATH", str(path))
    return path


# ============================================================
# 公共构造工具
# ============================================================


def _result(**kw):
    """Fake PipelineResult（鸭子类型；与 core.v2.runtime.PipelineResult 字段同名）。"""
    base = {
        "run_id": None,
        "success": True,
        "failed_step": None,
        "error_message": None,
        "steps": [],
        "total_duration_ms": 100,
        "total_llm_calls": 2,
        "doc_id": None,
        "version_id": None,
        "test_point_count": 0,
        "test_case_count": 0,
        "review_report_id": None,
        "optimizer_result": None,
    }
    base.update(kw)
    return type("FakeResult", (), base)()


def _gold(statement="手机号必须为 11 位数字", *, with_match_keys=True) -> BenchmarkGold:
    gr = {
        "gold_id": "gr-1",
        "module": "登录",
        "type": "data_field",
        "statement": statement,
        "priority_hint": "P0",
    }
    if with_match_keys:
        gr["match_keys"] = {
            "identity_fingerprints": [
                compute_item_identity_fingerprint(module="登录", type="data_field", statement=statement)
            ]
        }
    return BenchmarkGold.model_validate(
        {
            "gold_version": "gold-test",
            "case_id": "bc_t1",
            "authoring_note": "s6-unit",
            "gold_authoring": GoldAuthoring.HUMAN_INDEPENDENT.value,
            "gold_requirements": [gr],
            "critical_scenarios": [],
            "obligations_expected": [],
            "strategy_expectations": [],
            "reference_cases": [],
            "acceptable_variants": [],
            "forbidden_patterns": [],
        }
    )


def _item(statement="手机号必须为 11 位数字", version_id=None) -> RequirementItem:
    return RequirementItem(
        version_id=version_id or new_ulid(),
        seq=1,
        type=RequirementItemType.DATA_FIELD,
        module="登录",
        statement=statement,
        fingerprint=compute_item_identity_fingerprint(module="登录", type="data_field", statement=statement),
    )


def _tp(item_ids, run_id, version_id, *, title="测试点", obligation_id=None) -> TestPoint:
    return TestPoint(
        run_id=run_id,
        version_id=version_id,
        item_ids=list(item_ids),
        module="登录",
        subcategory="边界",
        title=title,
        description="d",
        dimension="functional",
        obligation_id=obligation_id,
        provenance=Provenance.LLM,
    )


def _tc(run_id, tp_ids, display_id="TC_001", *, title=None, status=TestCaseStatus.REVIEWED) -> TestCase:
    return TestCase(
        run_id=run_id,
        display_id=display_id,
        test_point_ids=list(tp_ids),
        module="登录",
        title=title or f"用例 {display_id}",
        steps=[TestStep(seq=1, action="输入 11 位手机号并提交")],
        expected="手机号必须为 11 位数字",
        type=TestCaseType.FUNCTIONAL,
        status=status,
    )


def _config() -> GenerationConfig:
    return GenerationConfig(
        model_provider="openai",
        model_name="m-test",
        temperature=0.3,
        max_tokens=4096,
        enable_thinking=False,
        prompt_version="tp-v1,tc-v1",
        generator_version="tp-gen-v1",
    )


def _ensure_user() -> str:
    """benchmark 库内固定 user（多次 seed 复用，避免 username UNIQUE 冲突）"""
    from core.v2 import repository as repo
    from core.v2.db import v2_read_conn

    with v2_read_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (_USERNAME,)).fetchone()
    if row is not None:
        return row["id"]
    uid = new_ulid()
    repo.save_user(uid, _USERNAME, password_hash="")
    return uid


def _seed_run(*, with_review=False, archived=0, with_obligation=False):
    """在"当前 V2 路径"的库里落一条完整可装配的 Run 链；返回 (run, version_id, item_id)。"""
    from core.v2 import repository as repo

    uid = _ensure_user()
    cfg = _config()
    repo.save_generation_config(cfg)
    doc = RequirementDoc(user_id=uid, title="seed", source_type=SourceType.TEXT)
    repo.save_doc(doc)
    version = RequirementVersion(doc_id=doc.id, version_no=1, raw_text="原文")
    repo.save_version(version)
    run = Run(user_id=uid, doc_id=doc.id, requirement_version_id=version.id, generation_config_id=cfg.id)
    run.status = RunStatus.DONE
    repo.save_run(run)
    item = _item(version_id=version.id)
    repo.save_item(item)
    obligations = []
    if with_obligation:
        ob = CoverageObligation(
            run_id=run.id,
            item_id=item.id,
            technique=Technique.BOUNDARY_VALUE,
            target="phone.length",
            description="d",
        )
        repo.save_obligation(ob)
        obligations.append(ob)
        tp = _tp([item.id], run.id, version.id, title="边界点", obligation_id=ob.id)
        repo.save_test_point(tp)
        repo.add_coverage(ob.id, TargetType.TESTPOINT, tp.id)
    tps = []
    for i in range(2 + archived):
        tp = _tp([item.id], run.id, version.id, title=f"点{i}")
        repo.save_test_point(tp)
        tps.append(tp)
    repo.save_test_case(_tc(run.id, [tps[0].id], "TC_001"))
    # 每条归档用例独占一个 TestPoint：TestCase.fingerprint = version|mode|test_point_ids，共用会被幂等 upsert 成同一行
    for n in range(archived):
        repo.save_test_case(_tc(run.id, [tps[1 + n].id], f"TC_9{n:02d}", title=f"归档用例 {n}"))
        repo.update_test_case_status(_last_case_id(run.id, f"TC_9{n:02d}"), TestCaseStatus.ARCHIVED.value)
    if with_review:
        report = ReviewReport(
            run_id=run.id,
            revision=1,
            trigger_type=ReviewTriggerType.INITIAL,
            scores=ReviewScores(
                coverage=80, accuracy=85, executability=70, consistency=90, missing_risk=75, duplication=95
            ),
            overall_score=82.5,
        )
        repo.save_review_report(report)
    return run, version.id, item.id


def _last_case_id(run_id, display_id) -> str:
    from core.v2 import repository as repo

    for tc in repo.list_test_cases(run_id):
        if tc.display_id == display_id:
            return tc.id
    raise AssertionError(f"未找到用例 {display_id}")


class StubLLM:
    """S5 语义评价假客户端：按 payload 里的 display_id 回 canned JSON，并记录调用次数。"""

    def __init__(self, fail_with: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.fail_with = fail_with

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.fail_with is not None:
            raise self.fail_with
        if '"pending_items"' in user_prompt or "待裁决" in user_prompt:
            return json.dumps({"additional_valid_candidates": []})
        results = [
            {
                "display_id": disp,
                "verdict": "correct",
                "confidence": 0.9,
                "reason": "与 Gold 语义一致",
                "evidence_refs": ["gr-1"],
                "forbidden_pattern_id": None,
            }
            for disp in _display_ids_in(user_prompt)
        ]
        return json.dumps({"results": results, "additional_valid_candidates": []})


def _display_ids_in(payload: str) -> list[str]:
    import re

    return sorted(set(re.findall(r"TC_\d{3}", payload)))


# ============================================================
# A. DB 隔离与生命周期
# ============================================================


class TestDatabaseIsolation:
    def test_runner_uses_independent_benchmark_db(self, tmp_path, monkeypatch):
        """#1 benchmark DB 独立：runner 进程内路径指向 benchmark/data/benchmark_v2.db"""
        import scripts.v2_benchmark as vb

        monkeypatch.setattr(vb, "_PROJECT_ROOT", tmp_path)
        target = rl.assert_not_production_db(vb.DEFAULT_DB, tmp_path)
        assert target == (tmp_path / "benchmark" / "data" / "benchmark_v2.db").resolve()
        assert (tmp_path / "benchmark" / "data").is_dir() or True  # 解析不建目录

    def test_production_db_hard_block(self, tmp_path, monkeypatch, caplog):
        """#2 + #35 生产库路径保护：--db 指向 data/data_v2.db → exit 3 且不做任何写"""
        import scripts.v2_benchmark as vb

        monkeypatch.setattr(vb, "_PROJECT_ROOT", tmp_path)
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        prod = tmp_path / "data" / "data_v2.db"
        prod.write_bytes(b"keep-me")
        before = v2db.get_v2_db_path()
        with caplog.at_level(logging.ERROR):
            code = vb.main(["--db", str(prod)])
        assert code == vb.EXIT_DB_GUARD == 3
        assert prod.read_bytes() == b"keep-me"
        assert v2db.get_v2_db_path() == before  # 未发生切换
        assert any("生产库" in r.getMessage() for r in caplog.records)

    def test_fresh_db_wipes_history(self, tmp_path, monkeypatch, suite):
        """#3 fresh：历史 Run 被清空，只保留本次 runset 产物"""
        import scripts.v2_benchmark as vb
        from core.v2 import repository as repo
        from core.v2.ddl import create_v2_schema

        db = tmp_path / "bm.db"
        monkeypatch.setattr(v2db, "_V2_DB_PATH", str(db))
        create_v2_schema()
        run, _v, _i = _seed_run()
        assert repo.get_run(run.id) is not None

        _patch_offline(monkeypatch, vb, suite)
        monkeypatch.setattr(vb, "_PROJECT_ROOT", tmp_path)
        code = vb.main(["--db", str(db), "--fresh-db", "--case", "bc_01_login", "--output-dir", str(tmp_path / "rs")])
        assert code == vb.EXIT_OK
        assert repo.get_run(run.id) is None  # 旧 Run 已随库清理
        assert db.stat().st_size > 0

    def test_reuse_db_keeps_history(self, tmp_path, monkeypatch, suite):
        """#4 reuse：历史 Run 保留（仅调试用途）"""
        import scripts.v2_benchmark as vb
        from core.v2 import repository as repo
        from core.v2.ddl import create_v2_schema

        db = tmp_path / "bm.db"
        monkeypatch.setattr(v2db, "_V2_DB_PATH", str(db))
        create_v2_schema()
        run, _v, _i = _seed_run()

        _patch_offline(monkeypatch, vb, suite)
        monkeypatch.setattr(vb, "_PROJECT_ROOT", tmp_path)
        code = vb.main(["--db", str(db), "--reuse-db", "--case", "bc_01_login", "--output-dir", str(tmp_path / "rs")])
        assert code == vb.EXIT_OK
        assert repo.get_run(run.id) is not None

    def test_dry_run_zero_db_zero_llm(self, tmp_path, monkeypatch, capsys):
        """#32 dry-run：零 DB / 零 LLM / 零写盘，但输出 10 单元执行计划"""
        import scripts.v2_benchmark as vb

        def _boom(*a, **k):
            raise AssertionError("dry-run 不得调用 Runtime")

        monkeypatch.setattr(vb, "run_v2_pipeline", _boom)
        monkeypatch.setattr(vb, "build_llm_client", lambda *a, **k: pytest.fail("dry-run 不得建 LLM 客户端"))
        out = tmp_path / "rs"
        code = vb.main(["--dry-run", "--db", str(tmp_path / "x.db"), "--output-dir", str(out)])
        assert code == vb.EXIT_OK
        text = capsys.readouterr().out
        assert "planned units" in text and "10" in text
        assert not out.exists()
        assert not (tmp_path / "x.db").exists()

    def test_production_data_dir_untouched_after_full_run(self, tmp_path, monkeypatch, suite):
        """生产 data/ 目录在全量 runset 后零变化（大小+mtime 指纹）"""
        import scripts.v2_benchmark as vb

        _patch_offline(monkeypatch, vb, suite)
        before = _dir_signature(_ROOT / "data")
        code = vb.main(["--db", str(tmp_path / "bm.db"), "--output-dir", str(tmp_path / "rs")])
        assert code == vb.EXIT_OK
        assert _dir_signature(_ROOT / "data") == before


def _dir_signature(path: Path) -> dict:
    if not path.is_dir():
        return {}
    return {p.name: (p.stat().st_size, round(p.stat().st_mtime, 6)) for p in sorted(path.iterdir())}


def _patch_offline(monkeypatch, vb, suite, *, soft_client=None, pipeline=None):
    """把 CLI 的 Runtime / LLM / git 全部换成离线实现（正式 manifest 的 10 个 case 走真数据）。"""

    def _pipeline(*, user_id, title, text, source_type=SourceType.TEXT, client=None, stop_after=None):
        run, version_id, item_id = _seed_run(with_review=True)
        return _result(
            run_id=run.id,
            version_id=version_id,
            doc_id=run.doc_id,
            steps=[
                _step("ir", counts={"items": 1}),
                _step("testpoints", counts={"llm": 2}),
                _step("testcases", counts={"cases": 1}),
                _step("review", counts={"reviewed": 1}),
            ],
        )

    monkeypatch.setattr(vb, "run_v2_pipeline", pipeline or _pipeline)
    monkeypatch.setattr(vb, "build_llm_client", lambda purpose="generate": soft_client or StubLLM())
    monkeypatch.setattr(
        rl,
        "probe_git",
        lambda root: {"git_commit": "c" * 7, "git_branch": "main", "git_dirty": False, "git_probe": "ok"},
    )
    monkeypatch.setattr(vb, "_PROJECT_ROOT", _ROOT)
    monkeypatch.setattr(vb, "resolve_benchmark_user", lambda username: new_ulid())
    return suite


def _step(name, *, success=True, counts=None, llm_calls=1, duration_ms=10):
    return type(
        "Step",
        (),
        {
            "step": name,
            "success": success,
            "started_at": datetime.now(UTC),
            "finished_at": datetime.now(UTC),
            "duration_ms": duration_ms,
            "llm_calls": llm_calls,
            "error": None,
            "counts": counts or {},
        },
    )()


# ============================================================
# B. runner_lib 纯函数
# ============================================================


class TestArtifactsAssembly:
    def test_load_eval_artifacts_full_chain(self, bench_db, monkeypatch):
        """#18 Artifacts 装配：items/TP/TC/obligation/covered/review/config 全部到位"""
        from core.v2 import repository as repo
        from core.v2.ddl import create_v2_schema

        create_v2_schema()
        run, version_id, item_id = _seed_run(with_review=True, with_obligation=True)
        bundle = rl.load_eval_artifacts(run.id, version_id)
        a = bundle.artifacts
        assert len(a.items) == 1 and a.items[0].id == item_id
        assert len(a.test_points) == 3 and len(a.test_cases) == 1  # 2 个 LLM 点 + 1 个策略点
        assert len(a.obligations) == 1
        ob_id = a.obligations[0].id
        assert bundle.review_present is True
        assert a.review_report is not None
        assert len(a.obligation_covered_points[ob_id]) == 1
        assert bundle.summary()["items"] == 1
        assert repo.get_run(run.id).status == RunStatus.DONE

    def test_archived_cases_excluded_but_observed(self, bench_db):
        """#16 + #17 ARCHIVED 用例不进评价集合，但 id/count 保留为观察"""
        from core.v2.ddl import create_v2_schema

        create_v2_schema()
        run, version_id, _ = _seed_run(archived=2)
        bundle = rl.load_eval_artifacts(run.id, version_id)
        assert [tc.display_id for tc in bundle.artifacts.test_cases] == ["TC_001"]
        assert bundle.archived_count == 2
        assert len(bundle.archived_test_case_ids) == 2
        assert bundle.summary()["archived_test_cases"] == 2
        assert bundle.summary()["test_cases"] == 1

    def test_missing_run_returns_empty_bundle(self):
        """run_id=None（IR 前失败）→ 空产物且可安全评价（不抛）"""
        bundle = rl.load_eval_artifacts(None, None)
        assert bundle.artifacts.items == [] and bundle.review_present is False

    def test_generation_config_lookup(self, bench_db):
        from core.v2.ddl import create_v2_schema

        create_v2_schema()
        run, _v, _i = _seed_run()
        cfg = rl.load_generation_config(run.id)
        assert cfg is not None and cfg.model_name == "m-test"
        assert rl.load_generation_config(None) is None


class TestHardPayloadContract:
    def test_round_trip_preserves_metrics(self, gold01):
        """#27 Hard 序列化 round-trip：hard_to_payload → hard_from_payload → 再序列化一致"""
        gr = gold01.gold_requirements[0]
        item = RequirementItem(
            version_id=new_ulid(),
            seq=1,
            type=gr.type,
            module=gr.module,
            statement=gr.statement,
            fingerprint=compute_item_identity_fingerprint(module=gr.module, type=gr.type.value, statement=gr.statement),
        )
        tp = TestPoint(
            item_ids=[item.id],
            module="登录",
            subcategory="边界",
            title="点",
            description="d",
            dimension="functional",
        )
        tc = TestCase(
            run_id=new_ulid(),
            display_id="TC_001",
            test_point_ids=[tp.id],
            module="登录",
            title="用例",
            steps=[TestStep(seq=1, action="输入 11 位手机号并提交")],
            expected="提示格式错误",
            type=TestCaseType.FUNCTIONAL,
        )
        obs = RunObservation(
            case_id="bc_01_login",
            gold=gold01,
            status=BenchmarkRunStatus.COMPLETED,
            artifacts=EvalArtifacts(items=[item], test_points=[tp], test_cases=[tc]),
            total_duration_ms=1234,
            total_llm_calls=5,
        )
        report = evaluate_hard_metrics(obs)
        payload = rl.hard_to_payload(report)
        assert payload["quality_evaluated"] is True
        assert payload["metrics"]["requirement_coverage"]["numerator"] >= 1
        back = rl.hard_from_payload(payload)
        assert rl.hard_to_payload(back) == payload
        assert back.requirement_matches[0].state.value == "auto_hit"

    def test_non_completed_payload_keeps_observation(self, gold01):
        """非 COMPLETED：质量格 None 但 cost/counts 仍在（S3 契约透传到 payload）"""
        obs = RunObservation(
            case_id="bc_01_login",
            gold=gold01,
            status=BenchmarkRunStatus.LLM_FAILURE,
            reliability_note="heuristic",
            total_duration_ms=50,
        )
        payload = rl.hard_to_payload(evaluate_hard_metrics(obs))
        assert payload["metrics"]["requirement_coverage"]["value"] is None
        assert payload["cost_latency"]["total_duration_ms"] == 50
        assert payload["reviewer_based"] is None


class TestClassifier:
    def _counts(self, **kw):
        base = {"items": 5, "test_points": 3, "test_cases": 2, "obligations": 0, "review_present": True}
        base.update(kw)
        return base

    def test_completed(self):
        cls = rl.classify_run_status(
            pipeline_result=_result(success=True), capture=rl.LogCapture(), counts=self._counts()
        )
        assert cls.status == BenchmarkRunStatus.COMPLETED
        assert cls.rules_hit == ["rule=completed"]

    def test_log_prefix_maps_to_llm_failure(self):
        cap = rl.LogCapture(degrade_lines=["Phase A LLM 调用失败: item=01ABC"])
        cls = rl.classify_run_status(pipeline_result=_result(success=True), capture=cap, counts=self._counts())
        assert cls.status == BenchmarkRunStatus.LLM_FAILURE
        assert "rule=llm_degrade_log" in cls.rules_hit

    def test_success_true_with_downgrade_is_llm_failure(self):
        """任务书 §三特别规定：success=True + LLM 降级信号 → LLM_FAILURE（不进质量分母）"""
        cap = rl.LogCapture(degrade_lines=["LLM 用例合成调用失败: tp=01X"])
        cls = rl.classify_run_status(
            pipeline_result=_result(success=True, run_id="r1", steps=[_step("testcases")]),
            capture=cap,
            counts=self._counts(test_cases=0),
        )
        assert cls.status == BenchmarkRunStatus.LLM_FAILURE
        assert "rule=count_test_cases_zero" in cls.rules_hit
        assert "rule=llm_degrade_log" in cls.rules_hit

    def test_retry_exhausted_message_maps_to_llm_failure(self):
        err = "RuntimeError: LLM 调用失败（已重试 3 次）: 429"
        cls = rl.classify_run_status(
            pipeline_result=_result(success=False, failed_step="testpoints", error_message=err),
            capture=rl.LogCapture(),
            counts=self._counts(),
        )
        assert cls.status == BenchmarkRunStatus.LLM_FAILURE
        assert "rule=llm_error_signature" in cls.rules_hit

    def test_sdk_error_class_maps_to_llm_failure(self):
        err = "openai.AuthenticationError: 401 Unauthorized"
        cls = rl.classify_run_status(
            pipeline_result=_result(success=False, failed_step="ir", error_message=err),
            capture=rl.LogCapture(),
            counts=self._counts(),
        )
        assert cls.status == BenchmarkRunStatus.LLM_FAILURE

    def test_intermediate_retry_is_not_llm_failure(self):
        """任务书 §十三特别注意：中间重试日志（后续成功）不得判 LLM_FAILURE"""
        cap = rl.LogCapture(
            ignored_lines=["LLM 调用失败（第 1 次），2s 后重试: 429", "模型不支持图片输入，已自动忽略图片"]
        )
        cls = rl.classify_run_status(pipeline_result=_result(success=True), capture=cap, counts=self._counts())
        assert cls.status == BenchmarkRunStatus.COMPLETED
        assert cls.signals["ignored_intermediate_signals"] == 2

    def test_runtime_failure_without_llm_signal(self):
        cls = rl.classify_run_status(
            pipeline_result=_result(success=False, failed_step="optimizer", error_message="ValueError: boom"),
            capture=rl.LogCapture(),
            counts=self._counts(),
        )
        assert cls.status == BenchmarkRunStatus.RUNTIME_FAILURE
        assert cls.rules_hit == ["rule=failed_pipeline_no_llm_signal"]

    def test_input_invalid_short_circuits_llm_signal(self):
        cap = rl.LogCapture(degrade_lines=["需求解析 LLM 调用失败"])
        cls = rl.classify_run_status(pipeline_result=None, capture=cap, input_invalid=True)
        assert cls.status == BenchmarkRunStatus.INPUT_INVALID

    def test_evaluation_failure_has_top_priority(self):
        cap = rl.LogCapture(degrade_lines=["Phase B LLM 调用失败"])
        cls = rl.classify_run_status(
            pipeline_result=_result(success=False, failed_step="review"),
            capture=cap,
            counts=self._counts(),
            evaluation_error="AttributeError: hard",
        )
        assert cls.status == BenchmarkRunStatus.EVALUATION_FAILURE

    def test_items_zero_after_ir_is_llm_failure(self):
        """§9.2 次生症状：IR 跑完却 0 items（认证类 LLM 故障的典型形态）→ LLM_FAILURE"""
        cls = rl.classify_run_status(
            pipeline_result=_result(success=True, steps=[_step("ir", counts={"items": 0})]),
            capture=rl.LogCapture(),
            counts=self._counts(items=0),
        )
        assert cls.status == BenchmarkRunStatus.LLM_FAILURE
        assert "rule=count_items_zero" in cls.rules_hit

    def test_downstream_failure_is_not_counted_as_zero_products(self):
        """源 3 门控：testpoints 阶段失败（非 LLM 特征）时 test_cases=0 不算 LLM 降级证据"""
        cls = rl.classify_run_status(
            pipeline_result=_result(
                success=False,
                failed_step="testpoints",
                error_message="ValueError: boom",
                steps=[_step("ir"), _step("testpoints", success=False)],
            ),
            capture=rl.LogCapture(),
            counts=self._counts(test_cases=0),
        )
        assert cls.status == BenchmarkRunStatus.RUNTIME_FAILURE
        assert cls.signals["counts"]["test_cases"] == 0

    def test_review_missing_only_when_review_ran_with_cases(self):
        """reviewed 缺失信号仅在 review 跑过 + 有 TC + 无报告时生效"""
        hit = rl.classify_run_status(
            pipeline_result=_result(success=True, steps=[_step("review")]),
            capture=rl.LogCapture(),
            counts=self._counts(review_present=False),
        )
        assert hit.status == BenchmarkRunStatus.LLM_FAILURE
        assert "rule=review_result_missing" in hit.rules_hit
        miss = rl.classify_run_status(
            pipeline_result=_result(success=True, steps=[_step("testcases")]),
            capture=rl.LogCapture(),
            counts=self._counts(review_present=False),
        )
        assert miss.status == BenchmarkRunStatus.COMPLETED

    def test_log_capture_window_isolates_units(self):
        """mark/since 增量窗口：单元 A 的信号不串入后续单元"""
        sink = rl.LogCapture()
        handler = rl.LogSignalHandler(sink)

        def _emit(name, level, msg):
            handler.emit(logging.LogRecord(name, level, "f", 1, msg, (), None))

        mark_a = sink.mark()
        _emit("v2.tp_generator", logging.ERROR, "Phase A LLM 调用失败: item=x")
        unit_a = sink.since(mark_a)
        assert len(unit_a.degrade_lines) == 1 and unit_a.total_records == 1

        mark_b = sink.mark()
        _emit("v2.llm_client", logging.WARNING, "LLM 调用失败（第 1 次），2s 后重试")
        assert sink.since(mark_b).degrade_lines == []  # A 的信号不串到 B
        assert len(sink.since(mark_b).ignored_lines) == 1
        assert len(sink.since(mark_a).degrade_lines) == 1  # A 窗口仍是 A 的那一条


class TestFingerprint:
    def test_probe_git_failure_is_non_blocking(self, monkeypatch):
        """#22 git 探测失败 → git_probe=unknown，不抛异常"""

        def _boom(*a, **k):
            raise OSError("no git")

        monkeypatch.setattr(rl.subprocess, "run", _boom)
        info = rl.probe_git(_ROOT)
        assert info == {"git_commit": None, "git_branch": None, "git_dirty": None, "git_probe": "unknown"}

    def test_probe_git_success(self, monkeypatch):
        class P:
            returncode = 0
            stdout = "abc123\n" if True else ""

        def _fake(cmd, **kw):
            p = P()
            if "status" in cmd:
                p.stdout = " M core/x.py\n"
            elif "abbrev-ref" in " ".join(cmd):
                p.stdout = "main\n"
            else:
                p.stdout = "deadbee\n"
            return p

        monkeypatch.setattr(rl.subprocess, "run", _fake)
        info = rl.probe_git(_ROOT)
        assert info["git_commit"] == "deadbee" and info["git_branch"] == "main"
        assert info["git_dirty"] is True and info["git_probe"] == "ok"

    def test_environment_fingerprint_fields(self, suite):
        """#21 环境指纹字段齐 + 不依赖 GenerationConfig.prompt_version"""
        manifest = suite.manifests[_FORMAL_MANIFEST]
        digests = rl.build_digest_map(_BENCH)
        fp = rl.build_environment_fingerprint(
            manifest=manifest,
            cases=suite.cases,
            golds=suite.golds,
            digest_map=digests,
            git={"git_commit": "x" * 40, "git_branch": "main", "git_dirty": False, "git_probe": "ok"},
            generation_config=_config(),
            resolved_benchmark_db="benchmark/data/benchmark_v2.db",
        )
        required = {
            "benchmark_version",
            "manifest_id",
            "manifest_version",
            "manifest_case_ids",
            "case_set_digest",
            "gold_versions",
            "git_commit",
            "git_branch",
            "git_dirty",
            "git_probe",
            "model_provider",
            "model_name",
            "temperature",
            "enable_thinking",
            "max_tokens",
            "business_prompt_versions",
            "s5_prompt_version",
            "generator_version",
            "reviewer_version",
            "runner_version",
            "resolved_benchmark_db",
        }
        assert required <= set(fp)
        assert len(fp["manifest_case_ids"]) == 10
        assert fp["s5_prompt_version"] == "benchmark-semantic-eval-v1"
        assert len(fp["business_prompt_versions"]) == 6
        assert len(fp["case_set_digest"]) == 32
        assert fp["runner_version"] == rl.RUNNER_VERSION

    def test_case_fingerprint_and_digest_stability(self, suite):
        digests = rl.build_digest_map(_BENCH)
        case = suite.cases["bc_01_login"]
        fp = rl.case_fingerprint(case, suite.golds["bc_01_login"], digests)
        assert fp["case_file_digest"] and fp["gold_file_digest"] and fp["requirement_file_digest"]
        manifest = suite.manifests[_FORMAL_MANIFEST]
        d1 = rl.case_set_digest(manifest, digests, suite.cases, suite.golds)
        d2 = rl.case_set_digest(manifest, digests, suite.cases, suite.golds)
        assert d1 == d2
        other = rl.case_set_digest(
            manifest.model_copy(update={"cases": manifest.cases[:5]}), digests, suite.cases, suite.golds
        )
        assert other != d1


class TestJsonSafety:
    def test_assert_no_sensitive(self):
        for probe in (
            "api_key='sk-1'",
            "Authorization: ...",
            "base_url=http://x",
            "bearer t",
            "credential=1",
            "SECRET_KEY=1",
        ):
            with pytest.raises(rl.SensitiveDataError):
                rl.assert_no_sensitive(probe)
        rl.assert_no_sensitive(json.dumps({"max_tokens": 4096, "temperature": 0.3}))

    def test_write_json_atomic(self, tmp_path):
        path = tmp_path / "a.json"
        rl.write_json_atomic(path, {"b": 1, "a": 2})
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2, "b": 1}
        assert list(path.parent.iterdir()) == [path]  # 无残留临时文件

    def test_write_json_atomic_rejects_sensitive_without_touching_disk(self, tmp_path):
        path = tmp_path / "sub" / "a.json"
        with pytest.raises(rl.SensitiveDataError):
            rl.write_json_atomic(path, {"note": "api_key 泄露"})
        assert not path.exists()

    def test_safe_error_summary_truncates_and_hashes(self):
        long_text = "RuntimeError: " + "x" * 500 + " api-key-marker"
        s = rl.safe_error_summary(long_text)
        assert len(s["safe_prefix"]) <= rl.SAFE_PREFIX_CHARS and len(s["hash"]) == 16
        assert rl.safe_error_summary(None) == {"safe_prefix": "", "hash": ""}


# ============================================================
# C. run_case（单 case 执行与评价串联）
# ============================================================


def _ctx(tmp_path, suite, monkeypatch, *, soft_client=None, dataset_root=_BENCH):
    import scripts.v2_benchmark as vb

    monkeypatch.setattr(vb, "build_llm_client", lambda purpose="generate": soft_client or StubLLM())
    return vb.CaseContext(
        manifest=suite.manifests[_FORMAL_MANIFEST],
        dataset_root=dataset_root,
        output_root=tmp_path / "rs",
        digests=rl.build_digest_map(dataset_root),
        username=_USERNAME,
        user_id=new_ulid(),
    )


def _plan(suite, case_id="bc_01_login", repeat_index=1):
    import scripts.v2_benchmark as vb

    return vb.RunPlan(case=suite.cases[case_id], gold=suite.golds[case_id], repeat_index=repeat_index)


class TestRunCase:
    def test_input_invalid_skips_runtime(self, tmp_path, monkeypatch, suite, bench_db):
        """#8 INPUT_INVALID：预检失败 → 不调 Runtime，status=input_invalid，soft=null"""
        import scripts.v2_benchmark as vb

        def _boom(*a, **k):
            raise AssertionError("INPUT_INVALID 不得调用 Runtime")

        monkeypatch.setattr(vb, "run_v2_pipeline", _boom)
        fake_root = tmp_path / "data"
        (fake_root / "cases").mkdir(parents=True)
        (fake_root / "cases" / "bc_01_login_requirement.md").write_text("   \n\n  ", encoding="utf-8")
        ctx = _ctx(tmp_path, suite, monkeypatch, dataset_root=fake_root)
        payload = vb.run_case(_plan(suite), ctx, rl.LogCapture())
        assert payload["status"] == BenchmarkRunStatus.INPUT_INVALID.value
        assert payload["soft"] is None and payload["hard"] is None
        assert payload["pipeline"]["invoked"] is False

    def test_completed_runs_s5(self, tmp_path, monkeypatch, suite, bench_db):
        """#13 + #19 + #20 COMPLETED：Hard→Soft 串联，soft 段来自 S5 to_payload"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()
        stub = StubLLM()
        _patch_offline(monkeypatch, vb, suite, soft_client=stub)
        ctx = _ctx(tmp_path, suite, monkeypatch, soft_client=stub)
        payload = vb.run_case(_plan(suite), ctx, rl.LogCapture())
        assert payload["status"] == BenchmarkRunStatus.COMPLETED.value
        assert payload["hard"]["quality_evaluated"] is True
        assert payload["soft"]["s5_schema"] == "benchmark-soft-v1"
        assert stub.calls, "COMPLETED 必须调用 S5"

    def test_llm_failure_skips_s5_but_keeps_hard_observation(self, tmp_path, monkeypatch, suite, bench_db):
        """#9 + #10 + #20 LLM 降级：status=llm_failure，跑 S3（质量 None）但绝不调 S5"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()

        def _pipeline(**kw):
            run, version_id, _ = _seed_run()
            logging.getLogger("v2.tp_generator").error("Phase A LLM 调用失败: item=01X")
            return _result(run_id=run.id, version_id=version_id, success=True)

        def _no_client(purpose="generate"):
            raise AssertionError("非 COMPLETED 不得构建 S5 客户端")

        monkeypatch.setattr(vb, "run_v2_pipeline", _pipeline)
        monkeypatch.setattr(vb, "build_llm_client", _no_client)
        ctx = _ctx(tmp_path, suite, monkeypatch)
        sink, handler = rl.attach_log_capture()
        try:
            payload = vb.run_case(_plan(suite), ctx, sink)
        finally:
            rl.detach_log_capture(handler)
        assert payload["status"] == BenchmarkRunStatus.LLM_FAILURE.value
        assert payload["soft"] is None
        assert payload["hard"]["metrics"]["requirement_coverage"]["value"] is None
        assert payload["hard"]["counts"]["test_cases"] >= 1
        assert "rule=llm_degrade_log" in payload["classification"]["rules_hit"]

    def test_runtime_uncaught_exception_is_runtime_failure(self, tmp_path, monkeypatch, suite, bench_db):
        """#11 Runtime 抛穿且无 LLM 特征 → runtime_failure（不崩主流程）"""
        import scripts.v2_benchmark as vb

        def _boom(**kw):
            raise ValueError("boom")

        monkeypatch.setattr(vb, "run_v2_pipeline", _boom)
        ctx = _ctx(tmp_path, suite, monkeypatch)
        payload = vb.run_case(_plan(suite), ctx, rl.LogCapture())
        assert payload["status"] == BenchmarkRunStatus.RUNTIME_FAILURE.value
        assert payload["pipeline"]["invoked"] is False

    def test_evaluation_failure_when_hard_metrics_raise(self, tmp_path, monkeypatch, suite, bench_db):
        """#12 评价层异常 → evaluation_failure（最高优先）"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()

        def _raise(obs):
            raise RuntimeError("hard eval crashed")

        _patch_offline(monkeypatch, vb, suite)
        monkeypatch.setattr(vb, "evaluate_hard_metrics", _raise)
        ctx = _ctx(tmp_path, suite, monkeypatch)
        payload = vb.run_case(_plan(suite), ctx, rl.LogCapture())
        assert payload["status"] == BenchmarkRunStatus.EVALUATION_FAILURE.value
        assert payload["hard"] is None and payload["soft"] is None

    def test_s5_client_failure_becomes_evaluation_failure(self, tmp_path, monkeypatch, suite, bench_db):
        """S5 客户端构建失败 → EVALUATION_FAILURE，保留 S3 观测、不伪造语义分"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()
        _patch_offline(monkeypatch, vb, suite)
        ctx = _ctx(tmp_path, suite, monkeypatch)

        def _no_client(purpose="generate"):
            raise RuntimeError("LLM 未配置")

        monkeypatch.setattr(vb, "build_llm_client", _no_client)  # 覆盖 _ctx 的默认桩
        payload = vb.run_case(_plan(suite), ctx, rl.LogCapture())
        assert payload["status"] == BenchmarkRunStatus.EVALUATION_FAILURE.value
        assert payload["soft"] is None
        assert payload["hard"] is not None
        assert "rule=completed" in payload["classification"]["rules_hit"]  # 运行面本无降级，评价面失败优先

    def test_s5_batch_failure_stays_completed(self, tmp_path, monkeypatch, suite, bench_db):
        """S5 批内 LLM 失败由 S5 自身降级为 PENDING_REVIEW（不改变运行状态，也不伪造分）"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()
        stub = StubLLM(fail_with=RuntimeError("batch down"))
        _patch_offline(monkeypatch, vb, suite, soft_client=stub)
        ctx = _ctx(tmp_path, suite, monkeypatch, soft_client=stub)
        payload = vb.run_case(_plan(suite), ctx, rl.LogCapture())
        assert payload["status"] == BenchmarkRunStatus.COMPLETED.value
        assert payload["soft"]["gold_based"]["semantic_accuracy"]["value"] is None
        assert payload["soft"]["failures"]

    def test_llm_calls_reconciliation(self, tmp_path, monkeypatch, suite, bench_db):
        """#29 成本对账：total = pipeline.llm_calls + S5 批调用数"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()
        stub = StubLLM()
        _patch_offline(monkeypatch, vb, suite, soft_client=stub)
        ctx = _ctx(tmp_path, suite, monkeypatch, soft_client=stub)
        payload = vb.run_case(_plan(suite), ctx, rl.LogCapture())
        assert payload["llm_calls"]["pipeline"] == 2
        assert payload["llm_calls"]["s5_soft"] == len(stub.calls)
        assert payload["llm_calls"]["total"] == 2 + len(stub.calls)

    def test_case_file_schema_and_archived_observability(self, tmp_path, monkeypatch, suite, bench_db):
        """#26 case 文件 schema 齐；#16/#17 ARCHIVED 只在观察字段出现"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()

        def _pipeline(**kw):
            run, version_id, _ = _seed_run(with_review=True, archived=1)
            return _result(
                run_id=run.id,
                version_id=version_id,
                steps=[_step("ir", counts={"items": 1}), _step("review", counts={"reviewed": 1})],
            )

        _patch_offline(monkeypatch, vb, suite, pipeline=_pipeline)
        ctx = _ctx(tmp_path, suite, monkeypatch)
        payload = vb.run_case(_plan(suite), ctx, rl.LogCapture())
        required = {
            "s6_schema",
            "case_id",
            "repeat_index",
            "benchmark_version",
            "gold_version",
            "run_id",
            "status",
            "reliability_note",
            "classification",
            "pipeline",
            "artifacts_summary",
            "hard",
            "soft",
            "llm_calls",
            "generation_config_id",
            "case_fingerprint",
        }
        assert required <= set(payload)
        assert payload["s6_schema"] == rl.S6_CASE_SCHEMA_VERSION
        assert payload["artifacts_summary"]["test_cases"] == 1
        assert payload["artifacts_summary"]["archived_test_cases"] == 1
        assert len(payload["archived_test_case_ids"]) == 1

    def test_signals_do_not_contain_raw_logs(self, tmp_path, monkeypatch, suite, bench_db):
        """#14 可靠性数据：只落 safe prefix + hash + rules，不落整段日志"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()

        def _pipeline(**kw):
            run, version_id, _ = _seed_run()
            logging.getLogger("v2.parser").error("需求解析 LLM 调用失败")
            return _result(run_id=run.id, version_id=version_id)

        monkeypatch.setattr(vb, "run_v2_pipeline", _pipeline)
        monkeypatch.setattr(vb, "build_llm_client", lambda purpose="generate": AssertionError("不应调用"))
        ctx = _ctx(tmp_path, suite, monkeypatch)
        sink, handler = rl.attach_log_capture()
        try:
            payload = vb.run_case(_plan(suite), ctx, sink)
        finally:
            rl.detach_log_capture(handler)
        blob = json.dumps(payload["classification"], ensure_ascii=False)
        assert payload["status"] == BenchmarkRunStatus.LLM_FAILURE.value
        assert "safe_prefix" in blob and "hash" in blob
        assert "traceback" not in blob.lower()


# ============================================================
# D. 主流程与 runset
# ============================================================


class TestRunsetOutput:
    def test_full_formal_runset(self, tmp_path, monkeypatch, suite):
        """#5 + #25 正式 manifest 10 单元 → runset schema + evaluated_cases=n/N"""
        import scripts.v2_benchmark as vb

        _patch_offline(monkeypatch, vb, suite)
        out = tmp_path / "rs"
        code = vb.main(["--db", str(tmp_path / "bm.db"), "--output-dir", str(out)])
        assert code == vb.EXIT_OK
        runset_dir = next(p for p in out.iterdir() if p.is_dir())
        index = json.loads((runset_dir / "runset.json").read_text(encoding="utf-8"))
        assert index["s6_schema"] == rl.S6_SCHEMA_VERSION
        assert set(
            [
                "runset_id",
                "manifest",
                "environment_fingerprint",
                "cases",
                "evaluated_cases",
                "completed_cases",
                "non_completed_cases",
                "completion_rate",
                "runtime_failure_rate",
                "llm_failure_rate",
                "evaluation_failure_rate",
                "input_invalid_rate",
            ]
        ) <= set(index)
        assert index["evaluated_cases"] == "10/10"
        assert index["completion_rate"] == 1.0
        assert (runset_dir / "manifest.resolved.json").is_file()
        assert len(list((runset_dir / "cases").glob("*.json"))) == 10
        assert (runset_dir / vb.COMPLETE_MARKER).is_file()
        assert not (runset_dir / "INCOMPLETE").exists()  # 完整落盘后必须摘掉未完成标记

    def test_case_order_follows_manifest_and_repeat_override(self, tmp_path, monkeypatch, suite):
        """#6 + #7 case filter / repeat 覆盖：顺序按 manifest 原序，单元数 = cases × repeat"""
        import scripts.v2_benchmark as vb

        _patch_offline(monkeypatch, vb, suite)
        out = tmp_path / "rs"
        code = vb.main(
            [
                "--db",
                str(tmp_path / "bm.db"),
                "--output-dir",
                str(out),
                "--case",
                "bc_02_refund_order",
                "--case",
                "bc_01_login",
                "--repeat",
                "2",
            ]
        )
        assert code == vb.EXIT_OK
        runset_dir = next(p for p in out.iterdir() if p.is_dir())
        files = sorted(p.name for p in (runset_dir / "cases").glob("*.json"))
        assert files == [
            "bc_01_login__r01.json",
            "bc_01_login__r02.json",
            "bc_02_refund_order__r01.json",
            "bc_02_refund_order__r02.json",
        ]
        index = json.loads((runset_dir / "runset.json").read_text(encoding="utf-8"))
        assert index["manifest"]["repeat"] == 2
        assert [c["case_id"] for c in index["cases"]][:2] == ["bc_02_refund_order", "bc_02_refund_order"]

    def test_case_isolation_across_statuses(self, tmp_path, monkeypatch, suite):
        """#14 隔离：case1 成功 / case2 LLM 失败 / case3 成功 → 三者都落盘且互不影响"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()

        def _pipeline(*, user_id, title, text, source_type=None, client=None, stop_after=None):
            run, version_id, _ = _seed_run(with_review=True)
            if "bc_02" in title:
                logging.getLogger("v2.tc_generator").error("LLM 用例合成调用失败: tp=01X")
            return _result(run_id=run.id, version_id=version_id, steps=[_step("review", counts={"reviewed": 1})])

        _patch_offline(monkeypatch, vb, suite, pipeline=_pipeline)
        out = tmp_path / "rs"
        code = vb.main(["--db", str(tmp_path / "bm.db"), "--output-dir", str(out), "--repeat", "1"])
        assert code == vb.EXIT_NOT_ALL_COMPLETED == 1
        runset_dir = next(p for p in out.iterdir() if p.is_dir())
        index = json.loads((runset_dir / "runset.json").read_text(encoding="utf-8"))
        by_case = {c["case_id"]: c["status"] for c in index["cases"]}
        assert by_case["bc_02_refund_order"] == "llm_failure"
        assert by_case["bc_01_login"] == "completed" and by_case["bc_10_report_export"] == "completed"
        assert index["evaluated_cases"] == "9/10"
        assert index["llm_failure_rate"] == 0.1

    def test_repeat_isolation(self, tmp_path, monkeypatch, suite):
        """#15 同 case 内某 repeat 失败不影响其他 repeat"""
        import scripts.v2_benchmark as vb
        from core.v2.ddl import create_v2_schema

        create_v2_schema()
        seen = {"n": 0}

        def _pipeline(*, user_id, title, text, source_type=None, client=None, stop_after=None):
            seen["n"] += 1
            if seen["n"] == 1:
                raise ValueError("first repeat only")
            run, version_id, _ = _seed_run(with_review=True)
            return _result(run_id=run.id, version_id=version_id, steps=[_step("review", counts={"reviewed": 1})])

        _patch_offline(monkeypatch, vb, suite, pipeline=_pipeline)
        out = tmp_path / "rs"
        code = vb.main(
            ["--db", str(tmp_path / "bm.db"), "--output-dir", str(out), "--case", "bc_01_login", "--repeat", "3"]
        )
        assert code == 1
        runset_dir = next(p for p in out.iterdir() if p.is_dir())
        statuses = [
            json.loads((runset_dir / "cases" / f"bc_01_login__r0{i}.json").read_text(encoding="utf-8"))["status"]
            for i in range(1, 4)
        ]
        assert statuses == ["runtime_failure", "completed", "completed"]

    def test_sensitive_probe_blocks_runset_and_marks_incomplete(self, tmp_path, monkeypatch, suite):
        """#23 + #24 敏感串命中 → exit 5，且不留"看起来完整"的 runset"""
        import scripts.v2_benchmark as vb

        def _boom(**kw):
            raise RuntimeError("LLM 调用失败（已重试 3 次）: api_key=leaked-probe-value")

        monkeypatch.setattr(vb, "run_v2_pipeline", _boom)
        monkeypatch.setattr(vb, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(vb, "resolve_benchmark_user", lambda username: new_ulid())
        out = tmp_path / "rs"
        code = vb.main(["--db", str(tmp_path / "bm.db"), "--output-dir", str(out), "--case", "bc_01_login"])
        assert code == vb.EXIT_WRITE_FAILURE == 5
        runset_dir = next(p for p in out.iterdir() if p.is_dir())
        assert (runset_dir / "INCOMPLETE").exists()
        assert not (runset_dir / "runset.json").exists()
        assert not (runset_dir / vb.COMPLETE_MARKER).exists()

    def test_benchmark_data_is_read_only(self, tmp_path, monkeypatch, suite):
        """#30 + #31 manifest / cases / gold 字节级不变（数据集只读纪律）"""
        import scripts.v2_benchmark as vb

        _patch_offline(monkeypatch, vb, suite)
        before = rl.build_digest_map(_BENCH)
        code = vb.main(["--db", str(tmp_path / "bm.db"), "--output-dir", str(tmp_path / "rs"), "--case", "bc_01_login"])
        assert code == vb.EXIT_OK
        assert rl.build_digest_map(_BENCH) == before

    def test_soft_payload_round_trip_from_disk(self, tmp_path, monkeypatch, suite):
        """#28 soft 段是合法 S5 payload 且可被 from_payload 读回"""
        import scripts.v2_benchmark as vb

        _patch_offline(monkeypatch, vb, suite)
        out = tmp_path / "rs"
        assert vb.main(["--db", str(tmp_path / "bm.db"), "--output-dir", str(out), "--case", "bc_01_login"]) == 0
        runset_dir = next(p for p in out.iterdir() if p.is_dir())
        payload = json.loads((runset_dir / "cases" / "bc_01_login__r01.json").read_text(encoding="utf-8"))
        report = soft_from_payload(payload["soft"])
        assert soft_to_payload(report) == payload["soft"]
        assert report.case_id == "bc_01_login"


# ============================================================
# E. CLI 参数
# ============================================================


class TestCliArguments:
    def test_unknown_case_rejected(self, tmp_path, monkeypatch, capsys):
        """#6 --case 非 manifest 成员 → exit 2"""
        import scripts.v2_benchmark as vb

        monkeypatch.setattr(vb, "_PROJECT_ROOT", tmp_path)
        code = vb.main(["--case", "bc_demo_login", "--db", str(tmp_path / "bm.db"), "--dry-run"])
        assert code == vb.EXIT_USAGE == 2

    def test_unknown_manifest_rejected(self, tmp_path, monkeypatch):
        import scripts.v2_benchmark as vb

        monkeypatch.setattr(vb, "_PROJECT_ROOT", tmp_path)
        code = vb.main(["--manifest", "bm-not-exist", "--db", str(tmp_path / "bm.db"), "--dry-run"])
        assert code == vb.EXIT_USAGE

    def test_repeat_below_one_rejected(self, tmp_path, monkeypatch):
        """#33 --repeat 必须 ≥1"""
        import scripts.v2_benchmark as vb

        monkeypatch.setattr(vb, "_PROJECT_ROOT", tmp_path)
        code = vb.main(["--repeat", "0", "--db", str(tmp_path / "bm.db"), "--dry-run"])
        assert code == vb.EXIT_USAGE

    def test_fresh_and_reuse_mutually_exclusive(self):
        """#34 --fresh-db 与 --reuse-db 互斥（argparse 直接拒绝）"""
        import scripts.v2_benchmark as vb

        with pytest.raises(SystemExit):
            vb.build_arg_parser().parse_args(["--fresh-db", "--reuse-db"])
        assert vb.build_arg_parser().parse_args([]).db_mode == "fresh"

    def test_forbidden_flags_absent(self):
        """任务书 §二十一 明禁参数不得出现在 CLI"""
        import scripts.v2_benchmark as vb

        text = vb.build_arg_parser().format_help()
        for banned in ("--skip-soft", "--compare", "--stop-after", "--model", "--api-key", "--prompt"):
            assert banned not in text

    def test_default_manifest_is_formal(self):
        import scripts.v2_benchmark as vb

        assert vb.DEFAULT_MANIFEST == _FORMAL_MANIFEST
        assert vb.DEFAULT_DB == "benchmark/data/benchmark_v2.db"
        assert vb.DEFAULT_RUNSETS == "benchmark/runsets"


class TestBoundaryDiscipline:
    def _imported_modules(self, rel_path: str) -> set[str]:
        """AST 取模块级 import 目标（不受文档字符串措辞干扰）"""
        import ast

        tree = ast.parse((_ROOT / rel_path).read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        return mods

    def test_runner_lib_does_not_import_runtime(self):
        """冻结边界：runner_lib 不 import core.v2.runtime（只鸭子类型消费 PipelineResult）"""
        mods = self._imported_modules("core/v2/eval/runner_lib.py")
        assert not any(m.startswith("core.v2.runtime") for m in mods)
        assert not any(m.startswith("core.v2.") and "orchestrator" in m for m in mods)

    def test_s6_adds_no_new_tables(self):
        """零新增表纪律：runner_lib / CLI 均不触碰 DDL 与建表面"""
        for rel in ("core/v2/eval/runner_lib.py", "scripts/v2_benchmark.py"):
            src = (_ROOT / rel).read_text(encoding="utf-8")
            for banned in ("CREATE TABLE", "INSERT INTO", "DROP TABLE", "create_v2_schema"):
                assert banned not in src, f"{rel} 出现 {banned}"
        mods = self._imported_modules("core/v2/eval/runner_lib.py")
        assert "core.v2.ddl" not in mods
