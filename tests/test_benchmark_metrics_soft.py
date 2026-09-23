"""Step 11 S5：语义评价层单测（零 LLM / 零 DB —— 全部经 StubClient 注入 canned JSON）。

覆盖 S5 任务书 §19 的 25 项：三态判定、uncertain/低置信/证据不足降级、无 trace 与
candidate/ambiguous 隔离、forbidden_suspect 独立性与非法引用丢弃、additional candidate
三类池与终态不变、批次 20/20/1 与超长独占与 payload 拆分、批失败隔离、JSON 异常、
client=None、provenance、双轨透传与互不干扰、JSON round-trip、正式 benchmark 接线。
"""

import json
from pathlib import Path

import pytest

from core.schemas import (
    RequirementItem,
    RequirementItemType,
    ReviewReport,
    ReviewScores,
    ReviewTriggerType,
    TestCase,
    TestCaseType,
    TestPoint,
    TestStep,
    new_ulid,
)
from core.v2.eval.metrics_hard import EvalArtifacts, RunObservation, evaluate_hard_metrics
from core.v2.eval.metrics_soft import (
    BATCH_SIZE,
    SemanticVerdict,
    additional_pool_refs,
    evaluate_soft_metrics,
    from_payload,
    to_payload,
)
from core.v2.eval.schema import (
    BenchmarkGold,
    BenchmarkRunStatus,
    MetricProvenance,
    load_benchmark_suite,
)
from core.v2.eval.semantic_prompts import SEMANTIC_EVAL_PROMPT, SEMANTIC_EVAL_PROMPT_VERSION
from core.v2.fingerprint import compute_item_identity_fingerprint

_BENCH = Path(__file__).resolve().parents[1] / "benchmark"
_V = new_ulid()
_R = new_ulid()
_STMT = "手机号必须为 11 位数字"


class StubClient:
    """记录调用的假 LLMClient；responder 返回 str 或抛出 Exception。"""

    def __init__(self, responder):
        self.responder = responder
        self.prompts: list[tuple[str, str]] = []

    def chat(self, system_prompt, user_prompt, images=None, max_tokens=None) -> str:
        self.prompts.append((system_prompt, user_prompt))
        out = self.responder(len(self.prompts), system_prompt, user_prompt)
        if isinstance(out, Exception):
            raise out
        return out


def _gold(*, scenarios=None, forbidden=None) -> BenchmarkGold:
    return BenchmarkGold.model_validate(
        {
            "gold_version": "gold-t1",
            "case_id": "bc_t1",
            "authoring_note": "t",
            "gold_authoring": "human_independent",
            "gold_requirements": [
                {"gold_id": "gr-1", "module": "登录", "type": "data_field", "statement": _STMT},
                {"gold_id": "gr-2", "module": "登录", "type": "function", "statement": "验证码正确登录成功"},
            ],
            "critical_scenarios": scenarios or [],
            "obligations_expected": [],
            "strategy_expectations": [],
            "reference_cases": [],
            "acceptable_variants": [],
            "forbidden_patterns": forbidden
            if forbidden is not None
            else [{"pattern_id": "fp-bad", "description": "任意长度手机号登录成功", "severity_note": "冲突"}],
        }
    )


def _item(stmt: str, type_: RequirementItemType = RequirementItemType.DATA_FIELD) -> RequirementItem:
    return RequirementItem(
        version_id=_V,
        seq=1,
        type=type_,
        module="登录",
        statement=stmt,
        fingerprint=compute_item_identity_fingerprint(module="登录", type=type_.value, statement=stmt),
    )


