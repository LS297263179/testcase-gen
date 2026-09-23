"""S8 Compare / Delta 回归测试（全离线 fixture，零真实 LLM、零 DB 写入）。

锁定四件事：
  1. 可比性判据生效 —— 跨模型/跨数据集必须 not_comparable，且不产出 improvement/regression；
  2. delta 语义 —— None-safe、无阈值、identity 仅诊断、pending 不折算；
  3. 脱敏投影 —— baseline/report 里不得出现自由文本与跨 run ULID；
  4. CLI 契约 —— 退出码 0/2/3/5，且 regression 永不产生非 0。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.v2.eval import compare as cp
from core.v2.eval import runner_lib as rl

_ROOT = Path(__file__).resolve().parents[1]
_TRACKED_BASELINE = _ROOT / cp.DEFAULT_BASELINE_PATH

_FP = {
    "benchmark_version": "bench-v0.1",
    "manifest_id": "bm-bench-v0-1",
    "case_set_digest": "digest-aaa",
    "gold_versions": {"bc_01_login": "gold-v0.2"},
    "case_versions": {"bc_01_login": "bench-v0.1"},
    "model_name": "model-X",
    "model_provider": "openai",
    "temperature": 0.3,
    "enable_thinking": False,
    "max_tokens": 8192,
    "generator_version": "tp-gen-v1,tc-synth-v1",
    "reviewer_version": "test-case-reviewer-v1",
    "business_prompt_versions": {"parser": "requirement-parser-v1"},
    "s5_prompt_version": "benchmark-semantic-eval-v1",
    "runner_version": "benchmark-runner-v1",
    "git_commit": "1111111",
    "git_dirty": False,
}

_FREE_TEXT = "期望输出为『验证码错误』并提示用户重新获取（TestCase 正文，绝不允许进 baseline）"


def _cell(value, num=None, den=None):
    return {
        "value": value,
        "numerator": num if num is not None else (int(value * 10) if value is not None else None),
        "denominator": den if den is not None else 10,
        "provenance": "code",
        "detail": _FREE_TEXT,  # 必须被摘要投影丢弃
    }


def _case_payload(
    case_id="bc_01_login",
    *,
    status="completed",
    req_cov=0.8,
    req_prec=0.4,
    obl_cov=1.0,
    identity=0.0,
    items=10,
    tps=50,
    tcs=40,
    archived=5,
    pending=12,
    invalid=0,
    calls=60,
    s5_calls=5,
    duration_ms=400_000,
    retries=0,
    failures=0,
    parse_issues=0,
    verdicts=("correct", "correct", "pending_review"),
    illegal_refs=2,
    soft_overall=80.0,
):
    hard_metrics = {
        "requirement_coverage": _cell(req_cov, 8, 10),
        "requirement_precision": _cell(req_prec, 4, 10),
        "obligation_coverage": _cell(obl_cov, 6, 6),
        "critical_scenario_coverage": _cell(0.5, 3, 6),
        "test_point_precision": _cell(0.5, 25, 50),
        "structural_validity": _cell(1.0, 40, 40),
        "duplication_score": _cell(1.0),
        "identity_match_rate": _cell(identity, 0, 10),
    }
    scores = {
        "accuracy": 68.0,
        "missing_risk": 62.0,
        "executability": 82.0,
        "consistency": 100.0,
        "coverage": 100.0,
        "duplication": 70.0,
    }
    return {
        "s6_schema": rl.S6_SCHEMA_VERSION,
        "case_id": case_id,
        "repeat_index": 1,
        "benchmark_version": "bench-v0.1",
        "gold_version": "gold-v0.2",
        "run_id": "01M36D202EJ54PXDABYM4M3TC9",
        "status": status,
        "classification": {
            "rules_hit": ["rule=completed"] if status == "completed" else ["rule=llm_degrade_log"],
            "signals": {"llm_degrade_logs": [_FREE_TEXT]},
            "evaluation_error": {"safe_prefix": _FREE_TEXT, "hash": "deadbeef"},
        },
        "pipeline": {"total_llm_calls": calls, "steps": []},
        "artifacts_summary": {
            "items": items,
            "obligations": 6,
            "strategy_points": 12,
            "test_points": tps,
            "test_cases": tcs,
            "archived_test_cases": archived,
            "review_present": True,
        },
        "llm_calls": {"pipeline": calls, "s5_soft": s5_calls, "total": calls + s5_calls},
        "case_fingerprint": {
            "case_id": case_id,
            "benchmark_version": "bench-v0.1",
            "gold_version": "gold-v0.2",
            "case_file_digest": "c" * 64,
            "gold_file_digest": "g" * 64,
            "requirement_file_digest": "r" * 64,
        },
        "hard": {
            "case_id": case_id,
            "run_status": status,
            "quality_evaluated": status == "completed",
            "metrics": hard_metrics,
            "via_counts": {"identity": 0, "bridge": 5, "anchor": 3, "candidate": 2, "ambiguous": 0, "miss": 1},
            "identity_diagnostics": {
                "identity_match_rate": identity,
                "auto_hit_identity_only": 0,
                "best_similarity_mean": 0.6,
                "gold_total": 10,
                "module_consistency": "0/10",
                "type_consistency": "9/10",
                "statement_verbatim": "0/10",
                "identity_miss_gold_ids": [f"ri_{i}" for i in range(10)],
                "note": _FREE_TEXT,
            },
            "anchor_scope": "all_non_archived_testcases",
            "cost_latency": {
                "total_duration_ms": duration_ms,
                "total_llm_calls": calls,
                "llm_calls_per_case": 1.05,
                "duration_ms_per_case": duration_ms / max(tcs, 1),
                "per_step": [
                    {
                        "step": "ir",
                        "success": True,
                        "duration_ms": 20_000,
                        "llm_calls": 1,
                        "llm_attempts": 1 + retries,
                        "llm_retries": retries,
                        "llm_failures": failures,
                        "counts": {
                            "items": items,
                            "parse_issues": parse_issues,
                            "parse_issue_kinds": ["item_rejected_by_schema"] if parse_issues else [],
                        },
                    },
                    {
                        "step": "testcases",
                        "success": True,
                        "duration_ms": 300_000,
                        "llm_calls": calls - 2,
                        "llm_attempts": calls - 2 + retries,
                        "llm_retries": retries,
                        "llm_failures": failures,
                        "counts": {},
                    },
                ],
            },
            "duplication_exact": [],
            "duplication_semantic": [],
            "invalid": [{"reason": _FREE_TEXT}] * invalid,
            "pending_review": [{"kind": "candidate_or_ambiguous_trace", "ref_id": "tc_x", "detail": _FREE_TEXT}]
            * pending,
            "missing_risk": {
                "total_open": 3,
                "gold_miss_ids": [],
                "obligation_open_ids": [],
                "scenario_open_ids": ["cs_a", "cs_b", "cs_c"],
                "strategy_unmet_ids": [],
            },
            "reviewer_based": {
                "overall_score": 83.0,
                "provenance": "mixed",
                "finding_count": 20,
                "obligation_coverage": 1.0,
                "report_id": "01M345BHZ4GVBDERKNZJ7MGVT6",
                "scores": scores,
            },
            "requirement_matches": [],
            "scenario_outcomes": [{"scenario_id": "cs_a", "state": "partial", "detail": _FREE_TEXT, "anchors": {}}],
            "obligation_outcomes": [],
            "strategy_outcomes": [],
        },
        "soft": {
            "s5_schema": "benchmark-soft-v1",
            "case_id": case_id,
            "gold_version": "gold-v0.2",
            "run_id": "01M36D202EJ54PXDABYM4M3TC9",
            "prompt_version": "benchmark-semantic-eval-v1",
            "llm_calls": s5_calls,
            "batches": [
                {"index": 0, "kind": "tc_batch", "ok": True, "llm_calls": 1, "n_cases": 20, "refs": []},
                {"index": 1, "kind": "tc_batch", "ok": True, "llm_calls": s5_calls - 1, "n_cases": 10, "refs": []},
            ],
            "failures": [{"batch": 0, "note": f"丢弃非法 evidence_refs=['cs-x'] {i}"} for i in range(illegal_refs)],
            "gold_based": {
                "judgments": [
                    {
                        "tc_id": f"01M{i}",
                        "display_id": f"TC_{i}",
                        "verdict": v,
                        "reason": _FREE_TEXT,
                        "gold_ids": ["ri_1"],
                    }
                    for i, v in enumerate(verdicts)
                ],
                "additional_valid_candidates": [{"tc_id": "01Mz", "reason": _FREE_TEXT}],
                "forbidden_suspects": [{"pattern_id": "fp-bypass-format", "reason": _FREE_TEXT, "confidence": 0.95}],
            },
            "reviewer_based": {
                "overall_score": soft_overall,
                "provenance": "mixed",
                "finding_count": 5,
                "obligation_coverage": 1.0,
                "scores": scores,
                "report_id": "r1",
            },
            "model_meta": {"model": "model-X", "provider": "openai", "temperature": 0.3, "enable_thinking": False},
        },
    }


def _write_runset(
    root: Path,
    *,
    runset_id="bm-x-1",
    fingerprint: dict | None = None,
    cases: dict | None = None,
    complete=True,
    planned=None,
    drop_case_file=False,
):
    """造一个最小但结构真实的 runset 目录。"""
    cases = cases or {"bc_01_login__r1": _case_payload()}
    root.mkdir(parents=True, exist_ok=True)
    (root / "cases").mkdir(exist_ok=True)
    entries = []
    for key, payload in cases.items():
        rel = f"cases/{key}.json"
        if not drop_case_file:
            (root / rel).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        entries.append(
            {
                "case_id": payload["case_id"],
                "repeat_index": payload["repeat_index"],
                "status": payload["status"],
                "run_id": payload.get("run_id"),
                "quality_evaluated": payload["status"] == "completed",
                "file": rel,
                "llm_calls": payload["llm_calls"]["total"],
            }
        )
    n = len(entries)
    index = {
        "s6_schema": rl.S6_SCHEMA_VERSION,
        "runset_id": runset_id,
        "runner_version": "benchmark-runner-v1",
        "created_at": "2026-09-23T00:00:00+00:00",
        "manifest": {"manifest_id": "bm-bench-v0-1", "repeat": 1, "cases": [e["case_id"] for e in entries]},
        "environment_fingerprint": fingerprint or {**_FP, "runner_version": "benchmark-runner-v1"},
        "planned_units": planned if planned is not None else n,
        "written_units": n,
        "status_counts": {},
        "evaluated_cases": f"{n}/{n}",
        "completed_cases": n,
        "non_completed_cases": 0,
        "cases": entries,
    }
    (root / "runset.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (root / "manifest.resolved.json").write_text(json.dumps({"manifest": index["manifest"]}), encoding="utf-8")
    if complete:
        (root / "_COMPLETE.json").write_text(json.dumps({"runset_id": runset_id, "units": n}), encoding="utf-8")
    return root


@pytest.fixture
def baseline_runset(tmp_path):
    return cp.load_runset(_write_runset(tmp_path / "base"))


@pytest.fixture
def candidate_dir(tmp_path):
    return _write_runset(tmp_path / "cand", runset_id="bm-x-2")


class TestComparability:
    def test_same_fingerprint_is_comparable(self, tmp_path, baseline_runset):
        cand = cp.load_runset(_write_runset(tmp_path / "c2", fingerprint=dict(_FP)))
        comp = cp.check_comparability(baseline_runset, cand)
        assert comp["comparable"] is True
        assert comp["mismatches"] == []

    def test_different_model_is_not_comparable(self, tmp_path, baseline_runset):
        cand = cp.load_runset(_write_runset(tmp_path / "c3", fingerprint={**_FP, "model_name": "model-Y"}))
        rep = cp.compare_runsets(baseline_runset, cand)
        assert rep["comparability"]["comparable"] is False
        assert rep["verdict_totals"]["improvement"] == 0
        assert rep["verdict_totals"]["regression"] == 0
        assert rep["verdict_totals"]["not_comparable"] > 0

    def test_different_case_set_digest_is_not_comparable(self, tmp_path, baseline_runset):
        cand = cp.load_runset(_write_runset(tmp_path / "c4", fingerprint={**_FP, "case_set_digest": "digest-bbb"}))
        assert cp.check_comparability(baseline_runset, cand)["comparable"] is False

    def test_different_gold_versions_is_not_comparable(self, tmp_path, baseline_runset):
        cand = cp.load_runset(
            _write_runset(tmp_path / "c5", fingerprint={**_FP, "gold_versions": {"bc_01_login": "gold-v0.1"}})
        )
        assert cp.check_comparability(baseline_runset, cand)["comparable"] is False

    def test_git_commit_difference_is_recorded_but_not_blocking(self, tmp_path, baseline_runset):
        cand = cp.load_runset(_write_runset(tmp_path / "c6", fingerprint={**_FP, "git_commit": "2222222"}))
        comp = cp.check_comparability(baseline_runset, cand)
        assert comp["comparable"] is True, "代码版本正是 S8 的比较对象，不得当成不可比"
        assert [m["field"] for m in comp["mismatches"]] == ["git_commit"]
        assert comp["mismatches"][0]["blocking"] is False

    def test_case_set_mismatch_blocks(self, tmp_path, baseline_runset):
        other = _case_payload("bc_02_refund_order")
        cand = cp.load_runset(_write_runset(tmp_path / "c7", cases={"bc_02_refund_order__r1": other}))
        assert cp.check_comparability(baseline_runset, cand)["comparable"] is False


class TestDiffCell:
    def test_both_none_is_unavailable(self):
        c = cp.diff_cell(None, None, direction="higher_better")
        assert c["verdict"] == cp.VERDICT_BOTH_NULL and c["delta"] is None

    def test_one_side_null_is_flagged_not_scored(self):
        for b, v in ((None, 0.5), (0.5, None)):
            c = cp.diff_cell(b, v, direction="higher_better")
            assert c["verdict"] == cp.VERDICT_ONE_SIDE_NULL and c["delta"] is None

    def test_equal_floats_are_unchanged(self):
        c = cp.diff_cell(0.8, 0.8, direction="higher_better")
        assert c["verdict"] == cp.VERDICT_UNCHANGED and c["delta"] == 0

    def test_higher_better_direction_signs(self):
        assert cp.diff_cell(0.5, 0.8, direction="higher_better")["verdict"] == cp.VERDICT_IMPROVEMENT
        assert cp.diff_cell(0.8, 0.5, direction="higher_better")["verdict"] == cp.VERDICT_REGRESSION

    def test_lower_better_inverts(self):
        assert cp.diff_cell(3, 1, direction="lower_better")["verdict"] == cp.VERDICT_IMPROVEMENT
        assert cp.diff_cell(1, 3, direction="lower_better")["verdict"] == cp.VERDICT_REGRESSION

    def test_never_regresses_to_error_on_worse_numbers(self):
        # 门禁禁令：变差不抛、不返回状态码
        c = cp.diff_cell(1.0, 0.0, direction="higher_better")
        assert c["verdict"] == cp.VERDICT_REGRESSION and c["delta"] == -1.0

    def test_unknown_path_defaults_to_neutral(self):
        assert cp.direction_of("hard.some_future_cell.value") == "neutral"
        assert cp.diff_cell(1, 5, direction="neutral")["verdict"] == cp.VERDICT_NEUTRAL

    def test_non_numeric_cells_compared_by_equality(self):
        assert (
            cp.diff_cell("all_non_archived_testcases", "all_non_archived_testcases", direction="neutral")["verdict"]
            == cp.VERDICT_UNCHANGED
        )


class TestDirectionTable:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("hard.metrics.requirement_coverage.value", "higher_better"),
            ("hard.metrics.identity_match_rate.value", "diagnostic_only"),
            ("hard.via_counts.anchor", "diagnostic_only"),
            ("hard.missing_risk.total_open", "lower_better"),
            ("soft.verdicts.incorrect", "lower_better"),
            ("ir.parse_issues", "lower_better"),
            ("metric_means.requirement_coverage.mean", "higher_better"),
            ("metric_means.requirement_coverage.n", "neutral"),
            ("totals.llm_calls_total", "neutral"),
            ("hard.cost_latency.per_step[ir].llm_retries", "neutral"),
        ],
    )
    def test_direction_mapping(self, path, expected):
        assert cp.direction_of(path) == expected


class TestDigestSanitization:
    def test_free_text_and_ulids_dropped(self):
        d = cp.digest_case_payload(_case_payload())
        txt = json.dumps(d, ensure_ascii=False)
        assert _FREE_TEXT not in txt
        for banned in ("detail", "reason", "anchors", "note", "safe_prefix", "run_id", "report_id", "tc_id", "ref_id"):
            assert f'"{banned}"' not in txt, banned

    def test_metric_numbers_preserved(self):
        d = cp.digest_case_payload(_case_payload(req_cov=0.714, req_prec=0.2))
        m = d["hard"]["metrics"]
        assert m["requirement_coverage"]["value"] == 0.714
        assert m["requirement_coverage"]["numerator"] == 8 and m["requirement_coverage"]["denominator"] == 10
        assert m["requirement_precision"]["value"] == 0.2

    def test_counts_and_llm_reconciled(self):
        d = cp.digest_case_payload(_case_payload(calls=60, s5_calls=5, retries=2, failures=1, parse_issues=3))
        assert d["llm_calls"] == {"pipeline": 60, "s5_soft": 5, "total": 65}
        assert d["ir"]["parse_issues"] == 3 and d["ir"]["parse_issue_kinds"] == ["item_rejected_by_schema"]
        steps = {s["step"]: s for s in d["hard"]["cost_latency"]["per_step"]}
        assert steps["ir"]["llm_retries"] == 2 and steps["ir"]["llm_failures"] == 1

    def test_pending_and_verdicts_counted_not_merged(self):
        d = cp.digest_case_payload(_case_payload(pending=12, verdicts=("correct", "incorrect", "pending_review")))
        assert d["hard"]["pending_review_count"] == 12
        assert d["soft"]["verdicts"] == {"correct": 1, "incorrect": 1, "pending_review": 1}
        assert d["hard"]["invalid_count"] == 0  # pending 不折算成 invalid


class TestAggregate:
    def test_none_excluded_from_mean_denominator(self, tmp_path):
        cases = {f"bc_{i}__r1": _case_payload(f"bc_{i}") for i in range(9)}
        cases["bc_99__r1"] = _case_payload("bc_99", obl_cov=None)
        cases["bc_99__r1"]["hard"]["metrics"]["obligation_coverage"] = {
            "value": None,
            "numerator": None,
            "denominator": None,
            "provenance": "code",
        }
        agg = cp.aggregate_of({k: cp.digest_case_payload(v) for k, v in cases.items()})
        assert agg["metric_means"]["obligation_coverage"]["n"] == 9
        assert agg["metric_means"]["requirement_coverage"]["n"] == 10
        assert agg["metric_means"]["obligation_coverage"]["mean"] == 1.0

    def test_totals_sum_over_cases(self, tmp_path):
        cases = {f"bc_{i}__r1": _case_payload(f"bc_{i}", calls=60, s5_calls=5, duration_ms=400_000) for i in range(3)}
        agg = cp.aggregate_of({k: cp.digest_case_payload(v) for k, v in cases.items()})
        assert agg["totals"]["llm_calls_pipeline"] == 180
        assert agg["totals"]["llm_calls_s5"] == 15
        assert agg["totals"]["llm_calls_total"] == 195
        assert agg["totals"]["total_duration_ms"] == 1_200_000
        assert agg["completion_rate"] == 1.0

    def test_empty_cases_rejected(self):
        with pytest.raises(cp.CompareOperandError):
            cp.aggregate_of({})


class TestCompareReport:
    def test_identical_operands_yield_zero_deltas(self, tmp_path, baseline_runset):
        rep = cp.compare_runsets(
            baseline_runset, cp.load_runset(_write_runset(tmp_path / "same", fingerprint=dict(_FP)))
        )
        assert rep["comparability"]["comparable"] is True
        assert [c["delta"] for c in rep["aggregate"].values() if c["delta"]] == []
        assert rep["verdict_totals"]["improvement"] == 0 and rep["verdict_totals"]["regression"] == 0

    def test_quality_shift_labeled(self, tmp_path, baseline_runset):
        cand = cp.load_runset(
            _write_runset(
                tmp_path / "shift", fingerprint=dict(_FP), cases={"bc_01_login__r1": _case_payload(req_cov=0.9)}
            )
        )
        rep = cp.compare_runsets(baseline_runset, cand)
        cell = rep["cases"]["bc_01_login__r1"]["cells"]["hard.metrics.requirement_coverage.value"]
        assert cell["verdict"] == cp.VERDICT_IMPROVEMENT and cell["delta"] == pytest.approx(0.1)

    def test_identity_never_claimed_as_improvement(self, tmp_path, baseline_runset):
        cand = cp.load_runset(
            _write_runset(
                tmp_path / "idn", fingerprint=dict(_FP), cases={"bc_01_login__r1": _case_payload(identity=0.9)}
            )
        )
        rep = cp.compare_runsets(baseline_runset, cand)
        cell = rep["cases"]["bc_01_login__r1"]["cells"]["hard.metrics.identity_match_rate.value"]
        assert cell["diagnostic_only"] is True
        assert cell["verdict"] == cp.VERDICT_DIAGNOSTIC
        assert cell["delta"] == pytest.approx(0.9), "差值照报，但不判定为 improvement"

    def test_pending_growth_stays_neutral(self, tmp_path, baseline_runset):
        cand = cp.load_runset(
            _write_runset(
                tmp_path / "pend", fingerprint=dict(_FP), cases={"bc_01_login__r1": _case_payload(pending=999)}
            )
        )
        rep = cp.compare_runsets(baseline_runset, cand)
        cell = rep["cases"]["bc_01_login__r1"]["cells"]["hard.pending_review_count"]
        assert cell["verdict"] == cp.VERDICT_NEUTRAL and cell["delta"] == 987

    def test_report_round_trips_through_json(self, tmp_path, baseline_runset, candidate_dir):
        rep = cp.compare_runsets(baseline_runset, cp.load_runset(candidate_dir))
        out = tmp_path / "report.json"
        rl.write_json_atomic(out, rep)
        back = cp.report_from_payload(json.loads(out.read_text(encoding="utf-8")))
        assert back["s8_schema"] == cp.S8_COMPARE_SCHEMA_VERSION
        assert set(back["cases"]) == set(rep["cases"])
        assert back["comparability"]["comparable"] is True

    def test_report_schema_guard(self, tmp_path, baseline_runset, candidate_dir):
        rep = cp.compare_runsets(baseline_runset, cp.load_runset(candidate_dir))
        rep["s8_schema"] = "bogus"
        with pytest.raises(cp.CompareOperandError):
            cp.report_from_payload(rep)
        rep2 = cp.compare_runsets(baseline_runset, cp.load_runset(candidate_dir))
        del rep2["caveats"]
        with pytest.raises(cp.CompareOperandError):
            cp.report_from_payload(rep2)

    def test_caveats_present(self, tmp_path, baseline_runset, candidate_dir):
        rep = cp.compare_runsets(baseline_runset, cp.load_runset(candidate_dir))
        joined = " ".join(rep["caveats"])
        assert "identity" in joined and "阈值" in joined and "token" in joined


class TestRunsetIntegrity:
    def test_missing_complete_marker_rejected(self, tmp_path):
        root = _write_runset(tmp_path / "incomplete", complete=False)
        (root / "INCOMPLETE").write_text("x", encoding="utf-8")
        with pytest.raises(cp.CompareOperandError, match="_COMPLETE"):
            cp.load_runset(root)

    def test_planned_written_mismatch_rejected(self, tmp_path):
        with pytest.raises(cp.CompareOperandError, match="不自洽"):
            cp.load_runset(_write_runset(tmp_path / "mismatch", planned=5))

    def test_missing_case_file_rejected(self, tmp_path):
        with pytest.raises(cp.CompareOperandError, match="case 文件缺失"):
            cp.load_runset(_write_runset(tmp_path / "gone", drop_case_file=True))

    def test_missing_dir_rejected(self, tmp_path):
        with pytest.raises(cp.CompareOperandError):
            cp.load_runset(tmp_path / "nope")

    def test_resolve_operand_dispatches(self, tmp_path, candidate_dir):
        out = tmp_path / "bl.json"
        cp.emit_baseline(candidate_dir, out)
        assert cp.resolve_operand(candidate_dir)["operand_kind"] == "runset"
        assert cp.resolve_operand(out)["operand_kind"] == "baseline"

    def test_baseline_schema_guard(self, tmp_path, candidate_dir):
        out = tmp_path / "bl.json"
        cp.emit_baseline(candidate_dir, out)
        data = json.loads(out.read_text(encoding="utf-8"))
        data["s8_schema"] = "wrong"
        out.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(cp.CompareOperandError, match="s8_schema"):
            cp.load_baseline(out)


@pytest.fixture(scope="module")
def tracked_baseline():
    if not _TRACKED_BASELINE.is_file():
        pytest.skip("baseline 文件尚未提升入库")
    return cp.load_baseline(_TRACKED_BASELINE)


class TestTrackedBaseline:
    """对已提交的 baseline-v0.1.json 做机检（不依赖 gitignore 的 runset 原件）。"""

    def test_baseline_readable_and_shaped(self, tracked_baseline):
        rec = tracked_baseline
        assert rec["runset_id"] == "bm-bench-v0-1-20260923T055019Z"
        assert len(rec["cases"]) == 10
        assert all(f in rec["fingerprint"] for f in cp.COMPARABILITY_FIELDS)

    def test_baseline_aggregate_matches_sealed_numbers(self, tracked_baseline):
        t = tracked_baseline["aggregate"]["totals"]
        assert (t["llm_calls_pipeline"], t["llm_calls_s5"], t["llm_calls_total"]) == (1201, 53, 1254)
        assert (t["llm_retries"], t["llm_failures"]) == (0, 0)
        assert t["total_duration_ms"] == 5862026
        assert (t["test_cases_alive"], t["test_cases_archived"]) == (1111, 179)
        assert t["missing_risk_open"] == 8 and t["illegal_evidence_refs_dropped"] == 32
        assert tracked_baseline["aggregate"]["metric_means"]["requirement_coverage"]["mean"] == pytest.approx(0.872)
        assert tracked_baseline["aggregate"]["metric_means"]["obligation_coverage"]["n"] == 9

    def test_baseline_aggregate_is_reproducible_from_its_own_cases(self, tracked_baseline):
        again = cp.aggregate_of(tracked_baseline["cases"])
        assert again["totals"] == tracked_baseline["aggregate"]["totals"]
        assert again["metric_means"] == tracked_baseline["aggregate"]["metric_means"]

    def test_case_layer_carries_no_free_text_or_ids(self, tracked_baseline):
        cases = json.dumps(tracked_baseline["cases"], ensure_ascii=False)
        for banned in (
            "detail",
            "reason",
            "anchors",
            "note",
            "run_id",
            "report_id",
            "tc_id",
            "ref_id",
            "gold_ids",
            "judgments",
            "statements",
        ):
            assert f'"{banned}"' not in cases, banned

    def test_baseline_carries_no_credentials(self):
        raw = _TRACKED_BASELINE.read_text(encoding="utf-8").lower()
        for kw in (
            "api_key",
            "apikey",
            "authorization",
            "secret",
            "access_token",
            "bearer",
            "base_url",
            "credential",
            "sk-",
            "aliyuncs",
        ):
            assert kw not in raw, kw

    def test_identity_and_diagnostic_flags_in_baseline(self, tracked_baseline):
        for d in tracked_baseline["cases"].values():
            assert d["hard"]["metrics"]["identity_match_rate"]["value"] == 0.0


class TestCLI:
    def test_promote_then_compare_exit_zero(self, tmp_path, candidate_dir, capsys):
        from scripts import v2_benchmark_compare as cli

        bl = tmp_path / "baseline.json"
        assert cli.main(["--promote-baseline", "--baseline", str(candidate_dir), "--out", str(bl)]) == cli.EXIT_OK
        out = tmp_path / "report.json"
        assert cli.main(["--baseline", str(bl), "--candidate", str(candidate_dir), "--out", str(out)]) == cli.EXIT_OK
        assert out.is_file()
        assert "comparable = True" in capsys.readouterr().out

    def test_missing_candidate_is_usage_error(self, tmp_path, candidate_dir):
        from scripts import v2_benchmark_compare as cli

        assert cli.main(["--baseline", str(candidate_dir)]) == cli.EXIT_USAGE

    def test_promote_mode_rejects_candidate(self, tmp_path, candidate_dir):
        from scripts import v2_benchmark_compare as cli

        assert (
            cli.main(["--promote-baseline", "--baseline", str(candidate_dir), "--candidate", str(candidate_dir)])
            == cli.EXIT_USAGE
        )

    def test_missing_operand_exit_three(self, tmp_path, candidate_dir):
        from scripts import v2_benchmark_compare as cli

        assert (
            cli.main(
                [
                    "--baseline",
                    str(candidate_dir),
                    "--candidate",
                    str(tmp_path / "ghost"),
                    "--out",
                    str(tmp_path / "o.json"),
                ]
            )
            == cli.EXIT_OPERAND
        )

    def test_regression_never_changes_exit_code(self, tmp_path, baseline_runset, candidate_dir, capsys):
        from scripts import v2_benchmark_compare as cli

        worse = _write_runset(
            tmp_path / "worse",
            runset_id="bm-worse",
            fingerprint=dict(_FP),
            cases={"bc_01_login__r1": _case_payload(req_cov=0.1, obl_cov=0.1, soft_overall=10.0)},
        )
        cand = cp.load_runset(worse)
        rep = cp.compare_runsets(baseline_runset, cand)
        assert rep["verdict_totals"]["regression"] > 0
        bl = tmp_path / "bl.json"
        cp.emit_baseline(_write_runset(tmp_path / "b2", fingerprint=dict(_FP)), bl)
        assert (
            cli.main(["--baseline", str(bl), "--candidate", str(worse), "--out", str(tmp_path / "r.json")])
            == cli.EXIT_OK
        )

    def test_sensitive_failure_maps_to_exit_five(self, tmp_path, candidate_dir, monkeypatch):
        from scripts import v2_benchmark_compare as cli

        bl = tmp_path / "bl.json"
        cp.emit_baseline(candidate_dir, bl)  # 先备好操作数，再让写盘失败

        def boom(*_a, **_k):
            raise rl.SensitiveDataError("test")

        monkeypatch.setattr(rl, "write_json_atomic", boom)
        assert (
            cli.main(["--baseline", str(bl), "--candidate", str(candidate_dir), "--out", str(tmp_path / "o.json")])
            == cli.EXIT_WRITE_FAILURE
        )

    def test_no_exit_code_one_exists(self):
        from scripts import v2_benchmark_compare as cli

        assert not hasattr(cli, "EXIT_NOT_ALL_COMPLETED")
        assert {cli.EXIT_OK, cli.EXIT_USAGE, cli.EXIT_OPERAND, cli.EXIT_WRITE_FAILURE} == {0, 2, 3, 5}


class TestZeroLlmAndDb:
    def test_compare_never_calls_llm(self, tmp_path, candidate_dir, monkeypatch):
        from scripts import v2_benchmark_compare as cli

        def _forbid(*_a, **_k):
            raise AssertionError("S8 不得调用真实 LLM")

        monkeypatch.setattr("core.llm_client.LLMClient.chat", _forbid)
        monkeypatch.setattr("core.llm_client.LLMClient.chat_stream", _forbid)
        monkeypatch.setattr("core.v2.client_factory.build_llm_client", _forbid)
        bl = tmp_path / "bl.json"
        assert cli.main(["--promote-baseline", "--baseline", str(candidate_dir), "--out", str(bl)]) == cli.EXIT_OK
        assert (
            cli.main(["--baseline", str(bl), "--candidate", str(candidate_dir), "--out", str(tmp_path / "r.json")])
            == cli.EXIT_OK
        )

    def test_compare_never_opens_v2_db(self, tmp_path, candidate_dir, monkeypatch):
        import sqlite3

        from scripts import v2_benchmark_compare as cli

        def _forbid(*_a, **_k):
            raise AssertionError("S8 不得打开任何 SQLite 库")

        monkeypatch.setattr(sqlite3, "connect", _forbid)
        bl = tmp_path / "bl.json"
        assert cli.main(["--promote-baseline", "--baseline", str(candidate_dir), "--out", str(bl)]) == cli.EXIT_OK
