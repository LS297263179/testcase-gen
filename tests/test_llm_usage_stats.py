"""LLM 用量对账（A1）与分类证据自证（A7）的离线单测 —— 零真实 LLM。

覆盖：
  A1  LLMClient 的 calls/attempts/successes/failures 计数（含重试与耗尽）；
  A1  runtime 阶段级用量插桩（ir / review 等此前恒为 0 的步骤）；
  A8  IR 解析问题的粗粒度类别归一（classify_parse_issue）；
  A7  classify_run_status 的 evidence_counts 与 rules_without_evidence；
      StepTiming 新字段的折算与 payload 往返（含老 payload 向后兼容）。
"""

import time
from types import SimpleNamespace

import pytest

from core.llm_client import LLMClient
from core.v2.eval import runner_lib as rl
from core.v2.eval.metrics_hard import RunObservation
from core.v2.parser import classify_parse_issue


def _client(**kw):
    return LLMClient(base_url="http://invalid.test/v1", api_key="sk-not-a-real-key", model="m", **kw)


class TestLLMClientUsageStats:
    def test_initial_snapshot_is_all_zero(self):
        assert _client().llm_stats() == {"calls": 0, "attempts": 0, "successes": 0, "failures": 0}

    def test_success_counts_one_call_one_attempt(self, monkeypatch):
        c = _client()
        monkeypatch.setattr(c, "_call", lambda *a, **k: "ok")
        assert c.chat("s", "u") == "ok"
        assert c.llm_stats() == {"calls": 1, "attempts": 1, "successes": 1, "failures": 0}

    def test_retry_then_success_counts_attempts_and_retries(self, monkeypatch):
        """429 可重试：attempts 计入两次，但只有一次 call/failure"""
        c = _client(max_retries=3)
        seq = iter([RuntimeError("Error code: 429 rate_limit"), "ok"])

        def fake_call(*a, **k):
            item = next(seq)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(c, "_call", fake_call)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        assert c.chat("s", "u") == "ok"
        st = c.llm_stats()
        assert (st["calls"], st["attempts"], st["successes"], st["failures"]) == (1, 2, 1, 0)

    def test_exhausted_retries_count_one_failure(self, monkeypatch):
        c = _client(max_retries=2)
        monkeypatch.setattr(c, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom 500")))
        monkeypatch.setattr(time, "sleep", lambda s: None)
        with pytest.raises(RuntimeError):
            c.chat("s", "u")
        st = c.llm_stats()
        assert (st["calls"], st["attempts"], st["successes"], st["failures"]) == (1, 2, 0, 1)

    def test_non_retryable_auth_error_fails_immediately(self, monkeypatch):
        c = _client(max_retries=3)
        monkeypatch.setattr(
            c, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Error code: 401 unauthorized"))
        )
        with pytest.raises(RuntimeError):
            c.chat("s", "u")
        st = c.llm_stats()
        assert (st["calls"], st["attempts"], st["successes"], st["failures"]) == (1, 1, 0, 1)

    def test_stats_is_a_snapshot_not_a_reference(self, monkeypatch):
        c = _client()
        monkeypatch.setattr(c, "_call", lambda *a, **k: "ok")
        snap = c.llm_stats()
        c.chat("s", "u")
        assert snap["calls"] == 0 and c.llm_stats()["calls"] == 1


class TestClassifyParseIssue:
    @pytest.mark.parametrize(
        "issue,expected",
        [
            ("LLM 调用失败: Error code: 403", "llm_call_failed"),
            ("无法从 LLM 响应解析出 JSON", "json_unparsable"),
            ("LLM 响应缺少 items 数组", "missing_items_array"),
            (
                "item 丢弃: Schema 校验失败: ('fields', 0, 'default') Input should be a valid string",
                "item_rejected_by_schema",
            ),
            ("未产出任何有效需求项", "no_valid_items"),
            ("", "other"),
            ("没见过的措辞", "other"),
        ],
    )
    def test_categories(self, issue, expected):
        assert classify_parse_issue(issue) == expected

    def test_category_set_is_closed_and_leaks_nothing(self):
        """类别必须是固定小集合：避免把响应原文/异常正文带进 runset 或前端计数"""
        allowed = {
            "llm_call_failed",
            "json_unparsable",
            "missing_items_array",
            "item_rejected_by_schema",
            "no_valid_items",
            "other",
        }
        samples = ["LLM 调用失败: 内含疑似敏感串 abcdef", "totally unknown text"]
        assert {classify_parse_issue(s) for s in samples} <= allowed


def _hard_payload(with_usage: bool) -> dict:
    """构造 HardMetricsReport payload；with_usage=False 模拟 S6 时代（无用量字段）的历史落盘形态。"""
    step = {"step": "ir", "success": True, "duration_ms": 10, "llm_calls": 2, "counts": {"items": 1}}
    if with_usage:
        step.update({"llm_attempts": 3, "llm_retries": 1, "llm_failures": 0})
    cell = {"value": 0.5, "numerator": 1, "denominator": 2, "provenance": "code", "detail": "d"}
    return {
        "s6_schema": "benchmark-runset-v1",
        "case_id": "bc_x",
        "run_id": "r1",
        "run_status": "completed",
        "quality_evaluated": True,
        "metrics": {n: dict(cell) for n in rl._HARD_CELLS},
        "requirement_matches": [],
        "scenario_outcomes": [],
        "obligation_outcomes": [],
        "strategy_outcomes": [],
        "missing_risk": {"total_open": 0},
        "invalid": [],
        "pending_review": [],
        "duplication_exact": [],
        "duplication_semantic": [],
        "cost_latency": {
            "total_duration_ms": 10,
            "total_llm_calls": 2,
            "per_step": [step],
            "duration_ms_per_case": 10.0,
            "llm_calls_per_case": 2.0,
            "llm_failure_steps": [],
        },
        "counts": {
            "items": 1,
            "test_points": 0,
            "test_cases": 0,
            "obligations": 0,
            "strategy_points": 0,
            "llm_points": 0,
        },
        "reviewer_based": None,
    }


class TestStepTimingAccounting:
    def _stub_steps(self):
        return [
            SimpleNamespace(
                step="ir",
                success=True,
                duration_ms=10,
                llm_calls=2,
                llm_attempts=3,
                llm_retries=1,
                llm_failures=0,
                counts={"items": 1},
            ),
            SimpleNamespace(step="strategy", success=True, duration_ms=1, counts={}),  # 老形态：无新字段
        ]

    def test_from_pipeline_result_carries_new_fields_and_defaults(self):
        from core.v2.eval.schema import BenchmarkRunStatus

        pr = SimpleNamespace(run_id="r1", steps=self._stub_steps(), total_duration_ms=11, total_llm_calls=2)
        obs = RunObservation.from_pipeline_result(pr, case_id="c", gold=None, status=BenchmarkRunStatus.COMPLETED)
        by = {s.step: s for s in obs.steps}
        assert (by["ir"].llm_calls, by["ir"].llm_attempts, by["ir"].llm_retries, by["ir"].llm_failures) == (2, 3, 1, 0)
        assert (by["strategy"].llm_calls, by["strategy"].llm_attempts) == (0, 0)  # 缺字段按 0，不抛错

    def test_hard_payload_roundtrip_preserves_usage(self):
        rep = rl.hard_from_payload(_hard_payload(with_usage=True))
        step = rep.cost_latency.per_step[0]
        assert (step.llm_calls, step.llm_attempts, step.llm_retries, step.llm_failures) == (2, 3, 1, 0)
        out = rl.hard_to_payload(rep)
        assert out["cost_latency"]["per_step"][0]["llm_attempts"] == 3
        assert rl.hard_from_payload(out).cost_latency.per_step[0].llm_retries == 1

    def test_old_payload_without_usage_fields_still_loads(self):
        """S6 落盘的历史 runset 没有新字段 → 必须仍可读，按 0 处理（不伪造、不报错）"""
        payload = _hard_payload(with_usage=False)
        rep = rl.hard_from_payload(payload)
        step = rep.cost_latency.per_step[0]
        assert step.llm_calls == 2 and step.llm_attempts == 0 and step.llm_failures == 0


class TestClassifierEvidence:
    def test_evidence_counts_and_binding_for_llm_failure(self):
        from core.v2.eval.schema import BenchmarkRunStatus

        cap = rl.LogCapture(degrade_lines=["需求解析 LLM 调用失败", "需求解析 LLM 调用失败"], total_records=5)
        pr = SimpleNamespace(success=False, failed_step="ir", steps=[SimpleNamespace(step="ir", success=False)])
        res = rl.classify_run_status(pipeline_result=pr, capture=cap, counts={"items": 0, "test_cases": 0})
        assert res.status is BenchmarkRunStatus.LLM_FAILURE
        assert res.signals["evidence_counts"]["llm_degrade_log"] == 2
        assert res.signals["evidence_counts"]["count_items_zero"] == 1
        assert res.signals["rules_without_evidence"] == []  # 命中的每条规则都有证据支撑

    def test_completed_run_has_zero_evidence_and_no_unattributed_rules(self):
        from core.v2.eval.schema import BenchmarkRunStatus

        pr = SimpleNamespace(success=True, failed_step=None, steps=[SimpleNamespace(step="ir", success=True)])
        res = rl.classify_run_status(
            pipeline_result=pr,
            capture=rl.LogCapture(),
            counts={"items": 3, "test_cases": 2, "review_present": True},
        )
        assert res.status is BenchmarkRunStatus.COMPLETED
        assert set(res.signals["evidence_counts"].values()) == {0}
        assert res.signals["rules_without_evidence"] == []

    def test_signals_hold_no_raw_text(self):
        """证据仍是 {hash, safe_prefix} 白名单结构，A7 扩字段不得带回原文"""
        from core.v2.eval.schema import BenchmarkRunStatus

        cap = rl.LogCapture(degrade_lines=["LLM 用例合成调用失败: tp=01ABC"], total_records=1)
        pr = SimpleNamespace(success=True, failed_step=None, steps=[])
        res = rl.classify_run_status(pipeline_result=pr, capture=cap, counts={"items": 1})
        assert res.status is BenchmarkRunStatus.LLM_FAILURE
        for hit in res.signals["llm_degrade_logs"]:
            assert set(hit) <= {"hash", "safe_prefix"}