def _setup(gold: BenchmarkGold, n_cases: int = 1, *, extra_items=(), extra_tps=(), review_report=None):
    """构造 items（与 gr-1 data_field / gr-2 function 精确对齐 AUTO_HIT）+ n 条可 trace TC → hard 报告。"""
    it1 = _item(_STMT)
    it2 = _item("验证码正确登录成功", RequirementItemType.FUNCTION)
    items = [it1, it2] + list(extra_items)
    tps, tcs = [], []
    tp1 = TestPoint(
        item_ids=[it1.id], module="登录", subcategory="s", title="tp1", description="d", dimension="functional"
    )
    tps.append(tp1)
    for i in range(n_cases):
        tc = TestCase(
            run_id=_R,
            display_id=f"TC_{i + 1:03d}",
            test_point_ids=[tp1.id],
            module="登录",
            title=f"tc{i + 1}",
            steps=[TestStep(seq=1, action="输入手机号")],
            expected="提示格式错误",
            type=TestCaseType.FUNCTIONAL,
        )
        tcs.append(tc)
    for tp in extra_tps:
        tps.append(tp)
    arts = EvalArtifacts(items=items, test_points=tps, test_cases=tcs, obligations=[], review_report=review_report)
    obs = RunObservation(case_id="bc_t1", gold=gold, status=BenchmarkRunStatus.COMPLETED, run_id=_R, artifacts=arts)
    hard = evaluate_hard_metrics(obs)
    return obs, hard


def _resp(results_fn, additions=None):
    def responder(n, system, user):
        payload = json.loads(user)
        results = [results_fn(t) for t in payload.get("testcases", [])]
        out = {"results": results}
        if additions is not None:
            out["additional_valid_candidates"] = additions(payload) if callable(additions) else additions
        return json.dumps(out)

    return responder


def _correct(t):
    return {
        "display_id": t["display_id"],
        "verdict": "correct",
        "confidence": 0.9,
        "reason": "ok",
        "evidence_refs": [t["trace"]["gold_ids"][0]],
        "forbidden_pattern_id": None,
    }


