"""S4 Final QA：bench 正式数据集机械契约回归测试。

只锁定机械契约（配对/版本/正式态/指纹重算一致性/引用完整性/manifest 覆盖），
不含任何业务答案断言；Phase 2 新增 case 自动被本测试覆盖。
"""

from pathlib import Path

import pytest

from core.v2.eval.schema import GoldAuthoring, load_benchmark_suite
from core.v2.fingerprint import compute_item_identity_fingerprint

_BENCH = Path(__file__).resolve().parents[1] / "benchmark"

# 正式数据集版本（demo fixture 属 bench-v0.0-demo，不参与正式契约断言）
FORMAL_VERSION = "bench-v0.1"


@pytest.fixture(scope="module")
def suite():
    return load_benchmark_suite(_BENCH)


@pytest.fixture(scope="module")
def formal_cases(suite):
    return {cid: case for cid, case in suite.cases.items() if case.benchmark_version == FORMAL_VERSION}


@pytest.fixture(scope="module")
def formal_golds(suite, formal_cases):
    return {cid: suite.golds[cid] for cid in formal_cases}


class TestFormalDatasetMechanicalContracts:
    def test_suite_has_formal_cases(self, formal_cases):
        assert formal_cases, "bench-v0.1 正式数据集不能为空"

    def test_case_gold_pairing_bidirectional(self, suite, formal_cases, formal_golds):
        """case↔gold 1:1：每个正式 case 有配对 Gold，gold.case_id 回指一致"""
        for cid, case in formal_cases.items():
            gold = formal_golds[cid]
            assert case.gold_ref.endswith(".json")
            assert gold.case_id == cid

    def test_requirement_file_exists(self, suite):
        for cid, case in suite.cases.items():
            assert (_BENCH / "cases" / case.requirement_file).is_file(), f"{cid}: requirement 缺失"

    def test_gold_authoring_human_independent(self, formal_golds):
        for cid, gold in formal_golds.items():
            assert gold.gold_authoring == GoldAuthoring.HUMAN_INDEPENDENT, cid

    def test_benchmark_version_consistent_with_manifest(self, suite):
        for mid, manifest in suite.manifests.items():
            for cid in manifest.cases:
                assert suite.cases[cid].benchmark_version == manifest.benchmark_version, f"{mid}/{cid}"

    def test_every_formal_case_covered_by_formal_manifest(self, suite, formal_cases):
        """正式 case 必须被至少一个同版本正式 manifest 收录（防编写遗漏）"""
        covered = {cid for m in suite.manifests.values() if m.benchmark_version == FORMAL_VERSION for cid in m.cases}
        assert covered == set(formal_cases)

    def test_formal_cases_have_no_duplicate_ids_across_manifests(self, suite):
        for m in suite.manifests.values():
            assert len(m.cases) == len(set(m.cases))

    def test_fingerprints_required_and_recomputed_match(self, formal_golds):
        """正式 Gold 的每条 GoldRequirement 必须带 match_keys，且指纹 == helper 重算值（逐条全查）"""
        for cid, gold in formal_golds.items():
            for gr in gold.gold_requirements:
                assert gr.match_keys is not None, f"{cid}/{gr.gold_id}: 正式 Gold 缺 match_keys"
                recomputed = compute_item_identity_fingerprint(
                    module=gr.module, type=gr.type.value, statement=gr.statement
                )
                assert gr.match_keys.identity_fingerprints == [recomputed], f"{cid}/{gr.gold_id}: 指纹与重算不一致"

    def test_fingerprints_unique_within_each_gold(self, formal_golds):
        """同一 Gold 内 canonical identity 不得重复（同 case 内两条 Gold 项指纹相同 = 语义重复，
        matching 会判 CONFLICT 全部失效）。跨 Case 允许相同 fingerprint：matching 作用域是
        单次 run 的 runtime items，不同 Case 出现相同原子责任不必然是错误。"""
        for cid, gold in formal_golds.items():
            seen: dict[str, str] = {}
            for gr in gold.gold_requirements:
                fp = gr.match_keys.identity_fingerprints[0]
                assert fp not in seen, f"{cid}/{gr.gold_id}: 同一 Gold 内指纹碰撞（canonical identity duplicate）"
                seen[fp] = gr.gold_id

    def test_scenario_refs_valid(self, formal_golds):
        for cid, gold in formal_golds.items():
            ids = {g.gold_id for g in gold.gold_requirements}
            for sc in gold.critical_scenarios:
                assert set(sc.requirement_gold_ids) <= ids, f"{cid}/{sc.scenario_id}"

    def test_obligation_and_strategy_refs_valid(self, formal_golds):
        for cid, gold in formal_golds.items():
            ids = {g.gold_id for g in gold.gold_requirements}
            for oe in gold.obligations_expected:
                if oe.requirement_gold_id is not None:
                    assert oe.requirement_gold_id in ids, f"{cid}/obligation:{oe.target}"
                assert oe.min_covered_points >= 1
            for st in gold.strategy_expectations:
                assert st.requirement_gold_id in ids, f"{cid}/strategy:{st.feature.value}"

    def test_variant_refs_valid(self, formal_golds):
        for cid, gold in formal_golds.items():
            ids = {g.gold_id for g in gold.gold_requirements}
            for av in gold.acceptable_variants:
                assert av.for_gold_id in ids, f"{cid}/variant:{av.for_gold_id}"

    def test_id_uniqueness_within_gold(self, formal_golds):
        for cid, gold in formal_golds.items():
            for ids in (
                [g.gold_id for g in gold.gold_requirements],
                [s.scenario_id for s in gold.critical_scenarios],
                [p.pattern_id for p in gold.forbidden_patterns],
            ):
                assert len(ids) == len(set(ids)), f"{cid}: 业务键重复"