class TestVerdicts:
    def test_correct_counts(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        assert rep.semantic_accuracy.value == 1.0
        assert rep.judgments[0].verdict == SemanticVerdict.CORRECT
        assert rep.judgments[0].gold_ids == ["gr-1"]
        assert rep.s5_prompt_version == SEMANTIC_EVAL_PROMPT_VERSION

    def test_partial_counts_in_denominator(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(
            obs,
            hard,
            StubClient(_resp(lambda t: {**_correct(t), "verdict": "partial", "reason": "缺边界值断言"})),
        )
        assert rep.semantic_accuracy.value == 0.0 and rep.semantic_accuracy.denominator == 1
        assert rep.judgments[0].verdict == SemanticVerdict.PARTIAL

    def test_incorrect_with_evidence(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(
            obs,
            hard,
            StubClient(
                _resp(
                    lambda t: {
                        **_correct(t),
                        "verdict": "incorrect",
                        "reason": "与 Gold 矛盾",
                        "evidence_refs": ["gr-1"],
                    }
                )
            ),
        )
        assert rep.judgments[0].verdict == SemanticVerdict.INCORRECT
        assert rep.verdict_counts["incorrect"] == 1

    def test_uncertain_maps_pending(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(lambda t: {**_correct(t), "verdict": "uncertain"})))
        j = rep.judgments[0]
        assert j.verdict == SemanticVerdict.PENDING_REVIEW and j.pending_reason == "uncertain"
        assert rep.semantic_accuracy.value is None  # 分母 0，不伪造

    def test_low_confidence_maps_pending(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(lambda t: {**_correct(t), "confidence": 0.3})))
        assert rep.judgments[0].pending_reason == "low_confidence"

    def test_incorrect_without_evidence_demoted(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(
            obs,
            hard,
            StubClient(_resp(lambda t: {**_correct(t), "verdict": "incorrect", "evidence_refs": []})),
        )
        assert rep.judgments[0].verdict == SemanticVerdict.PENDING_REVIEW
        assert rep.judgments[0].pending_reason == "insufficient_evidence"

    def test_illegal_evidence_dropped(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(
            obs,
            hard,
            StubClient(
                _resp(lambda t: {**_correct(t), "verdict": "incorrect", "evidence_refs": ["gr-nope", "TC_999"]})
            ),
        )
        assert rep.judgments[0].pending_reason == "insufficient_evidence"
        assert any("evidence" in f for f in rep.failures)

    def test_out_of_range_verdict_dropped_as_missing(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(lambda t: {**_correct(t), "verdict": "awful"})))
        assert rep.judgments[0].pending_reason == "missing_result"
        assert any("verdict" in f for f in rep.failures)


class TestTraceIsolation:
    def test_no_reliable_trace_not_sent_to_llm(self):
        g = _gold()
        stray_tp = TestPoint(
            item_ids=[], module="登录", subcategory="s", title="tp-stray", description="d", dimension="functional"
        )
        stray_tc = TestCase(
            run_id=_R,
            display_id="TC_900",
            test_point_ids=[stray_tp.id],
            module="登录",
            title="无trace用例",
            steps=[TestStep(seq=1, action="x")],
            expected="y",
            type=TestCaseType.FUNCTIONAL,
        )
        obs, hard = _setup(g, 1, extra_tps=[stray_tp])
        obs.artifacts.test_cases.append(stray_tc)
        hard = evaluate_hard_metrics(obs)
        client = StubClient(_resp(_correct))
        rep = evaluate_soft_metrics(obs, hard, client)
        stray = next(j for j in rep.judgments if j.display_id == "TC_900")
        assert stray.verdict == SemanticVerdict.PENDING_REVIEW
        assert stray.pending_reason == "no_reliable_trace"
        assert stray.provenance == MetricProvenance.CODE
        # 送 LLM 的批 payload 不含该用例
        sent = json.loads(client.prompts[0][1])
        assert "TC_900" not in {t["display_id"] for t in sent["testcases"]}

    def test_candidate_or_ambiguous_trace_pending(self):
        from core.v2.eval.schema import GoldMatchKeys

        g2 = _gold()
        g2.gold_requirements[0].match_keys = GoldMatchKeys(identity_fingerprints=["ri_" + "0" * 32])
        obs, hard = _setup(g2, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        j = rep.judgments[0]
        assert j.verdict == SemanticVerdict.PENDING_REVIEW
        assert j.pending_reason == "candidate_or_ambiguous_trace"
        assert any(p.kind == "candidate_or_ambiguous_trace" for p in rep.pending_review)
        assert all(b.kind == "pending_batch" for b in rep.batches)  # 无任何 TC 批：CANDIDATE 不计分不送评

    def test_invalid_tc_skipped_from_semantic(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        obs.artifacts.test_cases[0].validation_errors = ["boom"]
        hard = evaluate_hard_metrics(obs)
        assert hard.invalid  # S3 已判 INVALID
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        assert rep.judgments == [] and rep.llm_calls == 0


class TestForbiddenAndAdditions:
    def test_forbidden_suspect_independent_of_verdict(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(
            obs,
            hard,
            StubClient(_resp(lambda t: {**_correct(t), "forbidden_pattern_id": "fp-bad"})),
        )
        assert rep.judgments[0].verdict == SemanticVerdict.CORRECT  # suspect 不改 verdict
        assert rep.judgments[0].forbidden_pattern_id == "fp-bad"
        assert rep.forbidden_suspects[0].pattern_id == "fp-bad"
        assert any(p.kind == "forbidden_suspect" for p in rep.pending_review)

    def test_illegal_pattern_id_dropped(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(
            obs,
            hard,
            StubClient(_resp(lambda t: {**_correct(t), "forbidden_pattern_id": "fp-nope"})),
        )
        assert rep.forbidden_suspects == []
        assert rep.judgments[0].forbidden_pattern_id is None
        assert rep.judgments[0].verdict == SemanticVerdict.CORRECT
        assert any("forbidden_pattern_id" in f for f in rep.failures)

    def _with_pending_pool(self):
        g = _gold()
        stray_item = _item("导出需支持 xlsx 与 csv 双格式")
        stray_tp = TestPoint(
            item_ids=[stray_item.id],
            module="登录",
            subcategory="s",
            title="tp-x",
            description="d",
            dimension="functional",
        )
        obs, hard = _setup(g, 1, extra_items=[stray_item], extra_tps=[stray_tp])
        return g, obs, hard

    def test_unsupported_item_candidate(self):
        g, obs, hard = self._with_pending_pool()
        pool = additional_pool_refs(hard)

        def additions(payload):
            return [
                {
                    "target_type": "item",
                    "ref_id": pool["item"],
                    "display_id": None,
                    "confidence": 0.8,
                    "reason": "合理新增",
                    "evidence_refs": [],
                }
            ]

        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct, additions=additions)))
        cand = rep.additional_candidates[0]
        assert cand.status == "suggested" and cand.target_type == "item"
        assert cand.provenance == MetricProvenance.LLM

    def test_unverified_output_candidate(self):
        g, obs, hard = self._with_pending_pool()
        pool = additional_pool_refs(hard)

        def additions(payload):
            return [
                {
                    "target_type": "testpoint",
                    "ref_id": pool["testpoint"],
                    "confidence": 0.7,
                    "reason": "独立联动场景",
                    "evidence_refs": ["gr-1"],
                }
            ]

        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct, additions=additions)))
        assert any(c.target_type == "testpoint" for c in rep.additional_candidates)

    def test_addition_ref_outside_pool_dropped(self):
        g, obs, hard = self._with_pending_pool()
        rep = evaluate_soft_metrics(
            obs,
            hard,
            StubClient(
                _resp(
                    _correct,
                    additions=[{"target_type": "testpoint", "ref_id": "01FAKE", "reason": "x", "confidence": 0.9}],
                )
            ),
        )
        assert rep.additional_candidates == []
        pending_batches = [b for b in rep.batches if b.kind == "pending_batch"]
        assert pending_batches and any("丢弃非法 additional_candidate" in f for f in rep.failures)

    def test_pending_batch_only_additions(self):
        g, obs, hard = self._with_pending_pool()
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        kinds = [b.kind for b in rep.batches]
        assert "pending_batch" in kinds


class TestBatching:
    def test_batch_20_20_1(self):
        g = _gold()
        obs, hard = _setup(g, 41)
        client = StubClient(_resp(_correct))
        rep = evaluate_soft_metrics(obs, hard, client)
        tc_batches = [b for b in rep.batches if b.kind == "tc_batch"]
        assert [b.n_cases for b in tc_batches] == [20, 20, 1]
        assert rep.llm_calls == 3
        assert rep.semantic_accuracy.value == 1.0

    def test_huge_tc_gets_own_batch(self):
        g = _gold()
        obs, hard = _setup(g, 2)
        obs.artifacts.test_cases[1].expected = "长" * 7000
        client = StubClient(_resp(_correct))
        rep = evaluate_soft_metrics(obs, hard, client)
        big = [b for b in rep.batches if b.kind == "tc_batch" and b.refs == ["TC_002"]]
        assert len(big) == 1 and big[0].n_cases == 1  # 超长 TC 独占一批
        assert rep.semantic_accuracy.value == 1.0

    def test_payload_split_deterministic(self):
        g = _gold()
        obs, hard = _setup(g, 20)
        for tc in obs.artifacts.test_cases:
            tc.steps[0].action = "输入手机号并核对" * 150  # 每条 ~2.7k 字符，整批超 24k
        client = StubClient(_resp(_correct))
        rep = evaluate_soft_metrics(obs, hard, client)
        tc_batches = [b for b in rep.batches if b.kind == "tc_batch"]
        assert len(tc_batches) >= 2
        total = sum(b.n_cases for b in tc_batches)
        assert total == 20
        # 确定性：重放产生完全相同批次
        rep2 = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        assert [b.refs for b in rep2.batches if b.kind == "tc_batch"] == [b.refs for b in tc_batches]
        for b in tc_batches:
            assert b.n_cases < BATCH_SIZE  # 拆分生效

    def test_same_input_same_batches(self):
        g = _gold()
        obs, hard = _setup(g, 25)
        r1 = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        r2 = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        assert [b.refs for b in r1.batches] == [b.refs for b in r2.batches]

    def test_batch_failure_isolated(self):
        g = _gold()
        obs, hard = _setup(g, 41)

        def responder(n, system, user):
            if n == 2:
                return RuntimeError("网关 500")
            return _resp(_correct)(n, system, user)

        rep = evaluate_soft_metrics(obs, hard, StubClient(responder))
        tc_batches = [b for b in rep.batches if b.kind == "tc_batch"]
        assert tc_batches[1].ok is False and "RuntimeError" in tc_batches[1].error
        assert tc_batches[0].ok and tc_batches[2].ok
        pend = [j for j in rep.judgments if j.pending_reason == "batch_failure"]
        assert len(pend) == 20
        assert rep.semantic_accuracy.denominator == 21  # 41 - 20
        assert rep.semantic_accuracy.value == 1.0

    def test_json_parse_failure_degrades_batch(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(lambda n, s, u: "这不是JSON{{{"))
        j = rep.judgments[0]
        assert j.verdict == SemanticVerdict.PENDING_REVIEW and j.pending_reason == "batch_failure"
        assert rep.semantic_accuracy.value is None

    def test_system_prompt_used(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        client = StubClient(_resp(_correct))
        evaluate_soft_metrics(obs, hard, client)
        assert client.prompts[0][0] == SEMANTIC_EVAL_PROMPT


class TestDegradeAndProvenance:
    def test_client_none_no_guessing(self):
        g = _gold()
        obs, hard = _setup(g, 3)
        rep = evaluate_soft_metrics(obs, hard, client=None)
        assert rep.llm_calls == 0 and rep.batches == []
        assert all(j.pending_reason == "no_client" for j in rep.judgments)
        assert rep.semantic_accuracy.value is None
        assert rep.semantic_accuracy.provenance == MetricProvenance.LLM  # 指标格轨道不变

    def test_provenance_partition(self):
        g = _gold()
        obs, hard = _setup(g, 2)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        assert all(j.provenance == MetricProvenance.LLM for j in rep.judgments)
        obs2, hard2 = _setup(g, 1)
        rep2 = evaluate_soft_metrics(obs2, hard2, client=None)
        assert all(j.provenance == MetricProvenance.CODE for j in rep2.judgments)


def _review_report() -> ReviewReport:
    return ReviewReport(
        run_id=_R,
        revision=1,
        trigger_type=ReviewTriggerType.INITIAL,
        scores=ReviewScores(
            coverage=90, accuracy=78, executability=88, consistency=100, missing_risk=72, duplication=76
        ),
        overall_score=84.0,
    )


class TestDualTrack:
    def test_reviewer_passthrough(self):
        g = _gold()
        obs, hard = _setup(g, 1, review_report=_review_report())
        hard = evaluate_hard_metrics(obs)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        assert rep.reviewer_based is not None
        assert rep.reviewer_based.overall_score == hard.reviewer_based.overall_score
        assert rep.reviewer_based.scores["accuracy"] == 78
        assert to_payload(rep)["reviewer_based"]["scores"]["missing_risk"] == 72

    def test_gold_based_identical_with_or_without_reviewer(self):
        g = _gold()
        obs_a, hard_a = _setup(g, 2)
        obs_b, hard_b = _setup(g, 2, review_report=_review_report())
        # 两次 setup 的 ULID 不同 → 对 gold_based 比较 display_id/verdict 结构即可
        pa = to_payload(evaluate_soft_metrics(obs_a, hard_a, StubClient(_resp(_correct))))
        pb = to_payload(evaluate_soft_metrics(obs_b, hard_b, StubClient(_resp(_correct))))
        strip = lambda p: json.dumps(p["gold_based"], sort_keys=True, default=str)  # noqa: E731
        fa = json.loads(strip(pa))
        fb = json.loads(strip(pb))
        for blk in ("judgments", "pending_review"):
            for x, y in zip(fa[blk], fb[blk], strict=True):
                for k in ("tc_id", "ref_id"):
                    x.pop(k, None)
                    y.pop(k, None)
        assert fa == fb  # reviewer 快照存在与否，不改变任何 GOLD-BASED 数字/判定

    def test_hard_report_not_mutated(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        before = json.dumps(
            {"invalid": [vars(f) for f in hard.invalid], "pending": [vars(p) for p in hard.pending_review]},
            default=str,
            ensure_ascii=False,
        )
        evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        after = json.dumps(
            {"invalid": [vars(f) for f in hard.invalid], "pending": [vars(p) for p in hard.pending_review]},
            default=str,
            ensure_ascii=False,
        )
        assert before == after


class TestSerialization:
    def test_round_trip(self):
        g = _gold()
        obs, hard = _setup(g, 3, review_report=_review_report())
        hard = evaluate_hard_metrics(obs)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        payload = to_payload(rep)
        assert payload["s5_schema"] == "benchmark-soft-v1"
        assert json.dumps(payload, ensure_ascii=False)  # JSON-safe
        rep2 = from_payload(payload)
        assert to_payload(rep2) == payload
        assert rep2 == rep

    def test_none_preserved_and_lists_sorted(self):
        g = _gold()
        obs, hard = _setup(g, 2)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        payload = to_payload(rep)
        assert payload["run_id"] is not None and "model_meta" in payload
        assert payload["model_meta"] == {}  # 无 GenerationConfig → 白名单空（不泄漏）
        displays = [j["display_id"] for j in payload["gold_based"]["judgments"]]
        assert displays == sorted(displays)
        kinds_refs = [(p["kind"], p["ref_id"]) for p in payload["gold_based"]["pending_review"]]
        assert kinds_refs == sorted(kinds_refs)

    def test_model_meta_whitelist(self):
        from core.schemas import GenerationConfig

        g = _gold()
        obs, hard = _setup(g, 1)
        obs.generation_config = GenerationConfig(
            model_provider="openai",
            model_name="m1",
            temperature=0.3,
            enable_thinking=False,
            prompt_version="p1",
            generator_version="g1",
        )
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        assert rep.model_meta == {"provider": "openai", "model": "m1", "temperature": 0.3, "enable_thinking": False}
        s = json.dumps(rep.model_meta)
        for kw in ("api_key", "authorization", "base_url", "token"):
            assert kw not in s.lower()


class TestFormalWiring:
    """§19-25：正式 bench-v0.1 至少 2 个 case 的端到端接线（数据驱动，stub 按 payload 作答）。"""

    @pytest.mark.parametrize("cid", ["bc_01_login", "bc_05_admin_rbac"])
    def test_formal_gold_soft_pipeline(self, cid):
        suite = load_benchmark_suite(_BENCH)
        gold = suite.golds[cid]
        # 理想 runtime：items 与 gr 对齐（match_keys 指纹），1 TC/gr
        items, tps, tcs = [], [], []
        for k, gr in enumerate(gold.gold_requirements, start=1):
            it = RequirementItem(
                version_id=new_ulid(),
                seq=k,
                type=gr.type,
                module=gr.module,
                statement=gr.statement,
                fingerprint=gr.match_keys.identity_fingerprints[0],
            )
            tp = TestPoint(
                item_ids=[it.id],
                module=gr.module,
                subcategory="s",
                title=f"tp-{gr.gold_id}",
                description="d",
                dimension="functional",
            )
            tc = TestCase(
                run_id=new_ulid(),
                display_id=f"TC_{k:03d}",
                test_point_ids=[tp.id],
                module=gr.module,
                title=f"tc-{gr.gold_id}",
                steps=[TestStep(seq=1, action="执行操作")],
                expected="符合 Gold 的结果",
                type=TestCaseType.FUNCTIONAL,
            )
            items.append(it)
            tps.append(tp)
            tcs.append(tc)
        arts = EvalArtifacts(items=items, test_points=tps, test_cases=tcs, obligations=[])
        obs = RunObservation(case_id=cid, gold=gold, status=BenchmarkRunStatus.COMPLETED, run_id=_R, artifacts=arts)
        hard = evaluate_hard_metrics(obs)
        rep = evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct)))
        assert rep.semantic_accuracy.value == 1.0
        assert all(j.gold_ids for j in rep.judgments)  # 每条都有代码注入的 trace
        payload = to_payload(rep)
        assert payload["gold_based"]["verdict_counts"]["correct"] == len(gold.gold_requirements)
        assert json.dumps(payload, ensure_ascii=False)


class TestBatchFailureEvidence:
    """A3：批次失败必须可归因（分型 + 最小安全证据），并与"条目待人工"可区分、不污染其他批次。"""

    @staticmethod
    def _tc(rep):
        return [b for b in rep.batches if b.kind == "tc_batch"]

    def test_transport_failure_is_typed_evidenced_and_isolated(self):
        g = _gold()
        obs, hard = _setup(g, 41)

        def responder(n, system, user):
            if n == 2:
                return RuntimeError("网关 500")
            return _resp(_correct)(n, system, user)

        rep = evaluate_soft_metrics(obs, hard, StubClient(responder))
        tc = self._tc(rep)
        assert [b.ok for b in tc] == [True, False, True]  # 不污染其他批
        bad, good = tc[1], tc[0]
        assert bad.failure_kind == "transport"
        assert bad.evidence["shape"] == "no_response" and bad.evidence["raw_len"] == 0
        assert bad.evidence["unreturned_cases"] == 20
        assert "raw_sha256_12" not in bad.evidence  # 无正文就不产摘要，避免 sha256("") 冒充线索
        assert good.ok and good.failure_kind == "" and good.evidence["shape"] == "json_with_results"
        # 批次失败与"待人工"仍可区分：整批降级用 batch_failure，且失败批 ok=False
        assert {j.pending_reason for j in rep.judgments if j.batch_index == 1} == {"batch_failure"}

    def test_illegal_json_is_protocol_failure_with_digest(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(lambda n, s, u: "这不是JSON{{{"))
        b = self._tc(rep)[0]
        assert b.ok is False and b.failure_kind == "protocol"
        assert b.evidence["shape"] == "json_unparsable" and b.evidence["raw_len"] > 0
        digest = b.evidence["raw_sha256_12"]
        assert len(digest) == 12 and all(c in "0123456789abcdef" for c in digest)

    def test_json_without_results_array_is_separable_from_unparsable(self):
        """结构合法但缺 results（bc_02/bc_04 的真实形态）必须与"完全不是 JSON"区分开"""
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(lambda n, s, u: '{"foo": 1}'))
        b = self._tc(rep)[0]
        assert b.failure_kind == "protocol" and b.evidence["shape"] == "json_without_results"

    def test_empty_body_gets_its_own_shape(self):
        """推理模型常见故障：HTTP 成功但正文为空 —— 不得与"根本没连上"混为一谈"""
        g = _gold()
        obs, hard = _setup(g, 1)
        rep = evaluate_soft_metrics(obs, hard, StubClient(lambda n, s, u: ""))
        b = self._tc(rep)[0]
        assert b.failure_kind == "protocol"  # 拿到了响应，属协议层
        assert b.evidence["shape"] == "empty_body" and b.evidence["raw_len"] == 0
        assert "raw_sha256_12" not in b.evidence

    def test_partial_batch_is_neither_clean_nor_failed(self):
        """整批 ok 但有条目被治理丢弃 → partial；且不得被误报成 batch_failure"""
        g = _gold()
        obs, hard = _setup(g, 2)

        def responder(n, system, user):
            payload = json.loads(user)
            results = [_correct(t) for t in payload["testcases"]]
            results[0]["verdict"] = "非常好"  # 越界 verdict → 该条被丢弃 → 落 missing_result
            return json.dumps({"results": results})

        rep = evaluate_soft_metrics(obs, hard, StubClient(responder))
        b = self._tc(rep)[0]
        assert b.ok is True and b.failure_kind == "partial"
        assert b.evidence["unreturned_cases"] == 1 and b.evidence["dropped_items"] >= 1
        reasons = {j.pending_reason for j in rep.judgments}
        assert "missing_result" in reasons and "batch_failure" not in reasons

    def test_evidence_survives_serialization_without_raw_response(self):
        g = _gold()
        obs, hard = _setup(g, 1)
        body = "这不是JSON 内含疑似响应正文 abcdefghijk"
        rep = evaluate_soft_metrics(obs, hard, StubClient(lambda n, s, u: body))
        p = to_payload(rep)
        assert p["batches"][0]["failure_kind"] == "protocol"
        assert p["batches"][0]["evidence"]["raw_sha256_12"]
        blob = json.dumps(p, ensure_ascii=False)
        assert body not in blob and "这不是JSON" not in blob  # 响应原文绝不落盘
        back = from_payload(p)
        assert back.batches[0].failure_kind == "protocol"
        assert back.batches[0].evidence["shape"] == "json_unparsable"

    def test_old_soft_payload_without_evidence_fields_still_loads(self):
        """A3 新增字段不得破坏历史 runset 的可读性"""
        g = _gold()
        obs, hard = _setup(g, 1)
        p = to_payload(evaluate_soft_metrics(obs, hard, StubClient(_resp(_correct))))
        for b in p["batches"]:
            b.pop("failure_kind")
            b.pop("evidence")
        back = from_payload(p)
        assert all(b.failure_kind == "" and b.evidence == {} for b in back.batches)
        assert back.semantic_accuracy.value == 1.0
